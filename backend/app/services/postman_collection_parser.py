"""Postman Collection 解析器。

支持 v2.0 / v2.1：
- 展开 folder（递归 item）
- 提取 URL、method、headers、query、body
- 提取 pre-request 和 test 脚本
- 提取事件、变量
- 保留来源位置（folder 路径 + item 索引）

输出统一 dict，供 api_asset_matcher 与契约模型对接。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any


# Postman URL 结构: {"raw": "...", "host": [...], "path": [...], "query": [...]}
# 或字符串 "https://example.com/api/json/memLogin"


@dataclass
class ParsedRequest:
    """解析出的单个请求。"""
    name: str
    method: str
    url_raw: str | None = None
    url_host: str | None = None
    url_path: str | None = None
    headers: list[dict[str, Any]] = field(default_factory=list)
    query: list[dict[str, Any]] = field(default_factory=list)
    body: dict[str, Any] | None = None
    pre_request_script: str | None = None
    test_script: str | None = None
    folder_path: list[str] = field(default_factory=list)
    item_index: int = 0
    variables: list[dict[str, Any]] = field(default_factory=list)
    # 提取的断言（从 test_script 解析）
    assertions: list[dict[str, Any]] = field(default_factory=list)
    # 提取的变量写入（从 pre_request/test 脚本解析）
    variable_writes: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ParsedCollection:
    """解析结果。"""
    name: str
    version: str  # "2.0" 或 "2.1"
    requests: list[ParsedRequest]
    variables: list[dict[str, Any]]
    base_url: str | None = None


class PostmanCollectionParser:
    """Postman Collection v2.x 解析器。"""

    def parse(self, content: bytes | str) -> ParsedCollection:
        if isinstance(content, bytes):
            content = content.decode("utf-8")
        data = json.loads(content)

        info = data.get("info", {})
        name = info.get("name", "Unnamed Collection")
        schema = info.get("schema", "")
        version = "2.1" if "v2.1" in schema else "2.0"

        # 提取 collection 级变量
        variables = self._parse_variables(data.get("variable", []))

        # 递归展开 items
        requests: list[ParsedRequest] = []
        for idx, item in enumerate(data.get("item", [])):
            self._walk_item(item, [], idx, requests)

        # 推断 base_url（取第一个有 host 的请求）
        base_url = None
        for r in requests:
            if r.url_host:
                base_url = r.url_host
                break

        return ParsedCollection(
            name=name,
            version=version,
            requests=requests,
            variables=variables,
            base_url=base_url,
        )

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _walk_item(
        self,
        item: dict[str, Any],
        folder: list[str],
        index: int,
        out: list[ParsedRequest],
    ) -> None:
        """递归展开 item；含 item 数组的是 folder，否则是 request。"""
        if "item" in item:
            # folder
            folder_name = item.get("name", f"folder_{index}")
            new_folder = folder + [folder_name]
            for sub_idx, sub in enumerate(item["item"]):
                self._walk_item(sub, new_folder, sub_idx, out)
            return

        # request
        request = item.get("request", {})
        if isinstance(request, str):
            # 极少数情况 request 是 URL 字符串
            request = {"url": request, "method": "GET"}

        name = item.get("name", f"request_{index}")
        method = (request.get("method") or "GET").upper()
        url_raw, url_host, url_path, query = self._parse_url(request.get("url"))

        headers = self._parse_headers(request.get("header", []))
        body = self._parse_body(request.get("body"))

        pre_script, test_script = self._parse_events(item.get("event", []))

        parsed = ParsedRequest(
            name=name,
            method=method,
            url_raw=url_raw,
            url_host=url_host,
            url_path=url_path,
            headers=headers,
            query=query,
            body=body,
            pre_request_script=pre_script,
            test_script=test_script,
            folder_path=folder,
            item_index=index,
        )

        # 从脚本提取断言和变量写入
        parsed.assertions = self._extract_assertions(test_script or "")
        parsed.variable_writes = self._extract_variable_writes(
            (pre_script or "") + "\n" + (test_script or "")
        )

        out.append(parsed)

    def _parse_url(self, url: Any) -> tuple[str | None, str | None, str | None, list[dict]]:
        """返回 (raw, host, path, query_params)。"""
        if url is None:
            return None, None, None, []
        if isinstance(url, str):
            return url, None, url, []

        raw = url.get("raw", "")
        host_parts = url.get("host", [])
        path_parts = url.get("path", [])
        query = url.get("query", [])

        host = ".".join(host_parts) if isinstance(host_parts, list) else str(host_parts or "")
        path = "/" + "/".join(path_parts) if isinstance(path_parts, list) else str(path_parts or "")
        if not path and raw:
            # 从 raw 提取 path
            path = self._path_from_raw(raw)

        query_params = []
        for q in (query or []):
            if isinstance(q, dict) and q.get("key"):
                query_params.append({
                    "key": q["key"],
                    "value": q.get("value", ""),
                    "disabled": q.get("disabled", False),
                })

        return raw or None, host or None, path or None, query_params

    @staticmethod
    def _path_from_raw(raw: str) -> str:
        m = re.search(r"https?://[^/]+(/[^?]*)", raw)
        return m.group(1) if m else raw

    @staticmethod
    def _parse_headers(headers: list) -> list[dict[str, Any]]:
        out = []
        for h in headers or []:
            if isinstance(h, dict) and h.get("key"):
                out.append({
                    "key": h["key"],
                    "value": h.get("value", ""),
                    "disabled": h.get("disabled", False),
                })
        return out

    def _parse_body(self, body: Any) -> dict[str, Any] | None:
        if not body:
            return None
        mode = body.get("mode", "raw")
        result: dict[str, Any] = {"mode": mode}

        if mode == "raw":
            raw = body.get("raw", "")
            result["raw"] = raw
            # 尝试解析 JSON
            try:
                result["json"] = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                pass
            # language
            lang = (body.get("options", {}).get("raw", {}).get("language", ""))
            if lang:
                result["language"] = lang
        elif mode == "urlencoded":
            result["urlencoded"] = [
                {"key": i.get("key"), "value": i.get("value", ""), "disabled": i.get("disabled", False)}
                for i in body.get("urlencoded", []) if isinstance(i, dict)
            ]
        elif mode == "formdata":
            result["formdata"] = [
                {"key": i.get("key"), "value": i.get("value", ""), "type": i.get("type", "text"), "disabled": i.get("disabled", False)}
                for i in body.get("formdata", []) if isinstance(i, dict)
            ]
        elif mode == "graphql":
            result["graphql"] = body.get("graphql", {})
        elif mode == "file":
            result["file"] = body.get("file", {})

        return result

    @staticmethod
    def _parse_events(events: list) -> tuple[str | None, str | None]:
        pre = None
        test = None
        for e in events or []:
            listen = e.get("listen", "")
            script_data = e.get("script", {})
            if isinstance(script_data, dict):
                exec_parts = script_data.get("exec", [])
                code = "\n".join(exec_parts) if isinstance(exec_parts, list) else str(exec_parts)
            else:
                code = str(script_data)
            if listen == "prerequest":
                pre = code
            elif listen == "test":
                test = code
        return pre, test

    @staticmethod
    def _parse_variables(variables: list) -> list[dict[str, Any]]:
        out = []
        for v in variables or []:
            if isinstance(v, dict) and v.get("key"):
                out.append({
                    "key": v["key"],
                    "value": v.get("value", ""),
                    "type": v.get("type", "default"),  # default / secret
                    "disabled": v.get("disabled", False),
                })
        return out

    # ------------------------------------------------------------------
    # 脚本分析
    # ------------------------------------------------------------------

    _ASSERT_PATTERNS = [
        # pm.expect(x).to.eql(y) / to.equal(y)
        (re.compile(r"pm\.expect\((.+?)\)\.to\.(?:eql|equal|deep\.equal)\((.+?)\)", re.DOTALL),
         "expect_equal"),
        # pm.response.to.have.status(200)
        (re.compile(r"pm\.response\.to\.have\.status\((\d+)\)"),
         "status"),
        # pm.expect(pm.response.code).to.equal(200)
        (re.compile(r"pm\.expect\(pm\.response\.code\)\.to\.(?:eql|equal)\((\d+)\)"),
         "status"),
        # pm.expect(pm.response.text()).to.include('x')
        (re.compile(r"pm\.expect\(pm\.response\.(?:text|json)\(\)\)\.to\.include\(['\"](.+?)['\"]\)"),
         "body_contains"),
        # jsonData = pm.response.json(); pm.expect(jsonData.Code).to.eql(0)
        (re.compile(r"pm\.expect\(jsonData\.([\w.]+?)\)\.to\.(?:eql|equal)\((.+?)\)", re.DOTALL),
         "json_path"),
    ]

    def _extract_assertions(self, script: str) -> list[dict[str, Any]]:
        assertions = []
        for pattern, atype in self._ASSERT_PATTERNS:
            for m in pattern.finditer(script):
                groups = [g.strip() for g in m.groups()]
                assertions.append({
                    "type": atype,
                    "expression": groups[0] if groups else "",
                    # 单组正则（如 status）的捕获组是期望值
                    "expected": groups[0] if len(groups) == 1 else (groups[1] if len(groups) > 1 else None),
                    "source": "collection_script",
                })
        return assertions

    _VAR_WRITE_RE = re.compile(
        r"pm\.(?:environment|collectionVariables|variables)\.set\(['\"](\w+)['\"]\s*,\s*(.+?)\)",
        re.DOTALL,
    )

    def _extract_variable_writes(self, script: str) -> list[dict[str, Any]]:
        writes = []
        for m in self._VAR_WRITE_RE.finditer(script):
            var_name = m.group(1)
            expr = m.group(2).strip()
            writes.append({
                "variable": var_name,
                "expression": expr,
                "source": "collection_script",
            })
        return writes
