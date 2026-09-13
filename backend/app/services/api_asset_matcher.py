"""接口资产匹配与冲突检测引擎。

职责：
- URL 归一化：去 base_url、处理 {{variable}}、去尾斜杠、小写
- method + normalized_path 主匹配；name/operationId 辅助匹配
- 跨来源冲突检测：method/path/field/required/auth/assertion/env_missing/production_url/script_unconvertible/excel_only/collection_only
- 冲突分级：high / medium / low
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.services.postman_collection_parser import ParsedCollection, ParsedRequest


# ---------------------------------------------------------------------------
# URL 归一化
# ---------------------------------------------------------------------------

_VAR_RE = re.compile(r"\{\{(\w+)\}\}")

# 占位 host（与 api_spec_normalizer._PLACEHOLDER_HOSTS 对齐）
_PLACEHOLDER_HOSTS = {"webservices", "{host}", "localhost", "127.0.0.1", "example.com"}

# UAT host 特征：uat/test/staging/sit/qa（api.maxims.com 视为生产）
_UAT_HOST_RE = re.compile(r"uat|test|staging|sit|qa|dev", re.IGNORECASE)


def normalize_path(url: str | None) -> str:
    """归一化路径：去协议/host/变量/尾斜杠，转小写。

    - 完整 URL -> 取 path 部分
    - {{variable}}/api/json/xxx -> /api/json/xxx
    - 去尾斜杠
    - 小写
    """
    if not url:
        return ""
    s = str(url).strip()

    # 去协议和 host
    m = re.match(r"https?://([^/]+)(/.*)?", s, re.IGNORECASE)
    if m:
        s = m.group(2) or "/"

    # 去 {{variable}} 前缀（如 {{base_url}}/api/... -> /api/...）
    s = _VAR_RE.sub("", s)

    # 若仍以 host 开头（无 / 前缀），尝试提取 /api/ 后的部分
    if not s.startswith("/"):
        idx = s.find("/api/")
        s = s[idx:] if idx >= 0 else "/" + s

    # 去 query string
    s = s.split("?")[0]

    # 去尾斜杠（保留根路径 /）
    if len(s) > 1:
        s = s.rstrip("/")

    return s.lower()


def extract_host(url: str | None) -> str | None:
    """提取 host；占位 host 返回 None。"""
    if not url:
        return None
    m = re.match(r"https?://([^/]+)", str(url), re.IGNORECASE)
    if m:
        host = m.group(1).lower()
        return None if host in _PLACEHOLDER_HOSTS else host
    return None


def endpoint_key_from_path(path: str) -> str:
    """从路径提取接口标识：/api/json/memLogin -> api_memlogin（小写）。"""
    if not path:
        return ""
    last = path.rstrip("/").split("/")[-1].lower()
    return f"api_{last}" if not last.startswith("api_") else last


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------

@dataclass
class MatchCandidate:
    """待匹配的接口（来自 Excel 或 Collection）。"""
    source_type: str  # excel_mx / postman_collection
    name: str
    method: str
    path: str  # 原始路径
    normalized_path: str
    endpoint_key: str
    request_fields: list[dict[str, Any]] = field(default_factory=list)
    response_fields: list[dict[str, Any]] = field(default_factory=list)
    auth: dict[str, Any] | None = None
    assertions: list[dict[str, Any]] = field(default_factory=list)
    raw: Any = None  # 原始 ParsedRequest 或统一模型 dict


@dataclass
class Conflict:
    conflict_type: str
    severity: str  # high / medium / low
    message: str
    contract_key: str  # method + normalized_path
    details: dict[str, Any] = field(default_factory=dict)
    source_refs: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class MatchResult:
    """匹配结果：契约分组 + 冲突列表。"""
    contracts: list[dict[str, Any]] = field(default_factory=list)
    conflicts: list[Conflict] = field(default_factory=list)
    unmatched_excel: list[MatchCandidate] = field(default_factory=list)
    unmatched_collection: list[MatchCandidate] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.contracts)

    @property
    def conflicted_count(self) -> int:
        return len({c.contract_key for c in self.conflicts})

    def conflicts_by_severity(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for c in self.conflicts:
            out[c.severity] = out.get(c.severity, 0) + 1
        return out


# ---------------------------------------------------------------------------
# 匹配器
# ---------------------------------------------------------------------------

class APIAssetMatcher:
    """跨来源接口匹配与冲突检测。"""

    def match(
        self,
        excel_specs: list[dict[str, Any]],
        collection: ParsedCollection | None = None,
        environment_vars: list[str] | None = None,
    ) -> MatchResult:
        """
        Args:
            excel_specs: APISpecNormalizer.to_unified_spec 产出的统一模型列表
            collection: PostmanCollectionParser 解析结果（可为 None）
            environment_vars: 环境变量名列表（检查 env_missing 冲突）

        Returns:
            MatchResult 含契约分组与冲突列表
        """
        result = MatchResult()
        # 已知变量 = 环境变量 + Collection 自身变量（Postman 会合并解析，
        # 只在内建变量里定义的引用不应被报为"环境缺失"）
        env_var_set = set(environment_vars or [])
        if collection is not None:
            for var in (collection.variables or []):
                key = var.get("key") or var.get("name")
                if key:
                    env_var_set.add(str(key))

        # 构建候选
        excel_candidates = [self._excel_to_candidate(s) for s in excel_specs]
        coll_candidates: list[MatchCandidate] = []
        if collection:
            coll_candidates = [self._request_to_candidate(r) for r in collection.requests]

        # 主匹配：method + normalized_path
        contract_map: dict[str, dict[str, Any]] = {}

        def _get_contract(key: str, method: str, path: str, ep_key: str) -> dict[str, Any]:
            if key not in contract_map:
                contract_map[key] = {
                    "method": method,
                    "normalized_path": path,
                    "endpoint_key": ep_key,
                    "sources": {},
                    "excel": None,
                    "collection": [],
                }
            return contract_map[key]

        for c in excel_candidates:
            key = f"{c.method} {c.normalized_path}"
            contract = _get_contract(key, c.method, c.normalized_path, c.endpoint_key)
            contract["sources"]["excel_mx"] = True
            contract["excel"] = c

        for c in coll_candidates:
            key = f"{c.method} {c.normalized_path}"
            contract = _get_contract(key, c.method, c.normalized_path, c.endpoint_key)
            contract["sources"]["postman_collection"] = True
            contract["collection"].append(c)

        result.contracts = list(contract_map.values())

        # 冲突检测
        for key, contract in contract_map.items():
            excel_c: MatchCandidate | None = contract["excel"]
            coll_cs: list[MatchCandidate] = contract["collection"]

            # 1. excel_only / collection_only
            if excel_c and not coll_cs:
                result.conflicts.append(Conflict(
                    conflict_type="collection_only" if False else "excel_only",
                    severity="low",
                    message=f"仅 Excel 存在该接口: {key}",
                    contract_key=key,
                    source_refs=[{"source_type": "excel_mx", "name": excel_c.name}],
                ))
            elif coll_cs and not excel_c:
                for cc in coll_cs:
                    result.conflicts.append(Conflict(
                        conflict_type="collection_only",
                        severity="low",
                        message=f"仅 Collection 存在该接口: {key}",
                        contract_key=key,
                        source_refs=[{"source_type": "postman_collection", "name": cc.name}],
                    ))

            # 2. 字段/必填/鉴权/断言冲突（双方都存在时）
            if excel_c and coll_cs:
                for cc in coll_cs:
                    result.conflicts.extend(
                        self._detect_pair_conflicts(excel_c, cc, env_var_set)
                    )

            # 3. 单边来源也必须做的环境/地址检查
            #    env_missing / production_url 属于「来源自身」的问题，
            #    不依赖 Excel 是否提供该接口；否则 collection-only 的请求
            #    （真实美心 47 个请求里有 34 个）会被漏检，缺失变量无法暴露。
            for cc in coll_cs:
                if not excel_c:
                    result.conflicts.extend(
                        self._detect_source_conflicts(cc, env_var_set)
                    )

        # 未匹配项
        matched_keys = set(contract_map.keys())
        result.unmatched_excel = [
            c for c in excel_candidates
            if f"{c.method} {c.normalized_path}" not in matched_keys
        ]
        result.unmatched_collection = [
            c for c in coll_candidates
            if f"{c.method} {c.normalized_path}" not in matched_keys
        ]

        return result

    # ------------------------------------------------------------------
    # 候选构建
    # ------------------------------------------------------------------

    @staticmethod
    def _excel_to_candidate(spec: dict[str, Any]) -> MatchCandidate:
        path = spec.get("path") or ""
        method = (spec.get("http_method") or "POST").upper()
        norm = normalize_path(path)
        # 去掉 /api/ 前缀后的 api_xxx 重复（/api/json/api_memLogin -> /api/json/memLogin）
        norm = re.sub(r"/api_(?=[a-z])", "/", norm)
        return MatchCandidate(
            source_type="excel_mx",
            name=spec.get("name") or spec.get("method_name") or "",
            method=method,
            path=path,
            normalized_path=norm,
            endpoint_key=(spec.get("method_name") or "").lower() or endpoint_key_from_path(norm),
            request_fields=(spec.get("request") or {}).get("fields", []),
            response_fields=(spec.get("response") or {}).get("fields", []),
            auth=spec.get("auth"),
            assertions=[],
            raw=spec,
        )

    @staticmethod
    def _request_to_candidate(r: ParsedRequest) -> MatchCandidate:
        path = r.url_path or r.url_raw or ""
        norm = normalize_path(path)
        # 美心 Collection 用通用网关路径（/cop/json、/aigens/json、/mp/json）
        # 实际接口由 method header 区分，提取为虚拟路径
        method_header = next(
            (h["value"] for h in (r.headers or []) if h["key"].lower() == "method"),
            None,
        )
        if method_header and norm in ("/cop/json", "/aigens/json", "/mp/json"):
            # 统一为 /api/json/{method}，与 Excel 路径对齐
            # 去掉 method 中的 ord_ 前缀（Excel 无此前缀）
            method_clean = method_header.lower()
            if method_clean.startswith("ord_"):
                method_clean = method_clean[4:]
            norm = f"/api/json/{method_clean}"
        # 去掉 /api/ 前缀后的 api_xxx 重复（/api/json/api_memLogin -> /api/json/memLogin）
        norm = re.sub(r"/api_(?=[a-z])", "/", norm)
        return MatchCandidate(
            source_type="postman_collection",
            name=r.name,
            method=r.method,
            path=path,
            normalized_path=norm,
            endpoint_key=endpoint_key_from_path(norm),
            request_fields=[],  # Collection 字段在 body.json 中，不做字段级对比
            response_fields=[],
            auth=None,
            assertions=r.assertions,
            raw=r,
        )

    # ------------------------------------------------------------------
    # 单边来源检查（不依赖 Excel 配对）
    # ------------------------------------------------------------------

    def _detect_source_conflicts(
        self,
        coll_c: MatchCandidate,
        env_var_set: set[str],
    ) -> list[Conflict]:
        """仅凭 Collection 自身即可判定的冲突：环境缺变量、生产地址。

        这类问题与「Excel 是否声明了该接口」无关，因此对 collection-only
        的请求同样必须检查，否则缺失变量会被静默漏掉。
        """
        conflicts: list[Conflict] = []
        raw: ParsedRequest | None = coll_c.raw
        if raw is None:
            return conflicts

        key = f"{coll_c.method} {coll_c.normalized_path}"

        # 环境缺变量（high）
        refs: set[str] = set()
        if raw.url_raw:
            refs.update(_VAR_RE.findall(raw.url_raw))
        for h in (raw.headers or []):
            refs.update(_VAR_RE.findall(h.get("value", "")))
        body_raw = (raw.body or {}).get("raw", "") if raw.body else ""
        if body_raw:
            refs.update(_VAR_RE.findall(body_raw))

        missing = refs - env_var_set
        if missing:
            conflicts.append(Conflict(
                conflict_type="env_missing",
                severity="high",
                message=f"环境缺少变量: {', '.join(sorted(missing))}",
                contract_key=key,
                details={"missing": sorted(missing)},
                source_refs=[{"source_type": "postman_collection", "name": coll_c.name}],
            ))

        # 生产地址（high）
        host = extract_host(raw.url_raw)
        if host and not _UAT_HOST_RE.search(host):
            conflicts.append(Conflict(
                conflict_type="production_url",
                severity="high",
                message=f"Collection 请求指向生产地址: {host}",
                contract_key=key,
                details={"host": host},
                source_refs=[{"source_type": "postman_collection", "name": coll_c.name}],
            ))

        return conflicts

    # ------------------------------------------------------------------
    # 冲突检测
    # ------------------------------------------------------------------

    def _detect_pair_conflicts(
        self,
        excel_c: MatchCandidate,
        coll_c: MatchCandidate,
        env_var_set: set[str],
    ) -> list[Conflict]:
        conflicts: list[Conflict] = []
        key = f"{excel_c.method} {excel_c.normalized_path}"

        # 3. method 冲突（同 path 不同 method，理论上已分组，但防御）
        if excel_c.method != coll_c.method:
            conflicts.append(Conflict(
                conflict_type="method",
                severity="high",
                message=f"方法冲突: Excel={excel_c.method}, Collection={coll_c.method}",
                contract_key=key,
                details={"excel": excel_c.method, "collection": coll_c.method},
            ))

        # 4. 字段冲突：同名不同类型
        coll_body = (coll_c.raw.body or {}) if coll_c.raw else {}
        coll_json = coll_body.get("json") or {}
        if isinstance(coll_json, dict) and excel_c.request_fields:
            excel_field_map = {f["name"]: f for f in excel_c.request_fields if f.get("name")}
            for fname, fval in coll_json.items():
                if fname in excel_field_map:
                    ef = excel_field_map[fname]
                    ef_type = (ef.get("type") or "").lower()
                    coll_type = type(fval).__name__.lower()
                    # 简单类型映射对比
                    if ef_type and not self._type_compatible(ef_type, coll_type):
                        conflicts.append(Conflict(
                            conflict_type="field",
                            severity="medium",
                            message=f"字段类型冲突: {fname} Excel={ef_type}, Collection={coll_type}",
                            contract_key=key,
                            details={"field": fname, "excel_type": ef_type, "collection_type": coll_type},
                        ))

        # 5. 必填冲突：Excel 必填但 Collection body 缺失
        if excel_c.request_fields and isinstance(coll_json, dict):
            for f in excel_c.request_fields:
                if f.get("required") and f.get("name") not in coll_json:
                    # 检查是否在 query 或 header 中
                    coll_headers = {h["key"] for h in (coll_c.raw.headers or [])} if coll_c.raw else set()
                    coll_query = {q["key"] for q in (coll_c.raw.query or [])} if coll_c.raw else set()
                    if f["name"] not in coll_headers and f["name"] not in coll_query:
                        conflicts.append(Conflict(
                            conflict_type="required",
                            severity="medium",
                            message=f"Excel 必填字段在 Collection 请求中缺失: {f['name']}",
                            contract_key=key,
                            details={"field": f["name"]},
                        ))

        # 6. 鉴权冲突
        excel_auth = excel_c.auth or {}
        coll_headers = {h["key"].lower(): h.get("value", "") for h in (coll_c.raw.headers or [])} if coll_c.raw else {}
        has_excel_auth = excel_auth.get("type") == "mx_md5"
        has_coll_auth = any(
            k in coll_headers or k in (coll_c.raw.url_raw or "").lower()
            for k in ("appid", "sign", "ts", "authorization")
        ) if coll_c.raw else False
        if has_excel_auth and not has_coll_auth:
            conflicts.append(Conflict(
                conflict_type="auth",
                severity="medium",
                message="Excel 声明 MD5 鉴权，但 Collection 请求未携带鉴权头",
                contract_key=key,
            ))

        # 7. 断言冲突：Collection 有断言但 Excel 无错误码
        excel_error_codes = []
        if excel_c.raw:
            excel_error_codes = (excel_c.raw.get("response") or {}).get("error_codes", [])
        if coll_c.assertions and not excel_error_codes:
            conflicts.append(Conflict(
                conflict_type="assertion",
                severity="low",
                message="Collection 含断言但 Excel 未提供错误码表，失败分支断言依据不足",
                contract_key=key,
                details={"assertions": [a.get("type") for a in coll_c.assertions]},
            ))

        # 8. 环境缺变量
        if coll_c.raw:
            r: ParsedRequest = coll_c.raw
            refs = set()
            if r.url_raw:
                refs.update(_VAR_RE.findall(r.url_raw))
            for h in (r.headers or []):
                refs.update(_VAR_RE.findall(h.get("value", "")))
            body_raw = (r.body or {}).get("raw", "") if r.body else ""
            if body_raw:
                refs.update(_VAR_RE.findall(body_raw))
            missing = refs - env_var_set
            # base_url 等常见变量若在 collection 变量中定义则不算缺失
            if missing:
                conflicts.append(Conflict(
                    conflict_type="env_missing",
                    severity="high",
                    message=f"环境缺少变量: {', '.join(sorted(missing))}",
                    contract_key=key,
                    details={"missing": sorted(missing)},
                ))

        # 9. 生产地址检测
        coll_url = (coll_c.raw.url_raw or "") if coll_c.raw else ""
        host = extract_host(coll_url)
        if host and not _UAT_HOST_RE.search(host):
            conflicts.append(Conflict(
                conflict_type="production_url",
                severity="high",
                message=f"Collection 请求指向生产地址: {host}",
                contract_key=key,
                details={"host": host},
            ))

        # 10. 不可转换脚本
        if coll_c.raw and coll_c.raw.pre_request_script:
            script = coll_c.raw.pre_request_script
            # 检测 crypto-js 等非标准 require
            if "require(" in script and "crypto" in script.lower():
                conflicts.append(Conflict(
                    conflict_type="script_unconvertible",
                    severity="medium",
                    message="pre-request 脚本含 crypto require，需人工确认转换",
                    contract_key=key,
                ))

        return conflicts

    @staticmethod
    def _type_compatible(excel_type: str, coll_type: str) -> bool:
        """类型兼容判断（含别名映射）。"""
        aliases = {"string": "str", "integer": "int", "number": "float", "boolean": "bool", "array": "list", "object": "dict"}
        et = aliases.get(excel_type, excel_type)
        ct = aliases.get(coll_type, coll_type)
        mapping = {
            "str": {"str"},
            "int": {"int", "float"},      # Python bool 是 int 子类，但 json 中 true/false 是 bool
            "float": {"float", "int"},
            "bool": {"bool"},
            "list": {"list"},
            "dict": {"dict"},
        }
        return ct in mapping.get(et, {et})
