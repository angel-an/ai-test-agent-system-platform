"""统一接口数据模型与冲突/缺失识别。

把 ExcelAPIParser 解析出的 ParsedInterface 转成"统一接口数据模型"（纯 dict），
后续需求分析/测试计划/脚本生成只读该模型，不再直接读 Excel。

同时识别冲突与缺失，产出"待确认项"（pending_confirmations）：
- 只列问题，不补造契约。
"""

from __future__ import annotations

from typing import Any

from app.services.excel_api_parser import ParsedField, ParsedInterface


# 占位地址特征（需人工确认真实 host）
_PLACEHOLDER_HOSTS = ("webservices", "{host}", "localhost", "127.0.0.1", "example.com", "/")


def _field_to_dict(f: ParsedField) -> dict[str, Any]:
    return {
        "name": f.name,
        "required": f.required,
        "type": f.type,
        "length": f.length,
        "example": f.example,
        "description": f.description,
        "level": f.level,
        "match": f.match,
        "diff": f.diff,
        "follow_up": f.follow_up,
        "questions": f.questions,
        "source": f.source,
    }


class APISpecNormalizer:
    """把解析结果归一化为统一接口模型，并检测冲突/缺失。"""

    def to_unified_spec(
        self,
        iface: ParsedInterface,
        guide: dict[str, Any],
        attachment_id: str | None = None,
    ) -> dict[str, Any]:
        method_name = self._method_name(iface)
        spec: dict[str, Any] = {
            "name": iface.api_name or iface.title or iface.sheet,
            "method_name": method_name,
            "http_method": iface.http_method,
            "path": iface.path,
            "call_url": iface.call_url,
            "title": iface.title,
            "summary": iface.title,
            "scenario": iface.scenario,
            "request": {
                "fields": [_field_to_dict(f) for f in iface.request_fields],
            },
            "response": {
                "fields": [_field_to_dict(f) for f in iface.response_fields],
                "error_codes": iface.error_codes,
            },
            "auth": self._build_auth(guide),
            "business_rules": self._collect_business_rules(iface),
            "source": {
                "type": "excel_mx",
                "sheet": iface.sheet,
                "attachment_id": attachment_id,
                "rows": sorted(set(iface.source_rows)),
            },
            "pending_confirmations": [],
        }
        spec["pending_confirmations"] = self.detect_conflicts(spec, iface)
        return spec

    def merge_common_rules(self, specs: list[dict[str, Any]], guide: dict[str, Any]) -> None:
        """把导读公共规则关联到每个接口（原地更新 auth）。"""
        for spec in specs:
            spec["auth"] = self._build_auth(guide)

    def detect_conflicts(self, spec: dict[str, Any], iface: ParsedInterface) -> list[dict[str, Any]]:
        """识别冲突与缺失，返回待确认项列表。"""
        issues: list[dict[str, Any]] = []

        # 1. 占位地址
        url = spec.get("call_url") or ""
        host = self._host_of(url)
        if not url:
            issues.append(self._issue("missing_call_url", None, "未在场景描述中找到 Web Service Call 调用地址", {"sheet": iface.sheet}))
        elif host in _PLACEHOLDER_HOSTS or "{" in host:
            issues.append(self._issue(
                "placeholder_url", None,
                f"调用地址为占位 host（{host}），需确认真实环境地址", {"sheet": iface.sheet, "url": url},
            ))

        # 2. 接口名与调用路径 method 不一致
        api_name = spec.get("name") or ""
        path = spec.get("path") or ""
        if api_name and path and api_name not in path:
            issues.append(self._issue(
                "name_path_mismatch", None,
                f"接口名（{api_name}）与调用路径（{path}）不一致", {"sheet": iface.sheet},
            ))

        # 3. 字段大小写冲突（同名字段不同大小写）+ 示例与声明类型不一致 + 匹配/疑问
        for section in ("request", "response"):
            seen_lower: dict[str, str] = {}
            for f in spec[section]["fields"]:
                name = f["name"]
                low = name.lower()
                if low in seen_lower and seen_lower[low] != name:
                    issues.append(self._issue(
                        "case_conflict", name,
                        f"字段大小写冲突：'{seen_lower[low]}' 与 '{name}'",
                        f.get("source"),
                    ))
                else:
                    seen_lower[low] = name
                # 匹配列 × 或 ？ -> 待确认
                if f.get("match") in {"×", "x", "？", "?"}:
                    issues.append(self._issue(
                        "match_flag", name,
                        f"字段匹配标记为 '{f['match']}'，需确认是否返回/实现",
                        f.get("source"),
                    ))
                # 疑问清单
                if f.get("questions"):
                    issues.append(self._issue(
                        "questions", name,
                        f"存在疑问清单：{f['questions'][:200]}",
                        f.get("source"),
                    ))

        # 4. 错误码缺失
        if not spec["response"].get("error_codes"):
            issues.append(self._issue(
                "missing_error_codes", None,
                "未提供错误码表（Return Code），失败分支断言需人工确认",
                {"sheet": iface.sheet},
            ))

        return issues

    # ---------- 内部 ----------

    @staticmethod
    def _method_name(iface: ParsedInterface) -> str | None:
        if iface.api_name and iface.api_name.startswith("api_"):
            return iface.api_name[len("api_"):]
        return iface.api_name

    @staticmethod
    def _build_auth(guide: dict[str, Any]) -> dict[str, Any]:
        sign = guide.get("sign") or {}
        return {
            "type": "mx_md5",
            "appid_ref": "env:MX_APPID",
            "secret_ref": "env:MX_APPSECRET",
            "ts_format": "yyyyMMddHHmmss",
            "ts_timezone": "Asia/Hong_Kong",
            "sign_formula": "md5(appid + appsercret + ts + msg)",
            "sign_fields": sign.get("concat_order", ["appid", "appsercret", "ts", "msg"]),
            "sign_case": sign.get("case", "lower"),
            "model": guide.get("model"),
            "method_rule": guide.get("method_rule"),
            "sign_doc_text": guide.get("sign_text"),
        }

    @staticmethod
    def _collect_business_rules(iface: ParsedInterface) -> list[dict[str, Any]]:
        """从說明/差異列提取业务规则（保留原文与来源）。"""
        rules = []
        for f in iface.request_fields + iface.response_fields:
            if f.diff:
                rules.append({
                    "field": f.name,
                    "section": "request" if f in iface.request_fields else "response",
                    "text": f.diff,
                    "source": f.source,
                })
        return rules

    @staticmethod
    def _host_of(url: str) -> str:
        m = url.split("//", 1)
        if len(m) == 2:
            return m[1].split("/", 1)[0].split(":", 1)[0]
        return url.split("/", 1)[0]

    @staticmethod
    def _issue(kind: str, field: str | None, message: str, source: Any) -> dict[str, Any]:
        return {"type": kind, "field": field, "message": message, "source": source}
