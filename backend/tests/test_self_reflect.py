"""执行治理层 rev34/rev35 回归：self_reflect_result.json=failed 且进程退出码 0 时的归因。

场景（评审 P0/P1）：
1. _apply_self_reflect_status：webwright 模式下脚本自评 failed → execution_result
   success 置 False（外层结果不得误报通过）；passed/无文件/非 webwright → 不改写；
2. PlaywrightRunner（HTTP 执行链）：Python 脚本退出码 0 但 self_reflect failed
   → RunnerResult.success=False（WebTestService 据此不会更新为 completed）。
"""

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from subprocess import CompletedProcess
from unittest import mock

import pytest

sys.path.insert(0, r"D:\code\Pyproject\ai-test-agent-system-platform\backend")

from app.agents.tools.web.execution_tools import _apply_self_reflect_status


def _tmp_dir():
    root = Path(tempfile.mkdtemp(prefix="self_reflect_"))
    return root


# ---------------------------------------------------------------------------
# 1. _apply_self_reflect_status（纯函数）
# ---------------------------------------------------------------------------

class TestApplySelfReflectStatus:
    def test_self_reflect_failed_overrides_success(self):
        root = _tmp_dir()
        try:
            (root / "self_reflect_result.json").write_text(
                json.dumps({"execution_status": "failed", "steps": []}), encoding="utf-8"
            )
            result = {"success": True, "return_code": 0, "report_path": str(root)}
            _apply_self_reflect_status(result, "webwright")
            assert result["success"] is False
            assert result["self_reflect_status"] == "failed"
            assert "自评 failed" in result.get("error_message", "")
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_self_reflect_passed_keeps_success(self):
        root = _tmp_dir()
        try:
            (root / "self_reflect_result.json").write_text(
                json.dumps({"execution_status": "passed"}), encoding="utf-8"
            )
            result = {"success": True, "return_code": 0, "report_path": str(root)}
            _apply_self_reflect_status(result, "webwright")
            assert result["success"] is True
            assert result["self_reflect_status"] == "passed"
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_no_self_reflect_file_keeps_success(self):
        root = _tmp_dir()
        try:
            result = {"success": True, "return_code": 0, "report_path": str(root)}
            _apply_self_reflect_status(result, "webwright")
            assert result["success"] is True
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_non_webwright_mode_ignored(self):
        root = _tmp_dir()
        try:
            (root / "self_reflect_result.json").write_text(
                json.dumps({"execution_status": "failed"}), encoding="utf-8"
            )
            result = {"success": True, "return_code": 0, "report_path": str(root)}
            _apply_self_reflect_status(result, "web_cli")
            assert result["success"] is True  # 非 webwright 不读自评
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_invalid_json_keeps_success(self):
        root = _tmp_dir()
        try:
            (root / "self_reflect_result.json").write_text("{not json", encoding="utf-8")
            result = {"success": True, "return_code": 0, "report_path": str(root)}
            _apply_self_reflect_status(result, "webwright")
            assert result["success"] is True
        finally:
            shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------------------
# 2. PlaywrightRunner：HTTP 执行链的自评归因
# ---------------------------------------------------------------------------

@pytest.mark.skipif(os.name != "nt", reason="Windows 分支（run_with_job）")
class TestRunnerSelfReflectAttribution:
    async def test_rc0_but_self_reflect_failed_is_not_success(self):
        from app.services.execution.runner import PlaywrightRunner

        ws = Path(tempfile.mkdtemp(prefix="runner_sr_"))
        try:
            script = ws / "tests" / "t.py"
            script.parent.mkdir(parents=True, exist_ok=True)
            script.write_text("print('ok')\n", encoding="utf-8")
            # 脚本自评 failed（业务步骤失败但进程退出码 0）
            (script.parent / "self_reflect_result.json").write_text(
                json.dumps({"execution_status": "failed"}), encoding="utf-8"
            )

            async def _fake_to_thread(fn, *args, **kwargs):
                return fn(*args, **kwargs)

            mock.patch("asyncio.to_thread", _fake_to_thread).start()

            def _fake_run_with_job(cmd, cwd, env, timeout, shell):
                return CompletedProcess(cmd, 0, b"ok\n", b"")  # 退出码 0

            mock.patch(
                "app.agents.tools.web.process_guard.run_with_job", _fake_run_with_job
            ).start()
            try:
                runner = PlaywrightRunner(ws)
                result = await runner.run(script, config={"reporter": "list"}, timeout=30)
                # 退出码 0 但自评 failed → 必须判定失败
                assert result.success is False
                assert "自评 failed" in (result.error_message or "")
            finally:
                mock.patch.stopall()
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    async def test_rc0_no_self_reflect_is_success(self):
        from app.services.execution.runner import PlaywrightRunner

        ws = Path(tempfile.mkdtemp(prefix="runner_sr2_"))
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
                assert result.success is True  # 无自评文件，按退出码
            finally:
                mock.patch.stopall()
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    async def test_rc_nonzero_no_self_reflect_no_unbound_local(self):
        """rev50（真实环境暴露）：rc≠0 且无 self_reflect → 失败且 error_message
        携带真实错误（此前 error_message 未初始化触发 UnboundLocalError 掩盖失败原因）。"""
        from app.services.execution.runner import PlaywrightRunner

        ws = Path(tempfile.mkdtemp(prefix="runner_sr3_"))
        try:
            script = ws / "tests" / "t3.py"
            script.parent.mkdir(parents=True, exist_ok=True)
            script.write_text("print('boom')\nimport sys; sys.exit(1)\n", encoding="utf-8")

            async def _fake_to_thread(fn, *args, **kwargs):
                return fn(*args, **kwargs)

            mock.patch("asyncio.to_thread", _fake_to_thread).start()

            def _fake_run_with_job(cmd, cwd, env, timeout, shell):
                # 预检（--version）通过；主执行非零退出码
                if any("--version" in str(c) for c in cmd):
                    return CompletedProcess(cmd, 0, b"1.49.0\n", b"")
                return CompletedProcess(cmd, 1, b"boom\n", b"")

            mock.patch(
                "app.agents.tools.web.process_guard.run_with_job", _fake_run_with_job
            ).start()
            try:
                runner = PlaywrightRunner(ws)
                result = await runner.run(script, config={"reporter": "list"}, timeout=30)
                assert result.success is False
                assert result.error_message  # 不再 UnboundLocalError
                assert "boom" in (result.error_message or "") or "returncode" in (result.error_message or "")
            finally:
                mock.patch.stopall()
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    async def test_self_reflect_stats_mapped_to_summary(self):
        """rev51（统计映射）：self_reflect 携带 total/passed/failed →
        result_summary 合并（web_test_runs 运行统计与脚本步骤数一致，如 20/20）。"""
        from app.services.execution.runner import PlaywrightRunner

        ws = Path(tempfile.mkdtemp(prefix="runner_sr4_"))
        try:
            script = ws / "tests" / "t4.py"
            script.parent.mkdir(parents=True, exist_ok=True)
            script.write_text("print('ok')\n", encoding="utf-8")
            (script.parent / "self_reflect_result.json").write_text(
                json.dumps({"execution_status": "passed", "total": 20,
                            "passed": 20, "failed": 0, "skipped": 0}),
                encoding="utf-8")

            async def _fake_to_thread(fn, *args, **kwargs):
                return fn(*args, **kwargs)

            mock.patch("asyncio.to_thread", _fake_to_thread).start()

            def _fake_run_with_job(cmd, cwd, env, timeout, shell):
                if any("--version" in str(c) for c in cmd):
                    return CompletedProcess(cmd, 0, b"1.49.0\n", b"")
                return CompletedProcess(cmd, 0, b"ok\n", b"")

            mock.patch(
                "app.agents.tools.web.process_guard.run_with_job", _fake_run_with_job
            ).start()
            try:
                runner = PlaywrightRunner(ws)
                result = await runner.run(script, config={"reporter": "list"}, timeout=30)
                assert result.success is True
                assert result.result_summary.get("total") == 20
                assert result.result_summary.get("passed") == 20
                assert result.result_summary.get("failed") == 0
            finally:
                mock.patch.stopall()
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    async def test_self_reflect_stats_failed_mapped_and_rejected(self):
        """rev51：self_reflect failed + 统计（18/20）→ 判定失败且 failed 统计映射。"""
        from app.services.execution.runner import PlaywrightRunner

        ws = Path(tempfile.mkdtemp(prefix="runner_sr5_"))
        try:
            script = ws / "tests" / "t5.py"
            script.parent.mkdir(parents=True, exist_ok=True)
            script.write_text("print('partial')\n", encoding="utf-8")
            (script.parent / "self_reflect_result.json").write_text(
                json.dumps({"execution_status": "failed", "total": 20,
                            "passed": 18, "failed": 2, "skipped": 0}),
                encoding="utf-8")

            async def _fake_to_thread(fn, *args, **kwargs):
                return fn(*args, **kwargs)

            mock.patch("asyncio.to_thread", _fake_to_thread).start()

            def _fake_run_with_job(cmd, cwd, env, timeout, shell):
                if any("--version" in str(c) for c in cmd):
                    return CompletedProcess(cmd, 0, b"1.49.0\n", b"")
                return CompletedProcess(cmd, 0, b"partial\n", b"")

            mock.patch(
                "app.agents.tools.web.process_guard.run_with_job", _fake_run_with_job
            ).start()
            try:
                runner = PlaywrightRunner(ws)
                result = await runner.run(script, config={"reporter": "list"}, timeout=30)
                assert result.success is False  # 自评 failed 即使退出码 0
                assert "自评 failed" in (result.error_message or "")
                assert result.result_summary.get("failed") == 2
                assert result.result_summary.get("passed") == 18
            finally:
                mock.patch.stopall()
        finally:
            shutil.rmtree(ws, ignore_errors=True)
