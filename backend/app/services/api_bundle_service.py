"""统一接口资产包服务 — parse-bundle 与 confirm 两条链路。

职责划分：

``parse_bundle``（解析并落库，不创建端点）
    1. 计算每个来源文件 SHA-256，按 (bundle, source_type) 幂等 upsert ApiDocumentSource；
    2. 解析三类来源：Excel 契约（excel_mx）、Postman Collection、Postman Environment；
    3. 用 APIAssetMatcher 做跨来源匹配与冲突检测，产出统一契约；
    4. 按 (bundle, method, normalized_path) 幂等 upsert ApiInterfaceContract，
       并写入变量、冲突、待确认项；
    5. 返回解析统计与冲突汇总（不触碰 api_endpoints）。

``confirm``（一次性导入，单事务）
    1. 校验：所有 high 级冲突必须已确认（confirmed）或豁免（waived），否则拒绝；
    2. 在**同一事务**内创建/复用 Folder、APIEndpoint，并写入请求模板、断言、依赖；
    3. 幂等键 = 项目 + method + normalized_path：已存在的端点复用并回填契约关联，
       不会重复创建；重复 confirm 返回 skipped；
    4. 任一步骤失败 → 由调用方回滚整个事务，不留下半成品。

设计约束：
- 绝不把 secret 明文落库：环境变量统一存 ``env:NAME`` 引用；
- 归一化路径规则与 ``api_asset_matcher.normalize_path`` 完全一致，保证幂等键稳定。
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.api_asset import (
    ApiAssertion,
    ApiConflict,
    ApiDependency,
    ApiDocumentBundle,
    ApiDocumentSource,
    ApiInterfaceContract,
    ApiRequestTemplate,
    ApiVariable,
)
from app.models.api_endpoint import APIEndpoint
from app.models.folder import Folder
from app.services.api_asset_matcher import (
    APIAssetMatcher,
    endpoint_key_from_path,
    normalize_path,
)
from app.services.api_spec_normalizer import APISpecNormalizer
from app.services.excel_api_parser import ExcelAPIParser
from app.services.postman_collection_parser import PostmanCollectionParser, ParsedCollection
from app.services.postman_environment_parser import (
    PostmanEnvironmentParser,
    ParsedEnvironment,
)

logger = logging.getLogger(__name__)


SOURCE_TYPES = ("excel_mx", "postman_collection", "postman_environment")

# 统一导入产生的端点分组名（Folder.name）
DEFAULT_FOLDER_NAME = "统一导入"

# 需要确认才能导入的冲突等级
BLOCKING_SEVERITIES = ("high",)

_VAR_RE = re.compile(r"\{\{(\w+)\}\}")


def normalize_import_path(url: str | None) -> str:
    """统一导入使用的路径归一化（幂等键的一部分）。

    在 ``api_asset_matcher.normalize_path`` 基础上额外做两件事：
    1. 折叠重复斜杠（如 ``/api/icdp//v1/cs/cases/page`` → ``/api/icdp/v1/cs/cases/page``）；
    2. 去掉 ``/api/api_xxx`` 这类重复前缀（Excel 与 Collection 命名差异）。

    空路径返回空串，调用方应据此跳过该契约（不参与幂等匹配）。
    """
    norm = normalize_path(url)
    if not norm:
        return ""
    norm = re.sub(r"/{2,}", "/", norm)
    norm = re.sub(r"/api_(?=[a-z])", "/", norm)
    return norm.rstrip("/") or "/"


def _utcnow_naive() -> datetime:
    """当前 UTC 时间（**naive**）。

    ``api_interface_contracts.imported_at`` 是无时区的 TIMESTAMP 列，
    asyncpg 不接受带 tzinfo 的 datetime（会报 offset-naive/aware 混用），
    因此统一用 naive UTC 写入。
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _contract_source_location(contract: Any) -> dict[str, Any] | None:
    """把契约的 ``source_refs``（list）归一化为生成器可消费的 dict。

    契约侧存的是 ``[{"source_type": "excel_mx", "name": ...}, ...]``，
    而生成器读 ``source.sheet`` / ``source.row``。这里取 Excel 来源
    （或首个来源）并把来源名映射为 sheet，保证脚本注释与用例依据可追溯。
    """
    refs = getattr(contract, "source_refs", None) or []
    if not isinstance(refs, list) or not refs:
        return None
    primary = next(
        (r for r in refs if isinstance(r, dict) and r.get("source_type") == "excel_mx"),
        None,
    ) or (refs[0] if isinstance(refs[0], dict) else None)
    if not primary:
        return None
    location: dict[str, Any] = {
        "source_type": primary.get("source_type"),
        "name": primary.get("name"),
    }
    if primary.get("source_type") == "excel_mx":
        location["sheet"] = primary.get("name")
    folder_path = primary.get("folder_path")
    if folder_path:
        location["folder_path"] = list(folder_path)
    return location


def _security_as_list(auth: Any) -> list[dict[str, Any]] | None:
    """把契约的 auth 配置归一化为 ``list[dict]``。

    ``APIEndpointResponse.security`` 是 ``list[dict] | None``，而统一契约里
    的 ``auth`` 是单个对象（如 ``{"type": "mx_md5", ...}``）。直接写入 dict
    会让端点列表接口的响应校验失败（整个列表 500），因此写入侧统一转 list。
    """
    if not auth:
        return None
    if isinstance(auth, list):
        return [a for a in auth if isinstance(a, dict)] or None
    if isinstance(auth, dict):
        return [auth]
    return [{"value": auth}]


# ---------------------------------------------------------------------------
# 结构就绪校验辅助
# ---------------------------------------------------------------------------

#: 幂等键定义（项目 + method + normalized_path）
IDEMPOTENCY_KEY_COLUMNS = ("project_id", "method", "normalized_path")


def _constraint_covers_key(columns: list[str]) -> bool:
    """判断一组列是否覆盖幂等键。

    顺序不敏感、允许大小写差异；只要同时包含三个键列即可
    （额外列如 ``folder_id`` 不影响唯一性判定的充分性）。
    """
    normalized = {c.lower() for c in columns}
    return all(col in normalized for col in IDEMPOTENCY_KEY_COLUMNS)


def _missing_idempotency_constraints(inspector: Any, table: str) -> list[str]:
    """检查 table 上是否存在覆盖幂等键的唯一约束或唯一索引。"""
    found = False

    for constraint in inspector.get_unique_constraints(table) or []:
        if _constraint_covers_key(list(constraint.get("column_names") or [])):
            found = True
            break

    if not found:
        for index in inspector.get_indexes(table) or []:
            if not index.get("unique"):
                continue
            if _constraint_covers_key(list(index.get("column_names") or [])):
                found = True
                break

    if not found:
        return [
            f"唯一约束缺失: {table}({', '.join(IDEMPOTENCY_KEY_COLUMNS)})"
            " —— 缺少它时重复导入会重复创建端点（请执行 alembic upgrade head）"
        ]
    return []


# ---------------------------------------------------------------------------
# 结果对象
# ---------------------------------------------------------------------------

@dataclass
class SourceInput:
    """一个来源文件输入。"""

    source_type: str
    file_name: str
    content: bytes


@dataclass
class ParseOutcome:
    """parse-bundle 结果。"""

    bundle_id: UUID
    status: str
    stats: dict[str, Any] = field(default_factory=dict)
    contracts: list[dict[str, Any]] = field(default_factory=list)
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    sources: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass
class ConfirmOutcome:
    """confirm 结果。"""

    bundle_id: UUID
    status: str
    imported: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)
    failed: list[dict[str, Any]] = field(default_factory=list)
    folder_ids: list[str] = field(default_factory=list)


class BundleStateError(RuntimeError):
    """bundle 状态不允许当前操作。"""


class ConflictNotConfirmedError(RuntimeError):
    """存在未确认的高等级冲突。"""

    def __init__(self, conflicts: list[dict[str, Any]]):
        self.conflicts = conflicts
        super().__init__(
            f"存在 {len(conflicts)} 个未确认的高等级冲突，确认后才能导入"
        )


class SchemaNotReadyError(RuntimeError):
    """数据库结构缺少统一导入所需的约束（迁移未应用到 head）。

    幂等键 ``项目 + method + normalized_path`` 依赖
    ``uq_endpoint_project_method_normpath`` 唯一约束；缺它时重复 confirm
    会静默重复创建端点，因此这里**硬失败**而不是降级运行。
    """

    def __init__(self, detail: str, missing: list[str] | None = None):
        self.detail = detail
        self.missing = missing or []
        super().__init__(detail)


# ---------------------------------------------------------------------------
# 服务
# ---------------------------------------------------------------------------

class ApiBundleService:
    """三文件联合导入服务。"""

    def __init__(self, db: AsyncSession):
        self.db = db

    # ------------------------------------------------------------------
    # 结构就绪校验（P0-4 迁移漂移防护）
    # ------------------------------------------------------------------

    async def ensure_schema_ready(self) -> None:
        """校验统一导入所需的库结构已就绪，否则抛 ``SchemaNotReadyError``。

        校验项（对应迁移 0019 / 0021）：
        1. ``api_interface_contracts`` 有 ``normalized_path``（parse 落库依赖）；
        2. ``api_endpoints`` 有 ``normalized_path``（迁移 0021）；
        3. ``api_endpoints`` 存在覆盖 ``(project_id, method, normalized_path)``
           的唯一约束/唯一索引 —— 幂等键的真正凭据。

        实现用 SQLAlchemy 同步 inspector 走 run_sync，不依赖 psql；
        非 SQLAlchemy 可 inspect 的场景（无法读取约束）按通过处理，避免误伤。
        """
        from sqlalchemy import inspect as sa_inspect

        def _collect(sync_session: Any) -> list[str]:
            # 注意：AsyncSession.run_sync 回调收到的是 **Session**（不是 Connection），
            # 直接 sa_inspect(Session) 会抛 NoInspectionAvailable，必须先取 bind。
            bind = sync_session.get_bind()
            inspector = sa_inspect(bind)
            tables = set(inspector.get_table_names())
            missing: list[str] = []

            for table, column in (
                ("api_interface_contracts", "normalized_path"),
                ("api_endpoints", "normalized_path"),
            ):
                if table not in tables:
                    missing.append(f"表缺失: {table}")
                    continue
                columns = {c["name"] for c in inspector.get_columns(table)}
                if column not in columns:
                    missing.append(f"列缺失: {table}.{column}")
                    continue
                if table == "api_endpoints":
                    missing.extend(_missing_idempotency_constraints(inspector, table))
            return missing

        missing = await self.db.run_sync(_collect)
        if missing:
            raise SchemaNotReadyError(
                "数据库结构未就绪，缺少统一导入所需的对象（迁移未到 head？）: "
                + "; ".join(missing)
                + "。请先执行: alembic upgrade head",
                missing=missing,
            )

    # ------------------------------------------------------------------
    # bundle 基础操作
    # ------------------------------------------------------------------

    async def create_bundle(
        self,
        project_id: UUID,
        name: str,
        system_type: str | None = None,
        created_by: str | None = None,
    ) -> ApiDocumentBundle:
        """创建资产包；同名（同项目）返回既有包，保证幂等。"""
        stmt = select(ApiDocumentBundle).where(
            ApiDocumentBundle.project_id == project_id,
            ApiDocumentBundle.name == name,
        )
        existing = (await self.db.execute(stmt)).scalar_one_or_none()
        if existing is not None:
            return existing

        bundle = ApiDocumentBundle(
            project_id=project_id,
            name=name,
            system_type=system_type,
            status="draft",
            created_by=created_by,
        )
        self.db.add(bundle)
        await self.db.flush()
        return bundle

    async def get_bundle(self, bundle_id: UUID) -> ApiDocumentBundle | None:
        stmt = select(ApiDocumentBundle).where(ApiDocumentBundle.id == bundle_id)
        return (await self.db.execute(stmt)).scalar_one_or_none()

    # ------------------------------------------------------------------
    # parse-bundle
    # ------------------------------------------------------------------

    async def parse_bundle(
        self,
        bundle: ApiDocumentBundle,
        sources: list[SourceInput],
        attachment_ids: dict[str, UUID] | None = None,
    ) -> ParseOutcome:
        """解析三文件并落库（Bundle / Source / 契约 / 变量 / 冲突）。"""
        attachment_ids = attachment_ids or {}
        outcome = ParseOutcome(bundle_id=bundle.id, status="parsed")

        by_type: dict[str, SourceInput] = {}
        for src in sources:
            if src.source_type not in SOURCE_TYPES:
                outcome.errors.append(f"不支持的来源类型: {src.source_type}")
                continue
            by_type[src.source_type] = src

        if not by_type:
            outcome.status = "failed"
            outcome.errors.append("未提供任何可识别的来源文件")
            await self._finish_parse(bundle, outcome)
            return outcome

        # ---- 1. 来源登记（幂等） ----
        for src in by_type.values():
            source = await self._upsert_source(
                bundle, src, attachment_ids.get(src.source_type)
            )
            outcome.sources.append({
                "source_type": source.source_type,
                "file_name": source.file_name,
                "file_hash": source.file_hash,
                "file_size": source.file_size,
                "parse_status": source.parse_status,
            })

        # ---- 2. 解析 ----
        excel_specs: list[dict[str, Any]] = []
        collection: ParsedCollection | None = None
        environment: ParsedEnvironment | None = None
        env_var_names: list[str] = []
        guide: dict[str, Any] = {}

        if "excel_mx" in by_type:
            try:
                excel_specs, guide = self._parse_excel(by_type["excel_mx"].content)
            except Exception as exc:  # noqa: BLE001
                outcome.errors.append(f"Excel 解析失败: {exc}")
        if "postman_collection" in by_type:
            try:
                collection = PostmanCollectionParser().parse(
                    by_type["postman_collection"].content
                )
            except Exception as exc:  # noqa: BLE001
                outcome.errors.append(f"Collection 解析失败: {exc}")
        if "postman_environment" in by_type:
            try:
                environment = PostmanEnvironmentParser().parse(
                    by_type["postman_environment"].content
                )
                env_var_names = sorted({v.key for v in environment.variables})
            except Exception as exc:  # noqa: BLE001
                outcome.errors.append(f"Environment 解析失败: {exc}")

        outcome.stats = {
            "excel_interfaces": len(excel_specs),
            "collection_requests": len(collection.requests) if collection else 0,
            "environment_variables": len(environment.variables) if environment else 0,
            "errors": list(outcome.errors),
        }

        # ---- 3. 匹配与冲突检测 ----
        match_result = APIAssetMatcher().match(
            excel_specs=excel_specs,
            collection=collection,
            environment_vars=env_var_names,
        )

        # ---- 4. 契约落库（幂等） ----
        spec_by_key = {
            f"{(s.get('http_method') or 'POST').upper()} {self._norm_for_spec(s)}": s
            for s in excel_specs
        }
        skipped_no_path = 0
        for contract_data in match_result.contracts:
            saved = await self._upsert_contract(bundle, contract_data, spec_by_key)
            if saved is None:
                skipped_no_path += 1

        # ---- 5. 变量落库（幂等） ----
        if environment is not None:
            await self._upsert_variables_from_environment(bundle, environment)

        # ---- 6. 冲突落库 ----
        await self._sync_conflicts(bundle, match_result)

        if not excel_specs and not (collection and collection.requests):
            outcome.warnings.append("未解析出任何接口，请检查文件内容与模板")
        if skipped_no_path:
            outcome.warnings.append(
                f"{skipped_no_path} 个来源请求没有可用路径（url 缺失），已跳过（无法作为端点导入）"
            )
        outcome.stats["skipped_no_path"] = skipped_no_path

        await self._finish_parse(bundle, outcome)

        outcome.contracts = [
            {
                "id": str(c.id),
                "endpoint_key": c.endpoint_key,
                "method": c.method,
                "path": c.path,
                "normalized_path": c.normalized_path,
                "name": c.name,
                "status": c.status,
                "source_types": sorted((c.request or {}).get("_source_types", []) or []),
                "pending_count": len(c.pending_confirmations or []),
            }
            for c in await self._load_contracts(bundle)
        ]
        outcome.conflicts = await self._load_conflicts(bundle)
        return outcome

    # ------------------------------------------------------------------
    # confirm
    # ------------------------------------------------------------------

    async def confirm(
        self,
        bundle: ApiDocumentBundle,
        contract_ids: list[UUID] | None = None,
        parent_folder_id: UUID | None = None,
        folder_name: str | None = None,
    ) -> ConfirmOutcome:
        """确认导入：单事务创建 Folder / APIEndpoint / 模板 / 断言 / 依赖。

        幂等键：项目 + method + normalized_path。
        任一步骤抛错时，由调用方回滚整个事务（不保留半成品）。
        """
        contracts = await self._load_contracts(bundle, contract_ids)
        if not contracts:
            raise BundleStateError("没有可导入的契约，请先执行 parse-bundle")

        # ---- 0. 结构就绪校验（幂等键依赖唯一约束，缺则拒绝导入） ----
        await self.ensure_schema_ready()

        # ---- 1. 冲突确认校验 ----
        blocking = await self._unconfirmed_blocking_conflicts(bundle)
        if blocking:
            raise ConflictNotConfirmedError(blocking)

        # ---- 2. 目标文件夹（同项目同名复用） ----
        folder = await self._get_or_create_folder(
            project_id=bundle.project_id,
            name=folder_name or bundle.name or DEFAULT_FOLDER_NAME,
            parent_id=parent_folder_id,
            description=f"由统一导入资产包「{bundle.name}」创建",
        )

        outcome = ConfirmOutcome(bundle_id=bundle.id, status="imported")
        outcome.folder_ids.append(str(folder.id))

        # ---- 2. 契约 → 端点 ----
        for contract in contracts:
            existing = await self._find_endpoint(
                bundle.project_id, contract.method, contract.normalized_path
            )
            if existing is not None:
                # 幂等：复用既有端点，仅回填关联。
                # 注意：端点复用不代表契约内容已落库 —— 断言/依赖属于契约侧数据，
                # 仍必须写入，否则「先导入端点、后补充断言」的场景会永久丢失断言。
                await self._create_assertions(contract)
                await self._create_dependencies(bundle, contract, contracts)
                contract.imported_endpoint_id = existing.id
                contract.imported_at = _utcnow_naive()
                contract.status = "imported"
                outcome.skipped.append({
                    "contract_id": str(contract.id),
                    "endpoint_id": str(existing.id),
                    "reason": "端点已存在（项目+method+normalized_path 命中），已复用并补齐断言/依赖",
                })
                continue

            endpoint = APIEndpoint(
                project_id=bundle.project_id,
                folder_id=folder.id,
                display_name=f"{contract.method} {contract.path}",
                path=contract.path,
                method=contract.method,
                normalized_path=contract.normalized_path,
                summary=contract.name,
                description=(contract.request or {}).get("scenario"),
                parameters=(contract.request or {}).get("parameters") or [],
                request_body=(contract.request or {}).get("body_definition"),
                responses=(contract.response or {}).get("body_definition"),
                security=_security_as_list(contract.auth),
                tags=[contract.endpoint_key] if contract.endpoint_key else [],
                tag_group=folder.name,
                custom_config={
                    "import_source": "unified_import",
                    "bundle_id": str(bundle.id),
                    "contract_id": str(contract.id),
                    # 契约驱动生成（P1）从 custom_config 还原统一接口模型：
                    # - auth      必须存在，否则生成器报"仅支持 mx_md5 鉴权"
                    # - endpoint_key 作为 method_name（如 api_memLogin）
                    # - source_ref  供脚本注释与追溯使用
                    "api_name": contract.name or contract.endpoint_key,
                    "method_name": contract.endpoint_key,
                    "auth": contract.auth,
                    # 生成器按 dict 消费（`(spec.get("source") or {}).get("sheet")`），
                    # 而契约侧 source_refs 是 list；直接透传会抛
                    # AttributeError: 'list' object has no attribute 'get'。
                    "source_location": _contract_source_location(contract),
                    "pending_confirmations": contract.pending_confirmations or [],
                },
            )
            self.db.add(endpoint)
            await self.db.flush()  # 取得 endpoint.id

            # ---- 请求模板 / 断言 / 依赖 ----
            await self._create_templates(contract, endpoint)
            await self._create_assertions(contract)
            await self._create_dependencies(bundle, contract, contracts)

            contract.imported_endpoint_id = endpoint.id
            contract.imported_at = _utcnow_naive()
            contract.status = "imported"

            outcome.imported.append({
                "contract_id": str(contract.id),
                "endpoint_id": str(endpoint.id),
                "method": contract.method,
                "path": contract.path,
            })

        bundle.status = "imported"
        bundle.parse_result = {
            **(bundle.parse_result or {}),
            "confirm": {
                "imported": len(outcome.imported),
                "skipped": len(outcome.skipped),
                "confirmed_at": datetime.now(timezone.utc).isoformat(),
            },
        }
        await self.db.flush()
        return outcome

    async def confirm_conflicts(
        self,
        bundle: ApiDocumentBundle,
        resolutions: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """确认/豁免冲突。resolutions: [{conflict_id, resolution: accept|waive, note}]"""
        result: list[dict[str, Any]] = []
        for item in resolutions:
            conflict_id = item.get("conflict_id")
            resolution = item.get("resolution")
            if resolution not in ("accept", "waive"):
                raise BundleStateError(f"不支持的冲突处理方式: {resolution}")

            stmt = select(ApiConflict).where(
                ApiConflict.id == conflict_id,
                ApiConflict.bundle_id == bundle.id,
            )
            conflict = (await self.db.execute(stmt)).scalar_one_or_none()
            if conflict is None:
                raise BundleStateError(f"冲突不存在: {conflict_id}")

            conflict.status = "confirmed" if resolution == "accept" else "waived"
            conflict.details = {
                **(conflict.details or {}),
                "resolution": resolution,
                "note": item.get("note"),
                "resolved_at": datetime.now(timezone.utc).isoformat(),
            }
            result.append({
                "conflict_id": str(conflict.id),
                "status": conflict.status,
                "conflict_type": conflict.conflict_type,
            })
        await self.db.flush()
        return result

    # ==================================================================
    # 解析辅助
    # ==================================================================

    def _parse_excel(self, content: bytes) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """解析美心 Excel：返回 (统一模型列表, 导读规则)。"""
        parser = ExcelAPIParser().load(content)
        guide: dict[str, Any] = {}
        guide_title: str | None = None
        for name in parser.sheet_names():
            ws = parser.workbook[name]
            if parser.is_guide_sheet(ws):
                guide_title = ws.title
                guide = parser.parse_guide(ws)
                break

        normalizer = APISpecNormalizer()
        specs: list[dict[str, Any]] = []
        for name in parser.sheet_names():
            ws = parser.workbook[name]
            if guide_title is not None and ws.title == guide_title:
                continue
            if parser.is_guide_sheet(ws):
                continue
            iface = parser.parse_interface_sheet(ws)
            spec = normalizer.to_unified_spec(iface, guide)
            if spec.get("path"):
                specs.append(spec)
        return specs, guide

    @staticmethod
    def _norm_for_spec(spec: dict[str, Any]) -> str:
        """Excel 统一模型的归一化路径（与 matcher 保持一致）。"""
        return normalize_import_path(spec.get("path"))

    # ==================================================================
    # 落库辅助
    # ==================================================================

    async def _upsert_source(
        self,
        bundle: ApiDocumentBundle,
        src: SourceInput,
        attachment_id: UUID | None,
    ) -> ApiDocumentSource:
        """按 (bundle, source_type) 幂等写入来源记录。"""
        file_hash = hashlib.sha256(src.content).hexdigest()
        stmt = select(ApiDocumentSource).where(
            ApiDocumentSource.bundle_id == bundle.id,
            ApiDocumentSource.source_type == src.source_type,
        )
        source = (await self.db.execute(stmt)).scalar_one_or_none()
        if source is None:
            source = ApiDocumentSource(bundle_id=bundle.id, source_type=src.source_type)
            self.db.add(source)

        source.file_name = src.file_name
        source.file_hash = file_hash
        source.file_size = len(src.content)
        source.attachment_id = attachment_id
        source.parse_status = "completed"
        source.parse_error = None
        await self.db.flush()
        return source

    async def _upsert_contract(
        self,
        bundle: ApiDocumentBundle,
        contract_data: dict[str, Any],
        spec_by_key: dict[str, dict[str, Any]],
    ) -> ApiInterfaceContract:
        """按 (bundle, method, normalized_path) 幂等写入契约。

        返回 ``None`` 表示该契约没有可用路径（无法构成幂等键），调用方应跳过并告警。
        """
        method = (contract_data.get("method") or "POST").upper()
        normalized = normalize_import_path(contract_data.get("normalized_path"))
        if not normalized:
            # 无路径的请求（Collection 里 url 缺失）无法构成 (method, path) 幂等键，
            # 也不能作为端点导入，直接跳过。
            return None
        key = f"{method} {normalized}"

        stmt = select(ApiInterfaceContract).where(
            ApiInterfaceContract.bundle_id == bundle.id,
            ApiInterfaceContract.method == method,
            ApiInterfaceContract.normalized_path == normalized,
        )
        contract = (await self.db.execute(stmt)).scalar_one_or_none()

        excel_c = contract_data.get("excel")
        coll_list = contract_data.get("collection") or []
        source_types = sorted(contract_data.get("sources", {}).keys())

        raw_spec = spec_by_key.get(key) or {}
        request_fields = (raw_spec.get("request") or {}).get("fields") or []
        response_fields = (raw_spec.get("response") or {}).get("fields") or []
        error_codes = (raw_spec.get("response") or {}).get("error_codes") or []
        pending = raw_spec.get("pending_confirmations") or []

        source_refs: list[dict[str, Any]] = []
        if excel_c is not None:
            source_refs.append({"source_type": "excel_mx", "name": excel_c.name})
        for cc in coll_list:
            source_refs.append({
                "source_type": "postman_collection",
                "name": cc.name,
                "folder_path": list(getattr(cc.raw, "folder_path", []) or []),
            })

        name = (excel_c.name if excel_c is not None else None) or (
            coll_list[0].name if coll_list else None
        )
        endpoint_key = (
            contract_data.get("endpoint_key")
            or endpoint_key_from_path(normalized)
        )
        path = (
            excel_c.path if excel_c is not None
            else (coll_list[0].path if coll_list else normalized)
        )

        request_payload: dict[str, Any] = {
            "fields": request_fields,
            "scenario": raw_spec.get("scenario"),
            "parameters": self._collect_parameters(request_fields),
            "body_definition": {
                "content_type": "application/json",
                "schema": {"fields": request_fields},
            },
            "_source_types": source_types,
        }
        response_payload = {
            "fields": response_fields,
            "error_codes": error_codes,
            "body_definition": {"schema": {"fields": response_fields}},
        }

        assertions_payload: list[dict[str, Any]] = []
        for cc in coll_list:
            for a in (cc.assertions or []):
                assertions_payload.append({
                    **a,
                    "source_type": "collection_script",
                    "source_location": {"request": cc.name},
                })

        if contract is None:
            contract = ApiInterfaceContract(
                bundle_id=bundle.id,
                endpoint_key=endpoint_key,
                method=method,
                path=path,
                normalized_path=normalized,
                version=1,
            )
            self.db.add(contract)
            version = 1
        else:
            version = (contract.version or 1)
            if self._contract_changed(contract, request_payload):
                version += 1

        contract.endpoint_key = endpoint_key
        contract.path = path
        contract.name = name
        contract.version = version
        contract.request = request_payload
        contract.response = response_payload
        contract.auth = raw_spec.get("auth")
        contract.assertions = assertions_payload
        contract.dependencies = self._collect_dependency_hints(coll_list)
        contract.pending_confirmations = pending
        contract.source_refs = source_refs
        # 已导入的契约保持 imported，其余按是否有待确认项置 matched/conflicted
        if contract.status != "imported":
            contract.status = "conflicted" if pending else "matched"

        await self.db.flush()
        return contract

    @staticmethod
    def _contract_changed(contract: ApiInterfaceContract, new_request: dict[str, Any]) -> bool:
        """判断契约内容是否发生实质变化（决定 version 是否 +1）。"""
        old_fields = (contract.request or {}).get("fields") or []
        new_fields = new_request.get("fields") or []
        return old_fields != new_fields

    @staticmethod
    def _collect_parameters(fields: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """把 Excel 字段映射为 APIEndpoint.parameters 结构。"""
        out: list[dict[str, Any]] = []
        for f in fields:
            if not f.get("name"):
                continue
            out.append({
                "name": f["name"],
                "in": "body",
                "required": bool(f.get("required")),
                "schema": {"type": f.get("type")},
                "description": f.get("description") or f.get("diff"),
                "length": f.get("length"),
                "example": f.get("example"),
                "level": f.get("level"),
            })
        return out

    @staticmethod
    def _collect_dependency_hints(coll_list: list[Any]) -> list[dict[str, Any]]:
        """从 Collection 的变量写入推断依赖（如 SessionKey 传递）。

        本函数此前的两个缺陷叠加，导致**依赖提示恒为空**、
        `_create_dependencies` 永远不可能落库任何依赖：

        1. 把入参当成有 ``.raw`` 的包装对象：``getattr(cc, "raw", None)`` 恒为 None，
           直接 continue。实际上契约里存的就是 ``ParsedRequest`` 本身
           （``variable_writes`` / ``name`` 是它的直接字段），没有 ``raw``。
        2. 键名不匹配：解析器返回 ``{"variable", "expression"}``，
           此处曾读 ``name`` / ``key``。

        现在直接读 ``ParsedRequest`` 的字段，并保留对旧键名的兼容。
        """
        hints: list[dict[str, Any]] = []
        for cc in coll_list:
            writes = getattr(cc, "variable_writes", None)
            if writes is None:
                # 兼容历史包装对象
                raw = getattr(cc, "raw", None)
                writes = getattr(raw, "variable_writes", None) if raw is not None else None
                request_name = getattr(raw, "name", None) if raw is not None else None
            else:
                request_name = getattr(cc, "name", None)

            for write in (writes or []):
                if not isinstance(write, dict):
                    continue
                variable = (
                    write.get("variable")
                    or write.get("name")
                    or write.get("key")
                )
                if not variable:
                    continue
                hints.append({
                    "variable": variable,
                    "extract_expr": write.get("expression") or write.get("value"),
                    "from_request": request_name,
                })
        return hints

    async def _upsert_variables_from_environment(
        self,
        bundle: ApiDocumentBundle,
        environment: ParsedEnvironment,
    ) -> None:
        """按 (bundle, name) 幂等写入环境变量（仅存 env:NAME 引用）。"""
        for var in environment.variables:
            stmt = select(ApiVariable).where(
                ApiVariable.bundle_id == bundle.id,
                ApiVariable.name == var.key,
            )
            row = (await self.db.execute(stmt)).scalar_one_or_none()
            if row is None:
                row = ApiVariable(bundle_id=bundle.id, name=var.key)
                self.db.add(row)
            row.value_ref = var.value_ref
            row.is_secret = var.is_secret
            row.environment = environment.environment
            row.source_type = "postman_environment"
            row.enabled = var.enabled
        await self.db.flush()

    async def _sync_conflicts(
        self,
        bundle: ApiDocumentBundle,
        match_result: Any,
    ) -> None:
        """重建 open 冲突集合；已确认/豁免的冲突保留不动。"""
        existing = (await self.db.execute(
            select(ApiConflict).where(ApiConflict.bundle_id == bundle.id)
        )).scalars().all()
        resolved = {
            (c.conflict_type, c.message)
            for c in existing
            if c.status in ("confirmed", "waived")
        }
        for c in existing:
            if c.status == "open":
                await self.db.delete(c)
        await self.db.flush()

        contract_rows = await self._load_contracts(bundle)
        cmap = {(c.method, c.normalized_path): c.id for c in contract_rows}

        for conflict in match_result.conflicts:
            if (conflict.conflict_type, conflict.message) in resolved:
                continue
            method, _, norm = conflict.contract_key.partition(" ")
            self.db.add(ApiConflict(
                bundle_id=bundle.id,
                contract_id=cmap.get((method, norm)),
                conflict_type=conflict.conflict_type,
                severity=conflict.severity,
                status="open",
                message=conflict.message,
                details=conflict.details or {},
                source_refs=conflict.source_refs or [],
            ))
        await self.db.flush()

    async def _finish_parse(self, bundle: ApiDocumentBundle, outcome: ParseOutcome) -> None:
        has_any = bool(
            outcome.stats.get("excel_interfaces") or outcome.stats.get("collection_requests")
        )
        bundle.parse_result = {
            "stats": outcome.stats,
            "warnings": outcome.warnings,
            "errors": outcome.errors,
        }
        bundle.status = "parsed" if has_any else "failed"
        outcome.status = bundle.status
        await self.db.flush()

    # ==================================================================
    # 查询辅助
    # ==================================================================

    async def _load_contracts(
        self,
        bundle: ApiDocumentBundle,
        contract_ids: list[UUID] | None = None,
    ) -> list[ApiInterfaceContract]:
        stmt = select(ApiInterfaceContract).where(
            ApiInterfaceContract.bundle_id == bundle.id
        )
        if contract_ids:
            stmt = stmt.where(ApiInterfaceContract.id.in_(contract_ids))
        rows = (await self.db.execute(stmt)).scalars().all()
        # 稳定顺序，便于测试与幂等复现
        return sorted(rows, key=lambda c: (c.method, c.normalized_path))

    async def _load_conflicts(self, bundle: ApiDocumentBundle) -> list[dict[str, Any]]:
        rows = (await self.db.execute(
            select(ApiConflict).where(ApiConflict.bundle_id == bundle.id)
        )).scalars().all()
        return [
            {
                "id": str(c.id),
                "contract_id": str(c.contract_id) if c.contract_id else None,
                "conflict_type": c.conflict_type,
                "severity": c.severity,
                "status": c.status,
                "message": c.message,
                "details": c.details,
            }
            for c in rows
        ]

    async def list_variables(self, bundle: ApiDocumentBundle) -> list[dict[str, Any]]:
        rows = (await self.db.execute(
            select(ApiVariable).where(ApiVariable.bundle_id == bundle.id)
        )).scalars().all()
        return [
            {
                "id": str(v.id),
                "name": v.name,
                "value_ref": v.value_ref,
                "is_secret": v.is_secret,
                "environment": v.environment,
                "source_type": v.source_type,
                "enabled": v.enabled,
            }
            for v in sorted(rows, key=lambda v: v.name)
        ]

    async def _unconfirmed_blocking_conflicts(
        self, bundle: ApiDocumentBundle
    ) -> list[dict[str, Any]]:
        rows = (await self.db.execute(
            select(ApiConflict).where(
                ApiConflict.bundle_id == bundle.id,
                ApiConflict.severity.in_(BLOCKING_SEVERITIES),
                ApiConflict.status == "open",
            )
        )).scalars().all()
        return [
            {
                "id": str(c.id),
                "conflict_type": c.conflict_type,
                "severity": c.severity,
                "message": c.message,
            }
            for c in rows
        ]

    # ==================================================================
    # confirm 辅助
    # ==================================================================

    async def _get_or_create_folder(
        self,
        project_id: UUID,
        name: str,
        parent_id: UUID | None,
        description: str | None = None,
    ) -> Folder:
        stmt = select(Folder).where(
            Folder.project_id == project_id,
            Folder.name == name,
        )
        if parent_id is not None:
            stmt = stmt.where(Folder.parent_id == parent_id)
        else:
            stmt = stmt.where(Folder.parent_id.is_(None))
        folder = (await self.db.execute(stmt)).scalars().first()
        if folder is not None:
            return folder

        folder = Folder(
            project_id=project_id,
            parent_id=parent_id,
            name=name,
            description=description,
            folder_type="api_test",
        )
        self.db.add(folder)
        await self.db.flush()
        return folder

    async def _find_endpoint(
        self,
        project_id: UUID,
        method: str,
        normalized_path: str,
    ) -> APIEndpoint | None:
        """幂等键查找：项目 + method + normalized_path。"""
        if not normalized_path:
            return None
        stmt = select(APIEndpoint).where(
            APIEndpoint.project_id == project_id,
            APIEndpoint.method == method.upper(),
            APIEndpoint.normalized_path == normalized_path,
        )
        return (await self.db.execute(stmt)).scalars().first()

    async def _create_templates(
        self,
        contract: ApiInterfaceContract,
        endpoint: APIEndpoint,
    ) -> None:
        """为契约写入请求模板（幂等：已存在则不重复创建）。"""
        existing = (await self.db.execute(
            select(ApiRequestTemplate).where(ApiRequestTemplate.contract_id == contract.id)
        )).scalars().all()
        if existing:
            return

        raw = contract.request or {}
        self.db.add(ApiRequestTemplate(
            contract_id=contract.id,
            name=f"{endpoint.method} {endpoint.path}",
            headers={"content-type": "application/json"},
            query={},
            body={"schema": {"fields": raw.get("fields") or []}},
            source_location={"origin": "unified_import", "endpoint_id": str(endpoint.id)},
        ))
        await self.db.flush()

    async def _create_assertions(self, contract: ApiInterfaceContract) -> None:
        """把契约断言落库（幂等：同 contract 同类型+表达式不重复）。"""
        existing = (await self.db.execute(
            select(ApiAssertion).where(ApiAssertion.contract_id == contract.id)
        )).scalars().all()
        seen = {(a.assert_type, a.expression) for a in existing}

        for a in (contract.assertions or []):
            assert_type = str(a.get("type") or a.get("assert_type") or "status")
            expression = str(a.get("expression") or a.get("target") or "")
            if not expression:
                continue
            if (assert_type, expression) in seen:
                continue
            self.db.add(ApiAssertion(
                contract_id=contract.id,
                source_type=str(a.get("source_type") or "collection_script"),
                assert_type=assert_type,
                expression=expression,
                expected=str(a.get("expected")) if a.get("expected") is not None else None,
                source_location=a.get("source_location") or {},
            ))
            seen.add((assert_type, expression))

        # Excel 错误码 → 断言（每张返回码一条）
        for code in ((contract.response or {}).get("error_codes") or []):
            expr = f"error_code == {code.get('code')}"
            if ("error_code", expr) in seen:
                continue
            self.db.add(ApiAssertion(
                contract_id=contract.id,
                source_type="excel_contract",
                assert_type="error_code",
                expression=expr,
                expected=str(code.get("message") or ""),
                source_location=code.get("source") or {},
            ))
            seen.add(("error_code", expr))
        await self.db.flush()

    @staticmethod
    def _resolve_dependency_target(
        variable: str,
        all_contracts: list[Any],
        exclude_contract_id: Any,
    ) -> Any | None:
        """把变量名落脚到候选契约（依赖的"目标端点"）。

        规则（保守，避免推测）：变量名应**出现在**候选契约的 ``endpoint_key`` 中
        （如变量 ``sessionKey`` ⊂ ``endpoint_key api_sessionKey``）。
        匹配不到就返回 ``None``，调用方据此**不落库**该依赖。

        历史缺陷：原地实现写成 ``key in variable``（方向相反），
        导致永远匹配不到目标、依赖从未可能落库。
        """
        if not variable:
            return None
        needle = str(variable).lower()
        for other in all_contracts:
            key = getattr(other, "endpoint_key", None)
            if not key or getattr(other, "id", None) == exclude_contract_id:
                continue
            normalized_key = str(key).replace("api_", "").lower()
            if needle in normalized_key or needle in str(key).lower():
                return other
        return None

    async def _create_dependencies(
        self,
        bundle: ApiDocumentBundle,
        contract: ApiInterfaceContract,
        all_contracts: list[ApiInterfaceContract],
    ) -> None:
        """把依赖提示解析为 contract→contract 的 ApiDependency（幂等）。

        只处理能确定落脚点的情形：Collection 的变量写入若与另一个契约的
        ``endpoint_key`` 相关，则建立依赖。无法确定的依赖仅保留在
        ``contract.dependencies`` 供人工确认，不落表。
        """
        existing = (await self.db.execute(
            select(ApiDependency).where(ApiDependency.from_contract_id == contract.id)
        )).scalars().all()
        if existing:
            return

        hints = contract.dependencies or []
        if not hints:
            return

        for hint in hints:
            target = self._resolve_dependency_target(
                hint.get("variable"), all_contracts, contract.id
            )
            if target is None:
                continue
            self.db.add(ApiDependency(
                bundle_id=bundle.id,
                from_contract_id=contract.id,
                to_contract_id=target.id,
                variable=str(hint.get("variable")),
                extract_expr=hint.get("extract_expr"),
            ))
        await self.db.flush()
