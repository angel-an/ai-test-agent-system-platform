# -*- coding: utf-8 -*-
"""P0 → P1 桥接验收：用统一导入的真实端点驱动契约生成。

存在的缝隙：统一导入创建 APIEndpoint 时若 custom_config 不含 auth，契约生成的
``_endpoint_to_spec`` 会得到 auth=None，生成器直接抛"当前脚本生成仅支持 mx_md5 鉴权"，
即**导入的端点无法被生成链路消费**。另有 source_location 形状问题：契约侧是 list，
生成器按 dict 消费，直接透传会抛 ``AttributeError: 'list' object has no attribute 'get'``。

本脚本用真实美心三文件走完 parse-bundle → confirm，然后对导入的端点调用
``generate_artifacts_from_endpoint``，证明桥接成立。

用法::

    cd backend
    ..\\.venv\\Scripts\\python.exe -X utf8 scripts\\p0_p1_bridge_check.py --project PR-8
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from app.config.settings import settings  # noqa: E402
from app.models.api_endpoint import APIEndpoint  # noqa: E402
from app.models.project import Project  # noqa: E402
from app.services.api_bundle_service import ApiBundleService, SourceInput  # noqa: E402
from app.services.api_test_service import APITestService  # noqa: E402

from tests.sample_paths import MX_SAMPLE_DIR

MX_DIR = MX_SAMPLE_DIR
EXCEL = MX_DIR / "YX CRM API Spec For Non POS (1).xlsx"
COLLECTION = MX_DIR / "美心.postman_collection.json"
ENVIRONMENT = MX_DIR / "UAT_eatizen HK CPO.postman_environment.json"

EXIT_OK, EXIT_FAIL = 0, 1

RESULTS: list[tuple[bool, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((bool(ok), name, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{' - ' + detail if detail else ''}")
    return bool(ok)


async def _project(session, identifier: str) -> Project | None:
    return (await session.execute(
        select(Project).where(Project.identifier == identifier)
    )).scalar_one_or_none()


async def main() -> int:
    parser = argparse.ArgumentParser(description="P0 to P1 bridge acceptance")
    parser.add_argument("--project", required=True, help="项目标识")
    args = parser.parse_args()

    for label, path in (("Excel", EXCEL), ("Collection", COLLECTION), ("Environment", ENVIRONMENT)):
        if not path.exists():
            print(f"样本不存在（{label}）: {path}")
            return 2

    url = settings.postgres_sync_url.replace("postgresql://", "postgresql+asyncpg://")
    engine = create_async_engine(url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    # 每次运行使用新的资产包名：confirm 的幂等键是 (项目, method, normalized_path)，
    # 复用旧端点会跳过 custom_config 写入，导致脚本在旧数据上得到假阴性。
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    bundle_name = f"P0P1-{args.project}-{stamp}"

    try:
        # ---- 1. 解析 ----
        async with session_factory() as session:
            project = await _project(session, args.project)
            if project is None:
                print(f"项目不存在: {args.project}")
                return 2
            service = ApiBundleService(session)
            bundle = await service.create_bundle(
                project_id=project.id, name=bundle_name, system_type="crm_non_pos",
            )
            outcome = await service.parse_bundle(bundle, [
                SourceInput("excel_mx", EXCEL.name, EXCEL.read_bytes()),
                SourceInput("postman_collection", COLLECTION.name, COLLECTION.read_bytes()),
                SourceInput("postman_environment", ENVIRONMENT.name, ENVIRONMENT.read_bytes()),
            ])
            check(
                "parse-bundle 成功",
                not (outcome.stats.get("errors") or []),
                f"契约={len(outcome.contracts)} 冲突={len(outcome.conflicts)}",
            )
            await session.commit()

        # ---- 2. 确认冲突并导入（新 bundle 保证走真实创建分支） ----
        async with session_factory() as session:
            project = await _project(session, args.project)
            service = ApiBundleService(session)
            bundle = await service.create_bundle(
                project_id=project.id, name=bundle_name, system_type="crm_non_pos",
            )
            blocking = await service._unconfirmed_blocking_conflicts(bundle)  # noqa: SLF001
            if blocking:
                await service.confirm_conflicts(bundle, [
                    {"conflict_id": c["id"], "resolution": "accept"} for c in blocking
                ])
            confirmed = await service.confirm(bundle)
            await session.commit()
            print(f"    confirm: imported={len(confirmed.imported)} skipped={len(confirmed.skipped)}")

        # ---- 3. 取一个**有 Excel 契约**的导入端点 ----
        # Collection-only 请求（如 GET 查询类）本来就没有 Excel 契约，
        # auth 天然为空，不能用来验证桥接。
        async with session_factory() as session:
            project = await _project(session, args.project)
            candidates = (await session.execute(
                select(APIEndpoint).where(
                    APIEndpoint.project_id == project.id,
                    APIEndpoint.method == "POST",
                ).order_by(APIEndpoint.path)
            )).scalars().all()

            endpoint = next(
                (e for e in candidates if (e.custom_config or {}).get("auth")), None
            )
            if endpoint is None:
                check("存在带契约的导入端点", False,
                      f"POST 端点 {len(candidates)} 个，均无 auth")
                return EXIT_FAIL
            check("存在带契约的导入端点", True, f"{endpoint.method} {endpoint.path}")

            cc = endpoint.custom_config or {}
            check("导入端点的 custom_config 含 auth", bool(cc.get("auth")),
                  f"auth.type={(cc.get('auth') or {}).get('type')}")
            check("custom_config 含 api_name/method_name",
                  bool(cc.get("api_name")) and bool(cc.get("method_name")),
                  f"api_name={cc.get('api_name')} method_name={cc.get('method_name')}")
            check("source_location 为 dict 形状（生成器按 dict 消费）",
                  isinstance(cc.get("source_location"), dict),
                  f"type={type(cc.get('source_location')).__name__}")

            service = APITestService(session)
            spec = service._endpoint_to_spec(endpoint)  # noqa: SLF001
            check("_endpoint_to_spec 还原出 mx_md5 鉴权",
                  (spec.get("auth") or {}).get("type") == "mx_md5",
                  f"type={(spec.get('auth') or {}).get('type')}")

            try:
                artifacts = await service.generate_artifacts_from_endpoint(
                    endpoint_id=str(endpoint.id),
                    env_config={
                        "base_url": "https://uat.example.com",
                        "appid_ref": "env:MX_APPID",
                        "secret_ref": "env:MX_APPSECRET",
                    },
                )
                await session.commit()
                check("用导入端点生成契约产物成功", True,
                      f"script={len(artifacts['script'])}B "
                      f"attachment={artifacts['script_attachment_id'][:8]}...")
                check(
                    "生成脚本含 MD5 签名与目标地址",
                    "md5" in artifacts["script"].lower()
                    and "https://uat.example.com" in artifacts["script"],
                )
                check("测试计划非空", bool(artifacts.get("plan")))
            except Exception as exc:  # noqa: BLE001
                check("用导入端点生成契约产物成功", False, f"{type(exc).__name__}: {exc}")
    finally:
        await engine.dispose()

    passed = sum(1 for ok, _, _ in RESULTS if ok)
    print()
    print("=" * 72)
    print(f"桥接验收结果: {passed}/{len(RESULTS)} 通过")
    for ok, name, detail in RESULTS:
        if not ok:
            print(f"  - {name} {detail}")
    print("=" * 72)
    return EXIT_OK if passed == len(RESULTS) else EXIT_FAIL


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
