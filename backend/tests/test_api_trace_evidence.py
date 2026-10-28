"""Playwright trace → 真实请求/响应证据 的门禁测试（2026-10-27）。

背景：IDP 缺陷登记要求「有完整请求/响应证据」才允许自动建单，但
Playwright **JSON 报告里根本没有 request/response 结构**（实测 ``results[]``
只有 status/error/attachments…），历史代码按 ``result["request"]["url"]`` 取值，
永远取到空 ⇒ ``allow_create`` 恒 False ⇒ 失败自动建单从未发生。

trace 附件（``use.trace='retain-on-failure'``）里的 ``*-trace.network`` 是
HAR 风格网络快照，是唯一不改脚本就能拿到的真实往返记录。本文件锁住：

1. 能取到 method/url/status/请求头/请求体/响应体/reqid；
2. 多请求时取**最后一条**（登录之后的那个目标接口）；
3. 静态资源不算被测接口；
4. 无 trace / 损坏 zip 一律返回 None 且**不抛异常**（不能拖垮执行）；
5. 大响应按上限截断，不把整份列表塞进缺陷描述；
6. 提取不到证据时必须 ``complete=False``、状态码 None —— 不许编 200/500。
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

from app.services.api_test_executor import APITestExecutor
from app.services.trace_evidence import (
    MAX_BODY_BYTES,
    extract_from_result,
    extract_last_exchange,
    find_trace_attachment,
)


def _snapshot(
    method: str,
    url: str,
    status: int | None,
    started: str,
    *,
    response_text: str | None = None,
    response_file: str | None = None,
    response_size: int | None = None,
    request_headers: list | None = None,
    post_text: str | None = None,
    response_headers: list | None = None,
) -> dict:
    response: dict = {
        "status": status,
        "statusText": "OK",
        "headers": response_headers
        if response_headers is not None
        else [{"name": "x-request-id", "value": "0A000607-TRACE-1"}],
    }
    if response_file:
        response["content"] = {
            "size": response_size if response_size is not None else 64,
            "mimeType": "application/json",
            "_file": response_file,
        }
    elif response_text is not None:
        response["content"] = {"size": len(response_text), "text": response_text}
    request: dict = {
        "method": method,
        "url": url,
        "headers": request_headers
        if request_headers is not None
        else [{"name": "Content-Type", "value": "application/json"}],
    }
    if post_text is not None:
        request["postData"] = {"text": post_text}
    return {
        "type": "resource-snapshot",
        "snapshot": {
            "startedDateTime": started,
            "time": 12.5,
            "request": request,
            "response": response,
        },
    }


def _write_trace(tmp_path: Path, snapshots: list[dict], resources: dict[str, bytes] | None = None) -> Path:
    trace = tmp_path / "trace.zip"
    with zipfile.ZipFile(trace, "w") as zf:
        zf.writestr("0-trace.trace", json.dumps({"type": "context-options"}) + "\n")
        zf.writestr(
            "0-trace.network",
            "\n".join(json.dumps(s, ensure_ascii=False) for s in snapshots) + "\n",
        )
        for name, data in (resources or {}).items():
            zf.writestr(name, data)
    return trace


class TestTraceExtraction:
    def test_extracts_full_exchange(self, tmp_path):
        trace = _write_trace(
            tmp_path,
            [
                _snapshot(
                    "POST",
                    "https://sut.example.com/api/v1/orders?page=2",
                    500,
                    "2026-10-27T13:00:00.000Z",
                    response_file="resources/body.json",
                    post_text='{"sku":"A1"}',
                )
            ],
            resources={"resources/body.json": json.dumps({"code": 500, "msg": "boom"}).encode()},
        )
        ex = extract_last_exchange(str(trace))
        assert ex is not None
        assert ex.method == "POST"
        assert ex.status_code == 500
        assert ex.path == "/api/v1/orders"
        assert ex.query == "page=2"
        # 请求头键统一小写，值原样保留
        assert ex.request_headers["content-type"] == "application/json"
        # 请求体 JSON 应被解析成对象
        assert ex.request_body == {"sku": "A1"}
        # 响应体从 zip 内的 resources 文件读出
        assert ex.response_body == {"code": 500, "msg": "boom"}
        assert ex.response_headers["x-request-id"] == "0A000607-TRACE-1"

    def test_takes_last_request(self, tmp_path):
        trace = _write_trace(
            tmp_path,
            [
                _snapshot("POST", "https://sut.example.com/api/login", 200, "2026-10-27T13:00:00.000Z"),
                _snapshot("GET", "https://sut.example.com/api/orders", 404, "2026-10-27T13:00:05.000Z"),
            ],
        )
        ex = extract_last_exchange(str(trace))
        assert ex is not None and ex.path == "/api/orders" and ex.status_code == 404

    def test_skips_static_resources(self, tmp_path):
        trace = _write_trace(
            tmp_path,
            [
                _snapshot("GET", "https://sut.example.com/api/orders", 200, "2026-10-27T13:00:00.000Z"),
                _snapshot("GET", "https://sut.example.com/static/app.js", 200, "2026-10-27T13:00:09.000Z"),
            ],
        )
        ex = extract_last_exchange(str(trace))
        assert ex is not None and ex.path == "/api/orders"

    def test_reads_body_from_sha1_reference(self, tmp_path):
        """回归（2026-10-27 实测平台版本）：响应体以 `_sha1` 指向 resources/ 下文件。

        历史解析只认 `_file`，导致平台真实执行的响应体**永远为空**
        （状态码/头都拿到了，body 恒 {}）。
        """
        trace = tmp_path / "trace.zip"
        sha = "aaa5a1bd50eebbbc1c8ef6d4047a002a346dceb3.json"
        with zipfile.ZipFile(trace, "w") as zf:
            zf.writestr(
                "0-trace.network",
                json.dumps(
                    {
                        "type": "resource-snapshot",
                        "snapshot": {
                            "startedDateTime": "2026-10-27T13:22:00.000Z",
                            "time": 17.4,
                            "request": {"method": "GET", "url": "https://sut.example.com/api/x", "headers": []},
                            "response": {
                                "status": 404,
                                "headers": [{"name": "content-type", "value": "application/json"}],
                                "content": {
                                    "size": 59,
                                    "mimeType": "application/json",
                                    "_sha1": sha,
                                },
                            },
                        },
                    }
                )
                + "\n",
            )
            zf.writestr(f"resources/{sha}", json.dumps({"detail": "Not Found"}).encode())
        ex = extract_last_exchange(str(trace))
        assert ex is not None
        assert ex.status_code == 404
        assert ex.response_body == {"detail": "Not Found"}

    def test_reads_request_body_from_sha1_reference(self, tmp_path):
        """回归（2026-10-28 实测 ATR-20261028-004 的真实 trace.zip）：

        ``request.postData`` 是 ``{"mimeType": "application/json", "text": "",
        "params": [], "_sha1": "…json"}`` —— **text 是空串**，真实报文在
        ``resources/<sha1>``。历史实现只取 ``postData["text"]``，
        于是工单里「请求参数」恒空，只剩请求头兜底（content-length: 28 却看不到体）。
        """
        trace = tmp_path / "trace.zip"
        sha = "05b6bfec90d2772b36f9903dcc1d6c4c287fb8a4.json"
        with zipfile.ZipFile(trace, "w") as zf:
            zf.writestr(
                "0-trace.network",
                json.dumps(
                    {
                        "type": "resource-snapshot",
                        "snapshot": {
                            "startedDateTime": "2026-10-28T05:34:58.000Z",
                            "time": 232.1,
                            "request": {
                                "method": "POST",
                                "url": "https://sut.example.com/api/icdp/v1/memberCard/refund/calculate",
                                "headers": [
                                    {"name": "content-type", "value": "application/json"},
                                    {"name": "content-length", "value": "28"},
                                ],
                                "bodySize": 28,
                                "postData": {
                                    "mimeType": "application/json",
                                    "text": "",
                                    "params": [],
                                    "_sha1": sha,
                                },
                            },
                            "response": {"status": 404, "headers": []},
                        },
                    }
                )
                + "\n",
            )
            zf.writestr(f"resources/{sha}", b'{"orderNo":"xxxxxxxxxxxxxx"}')
        ex = extract_last_exchange(str(trace))
        assert ex is not None
        assert ex.status_code == 404
        assert ex.request_body == {"orderNo": "xxxxxxxxxxxxxx"}

    def test_missing_request_body_is_none_not_empty_dict(self, tmp_path):
        """请求体既无内联 text 也无 _sha1 ⇒ None（描述里才敢说"未采集到"）。"""
        trace = _write_trace(
            tmp_path,
            [_snapshot("POST", "https://sut.example.com/api/x", 500, "2026-10-27T13:00:00.000Z")],
        )
        ex = extract_last_exchange(str(trace))
        assert ex is not None and ex.request_body is None

    def test_large_body_is_truncated(self, tmp_path):
        big = "x" * (MAX_BODY_BYTES * 2)
        trace = _write_trace(
            tmp_path,
            [
                _snapshot(
                    "GET",
                    "https://sut.example.com/api/list",
                    200,
                    "2026-10-27T13:00:00.000Z",
                    response_file="resources/big.txt",
                    response_size=len(big),
                )
            ],
            resources={"resources/big.txt": big.encode()},
        )
        ex = extract_last_exchange(str(trace))
        assert ex is not None
        assert isinstance(ex.response_body, str)
        assert len(ex.response_body) <= MAX_BODY_BYTES + 64

    def test_missing_trace_returns_none(self):
        assert extract_last_exchange("") is None
        assert extract_last_exchange("D:/no/such/trace.zip") is None

    def test_corrupt_zip_returns_none(self, tmp_path):
        bad = tmp_path / "bad.zip"
        bad.write_bytes(b"not a zip at all")
        assert extract_last_exchange(str(bad)) is None

    def test_find_trace_attachment(self):
        assert find_trace_attachment(
            [{"name": "error-context", "contentType": "text/markdown", "path": "a.md"}]
        ) is None
        assert (
            find_trace_attachment(
                [
                    {"name": "error-context", "contentType": "text/markdown", "path": "a.md"},
                    {"name": "trace", "contentType": "application/zip", "path": "t.zip"},
                ]
            )
            == "t.zip"
        )

    def test_extract_from_result_without_attachments(self):
        assert extract_from_result({"status": "unexpected"}) is None
        assert extract_from_result({"attachments": []}) is None


class TestExecutorEvidence:
    """执行器的证据装配：有 trace 就有证据，没有就明确标"不知道"。"""

    @staticmethod
    def _executor() -> APITestExecutor:
        return APITestExecutor.__new__(APITestExecutor)

    @staticmethod
    def _api_test(base_url: str = "https://sut.example.com") -> SimpleNamespace:
        return SimpleNamespace(test_config={"base_url": base_url})

    def test_evidence_from_trace_marks_complete(self, tmp_path):
        trace = _write_trace(
            tmp_path,
            [
                _snapshot(
                    "GET",
                    "https://sut.example.com/api/v1/orders?page=2",
                    502,
                    "2026-10-27T13:00:00.000Z",
                    response_text=json.dumps({"code": 502, "reqid": "REQ-XYZ"}),
                )
            ],
        )
        results = [
            {
                "status": "unexpected",
                "error": {"message": "expect(received).toBe(expected) // 502 !== 200"},
                "attachments": [{"name": "trace", "contentType": "application/zip", "path": str(trace)}],
            }
        ]
        ev = self._executor()._extract_http_evidence(results, self._api_test(), "GET /api/v1/orders")
        assert ev["complete"] is True
        assert ev["source"] == "trace"
        assert ev["method"] == "GET"
        # endpoint 取 URL path，不受 test_config.base_url 与执行时 base_url 不一致影响
        assert ev["endpoint"] == "/api/v1/orders"
        assert ev["request_summary"]["query"] == "page=2"
        assert ev["response_summary"]["status_code"] == 502
        # reqid 优先取响应头，头里没有才回落到响应体
        assert ev["response_summary"]["reqid"] == "0A000607-TRACE-1"

    def test_reqid_falls_back_to_body(self, tmp_path):
        trace = _write_trace(
            tmp_path,
            [
                _snapshot(
                    "GET",
                    "https://sut.example.com/api/v1/orders",
                    500,
                    "2026-10-27T13:00:00.000Z",
                    response_text=json.dumps({"reqid": "FROM-BODY"}),
                    response_headers=[{"name": "content-type", "value": "application/json"}],
                )
            ],
        )
        results = [{"attachments": [{"name": "trace", "path": str(trace)}]}]
        ev = self._executor()._extract_http_evidence(results, self._api_test(), "GET /x")
        assert ev["response_summary"]["reqid"] == "FROM-BODY"

    def test_no_trace_means_unknown_not_fabricated(self):
        """没有 trace 时必须是"未知"，绝不能出现编造的 200/500。"""
        results = [{"status": "unexpected", "error": {"message": "boom"}}]
        ev = self._executor()._extract_http_evidence(results, self._api_test(), "GET /api/v1/orders")
        assert ev["complete"] is False
        assert ev["source"] == "fallback"
        assert ev["response_summary"]["status_code"] is None
        # 降级只解析方法，不编造接口路径
        assert ev["method"] == "GET"

    def test_playwright_status_string_never_becomes_status_code(self):
        """回归：`results[].status` 是用例状态字符串，不是 HTTP 状态码。"""
        results = [{"status": "failed", "error": {"message": "timedOut"}}]
        ev = self._executor()._extract_http_evidence(results, self._api_test(), "POST /api/x")
        code = ev["response_summary"]["status_code"]
        assert code is None or isinstance(code, int)

    def test_base_url_uses_executed_address_not_config(self, tmp_path):
        """回归（ATR-20261028-004）：描述里的 Base URL 必须是本次真实执行的地址。

        该次运行的 ``/execute`` body 传了 ``{"base_url": "https://sut.example.com"}``，
        覆盖掉了配置里带网关段的 ``…/yundt-saas-gateway``；旧实现写配置值，
        工单里的地址与实际请求不一致，排查方向直接被带偏。
        """
        trace = _write_trace(
            tmp_path,
            [_snapshot("POST", "https://sut.example.com/api/icdp/v1/x", 404, "2026-10-28T05:34:58.000Z")],
        )
        results = [{"attachments": [{"name": "trace", "path": str(trace)}]}]
        ev = self._executor()._extract_http_evidence(
            results,
            self._api_test("https://sut.example.com/yundt-saas-gateway"),
            "POST /api/icdp/v1/x",
            "https://sut.example.com",
        )
        rs = ev["request_summary"]
        assert rs["base_url"] == "https://sut.example.com"
        assert rs["base_url_source"] == "executed"
        assert rs["configured_base_url"] == "https://sut.example.com/yundt-saas-gateway"
        assert rs["execution_base_url"] == "https://sut.example.com"

    def test_fallback_base_url_is_marked_as_configured(self):
        """没有 trace 时不知道实际打到哪 —— 只能标"配置值"，不能冒充实际值。"""
        ev = self._executor()._extract_http_evidence(
            [{"status": "failed"}], self._api_test("https://sut.example.com/gw"), "POST /api/x"
        )
        rs = ev["request_summary"]
        assert rs["base_url_source"] == "configured"
        assert rs["base_url"] == "https://sut.example.com/gw"
