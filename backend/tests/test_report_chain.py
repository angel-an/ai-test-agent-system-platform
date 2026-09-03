"""执行治理层 rev37：报告链（index.html / Python report_path / service 集成）回归。

覆盖评审 P1：
1. _build_webwright_index_html 截图路径（screenshots/ 子目录优先 + 根兜底）；
2. PlaywrightRunner Python 脚本（含 self_reflect_result.json）设置 report_path——
   HTTP 报告链可达（此前 Python 分支固定 report_path=None）；
3. WebTestService.run_web_test 报告链集成：runner 返回 Python report_path →
   生成 index.html + 上传 ZIP + 创建 WEB_TEST_REPORT 附件。
"""

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from subprocess import CompletedProcess
from types import SimpleNamespace
from unittest import mock

import pytest

sys.path.insert(0, r"D:\code\Pyproject\ai-test-agent-system-platform\backend")

from app.services.web_test_service import _build_webwright_index_html


def _tmp_dir(prefix="report_chain_"):
    return Path(tempfile.mkdtemp(prefix=prefix))


# ---------------------------------------------------------------------------
# 1. index.html 截图路径
# ---------------------------------------------------------------------------

class TestBuildWebwrightIndexHtml:
    def test_screenshot_in_screenshots_subdir(self):
        root = _tmp_dir()
        try:
            (root / "screenshots").mkdir()
            (root / "screenshots" / "01_step.png").write_bytes(b"png")
            (root / "self_reflect_result.json").write_text(
                json.dumps({"execution_status": "passed", "run_name": "r",
                            "steps": [], "screenshots": ["01_step"]}),
                encoding="utf-8")
            idx = _build_webwright_index_html(root)
            assert idx is not None and idx.exists()
            html = idx.read_text(encoding="utf-8")
            assert "screenshots/01_step.png" in html  # 子目录路径
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_screenshot_at_root_fallback(self):
        root = _tmp_dir()
        try:
            (root / "01_step.png").write_bytes(b"png")
            (root / "self_reflect_result.json").write_text(
                json.dumps({"execution_status": "passed", "run_name": "r",
                            "steps": [], "screenshots": ["01_step"]}),
                encoding="utf-8")
            idx = _build_webwright_index_html(root)
            html = idx.read_text(encoding="utf-8")
            assert "src='01_step.png'" in html  # 根目录兜底（无前缀）
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_missing_screenshot_omitted(self):
        root = _tmp_dir()
        try:
            (root / "self_reflect_result.json").write_text(
                json.dumps({"execution_status": "failed", "run_name": "r",
                            "steps": [], "screenshots": ["missing"]}),
                encoding="utf-8")
            idx = _build_webwright_index_html(root)
            html = idx.read_text(encoding="utf-8")
            assert "missing.png" not in html  # 文件不存在则不生成 img
        finally:
            shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------------------
# 2. PlaywrightRunner：Python 脚本设置 report_path（报告链可达）
# ---------------------------------------------------------------------------

@pytest.mark.skipif(os.name != "nt", reason="Windows 分支（run_with_job）")
class TestRunnerPythonReportPath:
    async def test_python_script_with_self_reflect_sets_report_path(self):
        from app.services.execution.runner import PlaywrightRunner

        ws = _tmp_dir("runner_rp_")
        try:
            script = ws / "tests" / "t.py"
            script.parent.mkdir(parents=True, exist_ok=True)
            script.write_text("print('ok')\n", encoding="utf-8")
            (script.parent / "self_reflect_result.json").write_text(
                json.dumps({"execution_status": "passed"}), encoding="utf-8")

            async def _fake_to_thread(fn, *args, **kwargs):
                return fn(*args, **kwargs)

            mock.patch("asyncio.to_thread", _fake_to_thread).start()

            def _fake_run_with_job(cmd, cwd, env, timeout, shell):
                return CompletedProcess(cmd, 0, b"ok\n", b"")

            mock.patch(
                "app.agents.tools.web.process_guard.run_with_job", _fake_run_with_job
            ).start()
            try:
                runner = PlaywrightRunner(ws)
                result = await runner.run(script, config={"reporter": "list"}, timeout=30)
                # 含 self_reflect → report_path 应为脚本目录（报告链可达）
                assert result.report_path == str(script.parent)
            finally:
                mock.patch.stopall()
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    async def test_python_subdir_report_path_excludes_workspace_history(self):
        """rev53（报告目录隔离）：脚本在每次 run 专属子目录 + workspace 根有历史
        残留 → report_path 指向**脚本子目录**（报告打包不含 workspace 根历史）。"""
        from app.services.execution.runner import PlaywrightRunner

        ws = _tmp_dir("runner_rp3_")
        try:
            # workspace 根历史残留（大文件，不应进入报告目录）
            (ws / "historical_big.png").write_bytes(b"x" * 1024 * 1024)
            (ws / "old_log.log").write_text("old", encoding="utf-8")
            # 脚本位于每次 run 的专属子目录（HTTP 链 rev53 布局）
            script = ws / "tests" / "run_abc12345" / "run.py"
            script.parent.mkdir(parents=True, exist_ok=True)
            script.write_text("print('ok')\n", encoding="utf-8")
            (script.parent / "self_reflect_result.json").write_text(
                json.dumps({"execution_status": "passed", "total": 1,
                            "passed": 1, "failed": 0}), encoding="utf-8")

            async def _fake_to_thread(fn, *args, **kwargs):
                return fn(*args, **kwargs)

            mock.patch("asyncio.to_thread", _fake_to_thread).start()

            def _fake_run_with_job(cmd, cwd, env, timeout, shell):
                # 预检（playwright --version，cwd=workspace 根）与主执行区分
                if any("--version" in str(c) for c in cmd):
                    return CompletedProcess(cmd, 0, b"1.49.0\n", b"")
                # 主执行（python 脚本）：断言 cwd = 脚本目录（产物隔离的关键）
                assert cwd == str(script.parent), f"cwd 应为脚本目录: {cwd}"
                return CompletedProcess(cmd, 0, b"ok\n", b"")

            mock.patch(
                "app.agents.tools.web.process_guard.run_with_job", _fake_run_with_job
            ).start()
            try:
                runner = PlaywrightRunner(ws)
                result = await runner.run(script, config={"reporter": "list"}, timeout=30)
                assert result.report_path == str(script.parent)  # 专属子目录，非 workspace 根
            finally:
                mock.patch.stopall()
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    async def test_python_script_without_self_reflect_no_report_path(self):
        from app.services.execution.runner import PlaywrightRunner

        ws = _tmp_dir("runner_rp2_")
        try:
            script = ws / "tests" / "t2.py"
            script.parent.mkdir(parents=True, exist_ok=True)
            script.write_text("print('ok')\n", encoding="utf-8")

            async def _fake_to_thread(fn, *args, **kwargs):
                return fn(*args, **kwargs)

            mock.patch("asyncio.to_thread", _fake_to_thread).start()

            def _fake_run_with_job(cmd, cwd, env, timeout, shell):
                return CompletedProcess(cmd, 0, b"ok\n", b"")

            mock.patch(
                "app.agents.tools.web.process_guard.run_with_job", _fake_run_with_job
            ).start()
            try:
                runner = PlaywrightRunner(ws)
                result = await runner.run(script, config={"reporter": "list"}, timeout=30)
                assert result.report_path is None  # 无自评产物 → 无报告链
            finally:
                mock.patch.stopall()
        finally:
            shutil.rmtree(ws, ignore_errors=True)


# ---------------------------------------------------------------------------
# 3. WebTestService.run_web_test 报告链集成
# ---------------------------------------------------------------------------

class TestWebTestServiceReportChain:
    async def test_python_run_creates_index_zip_attachment(self, monkeypatch):
        from app.config.minio_client import MinIOClient
        from app.services.web_test_service import WebTestService

        ws = _tmp_dir("svc_chain_")
        report_dir = ws / "run_out"
        try:
            report_dir.mkdir()
            (report_dir / "screenshots").mkdir()
            (report_dir / "screenshots" / "01_step.png").write_bytes(b"pngdata")
            (report_dir / "self_reflect_result.json").write_text(
                json.dumps({"execution_status": "failed", "run_name": "r",
                            "sub_function": "sf", "steps": [
                                {"name": "保存广告投放", "ok": False, "detail": "x"}],
                            "screenshots": ["01_step"]}),
                encoding="utf-8")

            runner_result = SimpleNamespace(
                success=False, stdout="out", stderr="", returncode=0,
                report_path=str(report_dir), duration_ms=100,
                error_message="保存广告投放失败（平台校验拦截）",
                result_summary={"total": 10, "passed": 9, "failed": 1},
            )

            class _FakeRunner:
                def __init__(self, workspace_root):
                    pass

                async def run(self, script_path, config=None, timeout=600):
                    return runner_result

            # 构造 service（mock 依赖）
            class _Session:
                def __init__(self):
                    self.added = []

                async def commit(self):
                    pass

                async def get(self, model, pk):
                    return None

                async def flush(self):
                    # 模拟 flush：为待插入对象生成 id
                    import uuid as _uuid

                    for obj in self.added:
                        if getattr(obj, "id", None) is None:
                            obj.id = _uuid.uuid4()

                def add(self, obj):
                    self.added.append(obj)

            session = _Session()
            svc = WebTestService.__new__(WebTestService)
            svc.session = session
            project = SimpleNamespace(id="00000000-0000-0000-0000-0000000000a1",
                                      identifier="PR-1")
            web_test = SimpleNamespace(
                id="00000000-0000-0000-0000-0000000000b1",
                project_id="00000000-0000-0000-0000-0000000000a1",
                sub_function_id="00000000-0000-0000-0000-0000000000c1",
                identifier="WT-1", name="t", base_url="",
                script_path="obj/t.py", script_format="playwright",
                script_language="python",
            )
            test_run = SimpleNamespace(id="00000000-0000-0000-0000-0000000000d1",
                                       identifier="WTR-1")
            svc.web_test_repo = SimpleNamespace(
                get_by_id=mock.AsyncMock(return_value=web_test))
            svc.project_repo = SimpleNamespace(
                get_by_identifier=mock.AsyncMock(return_value=project))
            svc.web_test_run_repo = SimpleNamespace(
                create=mock.AsyncMock(return_value=test_run),
                update=mock.AsyncMock())
            svc.web_test_result_repo = SimpleNamespace()

            monkeypatch.setattr(
                "app.services.web_test_service.MinIOClient.download_file",
                mock.Mock(return_value=b"print('x')\n"))
            monkeypatch.setattr(MinIOClient, "download_file",
                                mock.Mock(return_value=b"print('x')\n"))
            upload_mock = mock.Mock()
            monkeypatch.setattr(MinIOClient, "upload_bytes", upload_mock)
            from app.config.settings import settings

            monkeypatch.setattr(settings, "web_cli_workspace_root", str(ws))
            monkeypatch.setattr(
                "app.agents.tools.web.script_provenance.authorize_script_execution",
                mock.AsyncMock(return_value=(True, "ok")))
            monkeypatch.setattr(
                "app.services.execution.runner.PlaywrightRunner", _FakeRunner)

            r = await svc.run_web_test(project_identifier="PR-1",
                                       web_test_id="00000000-0000-0000-0000-0000000000b1")
            assert r.get("status") == "failed"  # runner_result.success=False
            # index.html 生成
            assert (report_dir / "index.html").exists()
            idx_html = (report_dir / "index.html").read_text(encoding="utf-8")
            assert "screenshots/01_step.png" in idx_html
            # 附件创建（session.add 捕获 WEB_TEST_REPORT）
            report_atts = [a for a in session.added
                           if getattr(a, "entity_type", None) is not None
                           and "WEB_TEST_REPORT" in str(a.entity_type)]
            assert len(report_atts) == 1
            att = report_atts[0]
            assert "zip" in att.content_type
            assert att.object_name.endswith("report.zip")
            # rev37（评审口径 2）：附件 file_size 必须等于实际上传的 ZIP 字节数
            uploaded_size = upload_mock.call_args.kwargs.get("data", b"").__len__() if upload_mock.called else 0
            assert att.file_size == uploaded_size
            assert uploaded_size > 0
            # 运行记录 update 收到附件 ID
            _, kwargs = svc.web_test_run_repo.update.call_args
            assert kwargs.get("report_path") == str(att.id)
            # ZIP 上传被调用
            assert upload_mock.called
        finally:
            shutil.rmtree(ws, ignore_errors=True)


class TestWebSubFunctionArtifactQuery:
    async def test_includes_reports_attached_to_web_test_runs(self):
        from uuid import UUID

        from app.models.attachment import AttachmentEntityType
        from app.services.web_function_service import WebFunctionService

        sub_function_id = UUID("00000000-0000-0000-0000-0000000000c1")
        direct = SimpleNamespace(
            id=UUID("00000000-0000-0000-0000-0000000000c2"),
            entity_type=AttachmentEntityType.WEB_TEST_CASE,
            file_name="test-cases.json",
            description="cases",
            file_size=10,
            content_type="application/json",
            object_name="web-tests/PR-2/sub-functions/test-cases.json",
            created_at=None,
        )
        run_report = SimpleNamespace(
            id=UUID("00000000-0000-0000-0000-0000000000c3"),
            entity_type=AttachmentEntityType.WEB_TEST_REPORT,
            file_name="report.zip",
            description="report",
            file_size=20,
            content_type="application/zip",
            object_name="web-test-reports/PR-2/run/report.zip",
            created_at=None,
        )

        class _Result:
            def scalars(self):
                return self

            def all(self):
                return [direct, run_report]

        class _Session:
            statement = ""

            async def execute(self, stmt):
                self.statement = str(stmt)
                return _Result()

        session = _Session()
        service = WebFunctionService.__new__(WebFunctionService)
        service.session = session
        service.web_sub_function_repo = SimpleNamespace(
            get_by_id=mock.AsyncMock(return_value=SimpleNamespace(id=sub_function_id))
        )

        result = await service.get_sub_function_artifacts(str(sub_function_id))

        assert result["total"] == 2
        assert {item["type"] for item in result["artifacts"]} == {
            "web_test_case",
            "web_test_report",
        }
        assert "web_test_runs" in session.statement
        assert "web_tests" in session.statement
