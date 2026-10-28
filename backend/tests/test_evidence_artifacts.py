# -*- coding: utf-8 -*-
"""失败证据归档（P1-2）：trace/截图上传对象存储，缺陷描述挂可点开的预签名链接。

背景：历史上缺陷描述里的"测试报告地址"是 ``/api/v2/test-runs/{run_id}/report``
（相对路径 + 需要平台登录态），拿到工单的人点不开；Playwright trace 更是随
``TemporaryDirectory`` 一起被删，等于证据根本没留下。

实测前提（2026-10-28）：预签名 URL 匿名 GET 返回 200；裸对象地址匿名访问不通（桶私有），
所以链接只能走 presign，且**必须写明 7 天有效期**（S3 协议上限）。
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from app.services import evidence_artifacts as ea
from app.services.defect_decision_service import DefectDecisionResult, DefectPriority
from app.services.idp_defect_service import IDPDefectService


# ---------------------------------------------------------------------------
# 1. 收集规则
# ---------------------------------------------------------------------------

def _mk_workdir(tmp_path: Path) -> Path:
    work = tmp_path / "work"
    # 平台实际执行用 --output=<work_dir>/allure-results，trace 落在这里
    ar = work / "allure-results" / "case-a"
    ar.mkdir(parents=True)
    (ar / "trace.zip").write_bytes(b"PK\x03\x04trace")
    (ar / "screenshot.png").write_bytes(b"\x89PNG")
    (work / "allure-results" / "case-b").mkdir(parents=True)
    (work / "allure-results" / "case-b" / "trace.zip").write_bytes(b"PK\x03\x04trace2")
    # 不该被收：非 trace 的 zip、无诊断价值的 json
    (ar / "attachments.zip").write_bytes(b"PK\x03\x04noise")
    (work / "allure-results" / "results.json").write_text("{}", encoding="utf-8")
    return work


def test_collects_from_allure_results_output_dir(tmp_path):
    """平台用 --output=allure-results，只扫 test-results/ 会一个证据都收不到。"""
    files = ea.collect_evidence_files(_mk_workdir(tmp_path))
    assert sorted(f.name for f in files) == ["screenshot.png", "trace.zip", "trace.zip"]


def test_collect_still_supports_default_test_results_dir(tmp_path):
    """将来若去掉 --output，trace 会回到 test-results/，这里要继续收得到。"""
    work = tmp_path / "work2"
    d = work / "test-results" / "case-x"
    d.mkdir(parents=True)
    (d / "trace.zip").write_bytes(b"PK\x03\x04x")
    assert [f.name for f in ea.collect_evidence_files(work)] == ["trace.zip"]


def test_collect_caps_file_count(tmp_path, monkeypatch):
    monkeypatch.setattr(ea, "MAX_EVIDENCE_FILES", 2)
    assert len(ea.collect_evidence_files(_mk_workdir(tmp_path))) == 2


def test_collect_returns_empty_when_all_passed(tmp_path):
    """全通过的 run 不落 trace，这里必须安静返回空（不能报错、不能真去建目录）。"""
    work = tmp_path / "work"
    work.mkdir()
    assert ea.collect_evidence_files(work) == []


# ---------------------------------------------------------------------------
# 2. 上传与链接
# ---------------------------------------------------------------------------

class _FakeMinio:
    def __init__(self):
        self.uploaded: list[str] = []
        self.fail_on: set[str] = set()

    def upload_bytes(self, object_name, data, content_type="application/octet-stream"):
        if any(token in object_name for token in self.fail_on):
            raise RuntimeError("boom")
        self.uploaded.append(object_name)
        return object_name

    def get_presigned_url(self, object_name, expires=None):
        return f"http://114.55.110.60:9000/test-management/{object_name}?X-Amz-Signature=x"


@pytest.fixture()
def fake_minio(monkeypatch):
    fake = _FakeMinio()
    monkeypatch.setattr(ea.MinIOClient, "upload_bytes", staticmethod(fake.upload_bytes))
    monkeypatch.setattr(
        ea.MinIOClient, "get_presigned_url", staticmethod(fake.get_presigned_url)
    )
    return fake


def test_upload_returns_trace_and_screenshot_urls(tmp_path, fake_minio):
    out = ea.upload_run_evidence("run-1", _mk_workdir(tmp_path))
    assert out["trace_url"] and out["trace_url"].startswith("http://")
    assert out["screenshot_url"] and out["screenshot_url"].endswith("screenshot.png?X-Amz-Signature=x")
    assert out["expires_days"] == ea.PRESIGN_DAYS
    # 对象名必须带 run_id 前缀，便于按运行清理
    assert all(o.startswith("api-test-evidence/run-1/") for o in fake_minio.uploaded)


def test_single_file_failure_does_not_kill_others(tmp_path, fake_minio):
    fake_minio.fail_on = {"screenshot.png"}
    out = ea.upload_run_evidence("run-2", _mk_workdir(tmp_path))
    assert out["trace_url"] is not None
    assert out["screenshot_url"] is None


def test_presign_failure_yields_no_link(tmp_path, monkeypatch):
    """预签名失败时**不能**返回坏链接——工单里写个 403 的地址比不写更糟。"""
    monkeypatch.setattr(
        ea.MinIOClient, "upload_bytes", staticmethod(lambda **kw: kw["object_name"])
    )

    def _boom(*a, **kw):
        raise RuntimeError("sign failed")

    monkeypatch.setattr(ea.MinIOClient, "get_presigned_url", staticmethod(_boom))
    out = ea.upload_run_evidence("run-3", _mk_workdir(tmp_path))
    assert out["items"] and all(item["url"] is None for item in out["items"])
    assert out["trace_url"] is None
    assert ea.evidence_url_map(out) == {}


def test_oversize_file_skipped(tmp_path, fake_minio, monkeypatch):
    monkeypatch.setattr(ea, "MAX_EVIDENCE_BYTES", 5)
    out = ea.upload_run_evidence("run-4", _mk_workdir(tmp_path))
    # trace（9/10 字节）超限被跳过；截图 4 字节仍在限内
    assert [i["kind"] for i in out["items"]] == ["screenshot"]


def test_evidence_url_map_merges_screenshots():
    evidence = {
        "items": [
            {"kind": "trace", "url": "http://t"},
            {"kind": "screenshot", "url": "http://s1"},
            {"kind": "screenshot", "url": "http://s2"},
        ]
    }
    assert ea.evidence_url_map(evidence) == {
        "trace": "http://t",
        "screenshot": "http://s1",
        "screenshot_2": "http://s2",
    }


# ---------------------------------------------------------------------------
# 3. 描述渲染
# ---------------------------------------------------------------------------

def _decision() -> DefectDecisionResult:
    return DefectDecisionResult(
        decision="create", priority=DefectPriority.HIGH,
        priority_code="priority-1", priority_id=1,
        reason="r", error_type="assertion_failed", failure_summary="接口返回 500",
    )


def _desc(**kw) -> list[dict]:
    raw = IDPDefectService._generate_description(
        decision=_decision(),
        scenario_name="case",
        endpoint="/api/x",
        method="POST",
        request_summary={"base_url": "https://example.com", "body": {"a": 1}},
        response_summary={"status_code": 500, "body": {"err": "boom"}},
        reqid="N/A",
        test_run_id=__import__("uuid").uuid4(),
        test_case_id=None,
        report_url=kw.get("report_url"),
        source_project_key="PR-2",
        source_type="api",
        evidence_urls=kw.get("evidence_urls"),
    )
    return json.loads(raw)


def test_description_renders_clickable_trace_link():
    ops = _desc(evidence_urls={"trace": "http://minio/trace.zip?X-Amz=1"})
    assert any(op.get("insert") == "证据附件\n" for op in ops)
    link_ops = [op for op in ops if (op.get("attributes") or {}).get("link")]
    assert link_ops, "证据链接必须带 link 属性，裸 URL 在部分客户端点不开"
    assert link_ops[0]["insert"].startswith("http://minio/trace.zip")
    # 必须说清"会过期"以及"过期后怎么取回"（对象长期保留，链接现签）
    assert any("7 天" in op.get("insert", "") for op in ops)
    assert any("重新签发" in op.get("insert", "") for op in ops)


def test_description_report_url_is_clickable():
    ops = _desc(report_url="http://minio/report.zip?X-Amz=1")
    link_ops = [op for op in ops if (op.get("attributes") or {}).get("link")]
    assert [op["insert"] for op in link_ops] == ["http://minio/report.zip?X-Amz=1"]


def test_description_without_evidence_has_no_section():
    ops = _desc()
    assert not any(op.get("insert") == "证据附件\n" for op in ops)


# ---------------------------------------------------------------------------
# 4. 链路透传（漏传 = 证据白归档）
# ---------------------------------------------------------------------------

def test_executor_uploads_before_tempdir_destroyed_and_passes_links():
    from app.services import api_test_executor as mod

    src = inspect.getsource(mod.APITestExecutor._execute_in_background_locked)
    assert "upload_run_evidence" in src
    # 归档必须排在 process_test_results 之前（temp 目录在 with 退出时销毁）
    assert src.index("upload_run_evidence") < src.index("_process_test_results")
    assert "evidence_urls=evidence_urls" in src


def test_registration_service_forwards_evidence_urls():
    from app.services import defect_registration_service as mod

    src = inspect.getsource(mod.DefectRegistrationService.register_from_api_failure)
    assert "evidence_urls" in inspect.signature(
        mod.DefectRegistrationService.register_from_api_failure
    ).parameters
    assert "evidence_urls=evidence_urls" in src


def test_idp_service_forwards_evidence_urls_to_description():
    src = inspect.getsource(IDPDefectService.process_test_failure)
    assert "evidence_urls" in inspect.signature(
        IDPDefectService.process_test_failure
    ).parameters
    assert "evidence_urls=evidence_urls" in src
