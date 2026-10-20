# -*- coding: utf-8 -*-
"""测试运行保留策略回归（2026-09-29 客观复核第 5 项）。

要求（复核口径）：
1. 归档**不删除**运行/作业，只把大体积 stdout/stderr 降级为结构化摘要；
2. 只处理**终态**运行，new_run / in_progress / under_review 永不触碰；
3. 幂等：已归档的作业不重复处理；
4. 磁盘清理只删"未被作业 report_path 引用 + 超过保留期"的目录，且限制在工作目录内；
5. 证据摘要必须保留结论（断言分布 / 覆盖率 / 环境阻断标记），否则等于丢证据。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from app.services.run_retention_service import (
    TERMINAL_RUN_STATES,
    apply_disk_retention_plan,
    apply_retention_plan,
    build_disk_retention_plan,
    build_retention_plan,
    extract_evidence,
)

NOW = datetime(2026, 9, 29, tzinfo=timezone.utc)


def _stdout() -> str:
    return "\n".join([
        "Running 1 test using 1 worker",
        'COMPILED_ASSERTION {"name":"required_error:Case Title","outcome":"pass","observed":"出现必填校验"}',
        'COMPILED_ASSERTION {"name":"select_options:Brand [ETZ]","outcome":"unverified","observed":"未观测到合法选项 ETZ"}',
        'COMPILED_ASSERTION {"name":"upload_file:附件","outcome":"blocked","observed":"多部分请求被阻断"}',
        'COMPILED_ASSERTION_SUMMARY {"total":3,"pass":1,"unverified":1,"blocked":1}',
        'COMPILED_COVERAGE {"cases":4,"assertions":8}',
        'UPLOAD_EVIDENCE {"kind":"format"}',
        "ENVIRONMENT_BLOCKED request blocked by WAF",
        "  1 passed (55.0s)",
    ])


def _run(identifier: str, state: str, created: datetime, project_id=None):
    return SimpleNamespace(
        id=uuid4(), identifier=identifier, run_state=state,
        created_at=created, project_id=project_id or uuid4(),
    )


def _job(run, *, stdout: str | None = None, stderr: str = "", report_path: str | None = None,
         result_summary: dict | None = None):
    return SimpleNamespace(
        id=uuid4(), test_run_id=run.id, script_identifier=f"{run.identifier}-J1",
        stdout=stdout, stderr=stderr, report_path=report_path,
        result_summary=result_summary,
    )


class TestEvidenceExtraction:
    def test_evidence_keeps_conclusions(self):
        evidence = extract_evidence(_stdout())
        assert evidence["assertions"] == 3
        assert evidence["assertion_outcomes"] == {"pass": 1, "unverified": 1, "blocked": 1}
        assert evidence["summary"] == {"total": 3, "pass": 1, "unverified": 1, "blocked": 1}
        assert evidence["coverage"] == {"cases": 4, "assertions": 8}
        assert evidence["upload_evidence"] == 1
        assert evidence["environment_blocked"] is True
        assert evidence["stdout_chars"] == len(_stdout())

    def test_broken_evidence_line_does_not_raise(self):
        evidence = extract_evidence('COMPILED_ASSERTION {not json}\nCOMPILED_COVERAGE {"cases":1}')
        assert evidence["assertions"] == 0
        assert evidence["coverage"] == {"cases": 1}

    def test_evidence_never_keeps_observed_text(self):
        """证据最小化（复核意见）：只留断言名称与结构化计数，不留 observed 原文。"""
        stdout = (
            'COMPILED_ASSERTION {"name":"default_selected:Brand=YL","outcome":"pass",'
            '"observed":"默认显示值=YL（期望 YL）","rule_id":"FR-1","case_id":"TC-1"}\n'
            'COMPILED_ASSERTION_SUMMARY {"total":1,"pass":1,"unverified":0,"blocked":0,'
            '"submit_phase":{"queued":0},"skipped_by_reason":{},"plan_checks":1,'
            '"未知敏感字段":"secret-value"}\n'
            'COMPILED_COVERAGE {"cases":1,"assertions":1,"sample_rule_ids":["FR-1"],'
            '"未知字段":"secret-value"}\n'
        )
        evidence = extract_evidence(stdout)
        blob = json.dumps(evidence, ensure_ascii=False)
        assert "默认显示值" not in blob            # observed 原文不得保留
        assert "secret-value" not in blob          # 白名单外字段不得带进来
        assert evidence["assertion_sample"] == ["default_selected:Brand=YL"]
        assert evidence["summary"]["total"] == 1
        assert evidence["coverage"]["sample_rule_ids"] == ["FR-1"]
        assert "evidence_policy" in evidence

    def test_evidence_sample_is_bounded(self):
        stdout = "\n".join(
            'COMPILED_ASSERTION {"name":"%s","outcome":"pass","observed":"v%d"}' % ("x" * 200, i)
            for i in range(9)
        )
        evidence = extract_evidence(stdout)
        assert evidence["assertions"] == 9
        assert len(evidence["assertion_sample"]) == 5
        assert all(len(item) <= 80 for item in evidence["assertion_sample"])


class TestRetentionPlanning:
    def test_only_terminal_runs_are_selected(self):
        old = NOW - timedelta(days=30)
        done = _run("TR-DONE", "done", old)
        rejected = _run("TR-REJ", "rejected", old)
        running = _run("TR-RUN", "in_progress", old)
        fresh = _run("TR-NEW", "new_run", old)
        jobs = [_job(r, stdout=_stdout()) for r in (done, rejected, running, fresh)]
        plan = build_retention_plan(jobs=jobs, runs=[done, rejected, running, fresh],
                                    now=NOW, older_than_days=14)
        assert plan.run_identifiers == ["TR-DONE", "TR-REJ"]
        assert plan.skipped["not_terminal"] == 2
        assert TERMINAL_RUN_STATES >= {"done", "rejected", "closed"}

    def test_recent_runs_are_not_touched(self):
        recent = _run("TR-RECENT", "done", NOW - timedelta(days=1))
        plan = build_retention_plan(runs=[recent], jobs=[_job(recent, stdout=_stdout())],
                                    now=NOW, older_than_days=14)
        assert plan.actions == []
        assert plan.skipped["too_new"] == 1

    def test_explicit_run_identifiers_win(self):
        old = NOW - timedelta(days=30)
        a, b = _run("TR-A", "done", old), _run("TR-B", "done", old)
        plan = build_retention_plan(runs=[a, b], jobs=[_job(a, stdout=_stdout()), _job(b, stdout=_stdout())],
                                    now=NOW, run_identifiers=["TR-B"])
        assert plan.run_identifiers == ["TR-B"]
        assert [act.run_identifier for act in plan.actions] == ["TR-B"]

    def test_keep_latest_per_project_protects_newest(self):
        old = NOW - timedelta(days=30)
        project = uuid4()
        newest = _run("TR-3", "done", NOW - timedelta(days=20), project_id=project)
        middle = _run("TR-2", "done", NOW - timedelta(days=25), project_id=project)
        oldest = _run("TR-1", "done", old, project_id=project)
        runs = [oldest, middle, newest]
        plan = build_retention_plan(
            runs=runs, jobs=[_job(r, stdout=_stdout()) for r in runs],
            now=NOW, keep_latest_per_project=1,
        )
        assert plan.run_identifiers == ["TR-1", "TR-2"]
        assert plan.skipped["kept_latest"] == 1

    def test_already_archived_is_idempotent(self):
        old = NOW - timedelta(days=30)
        run = _run("TR-A", "done", old)
        job = _job(run, stdout=_stdout(), result_summary={"retention": {"archived_at": "x"}})
        plan = build_retention_plan(runs=[run], jobs=[job], now=NOW)
        assert plan.actions == []
        assert plan.skipped["already_archived"] == 1

    def test_jobs_without_output_are_skipped(self):
        old = NOW - timedelta(days=30)
        run = _run("TR-A", "done", old)
        plan = build_retention_plan(runs=[run], jobs=[_job(run, stdout=None, stderr="")], now=NOW)
        assert plan.actions == []
        assert plan.skipped["empty_output"] == 1

    def test_run_identifiers_cannot_reach_other_projects(self):
        """复核意见：run_identifiers 只能在**传入的候选运行**里命中（服务层只在项目内取候选）。"""
        old = NOW - timedelta(days=30)
        mine = _run("TR-MINE", "done", old, project_id=uuid4())
        plan = build_retention_plan(
            runs=[mine],                      # 候选只有本项目运行
            jobs=[_job(mine, stdout=_stdout())],
            now=NOW,
            run_identifiers=["TR-OTHER-PROJECT"],
        )
        assert plan.actions == []
        assert plan.run_identifiers == []
        assert plan.skipped["not_selected"] == 1


class TestRetentionApply:
    def test_apply_keeps_run_and_stores_evidence(self):
        old = NOW - timedelta(days=30)
        run = _run("TR-A", "done", old)
        job = _job(run, stdout=_stdout(), stderr="warn", result_summary={"total": 1})
        plan = build_retention_plan(runs=[run], jobs=[job], now=NOW)
        stats = apply_retention_plan(plan, archived_at="2026-09-29T00:00:00+00:00")

        assert stats["archived_jobs"] == 1
        assert job.stdout is None and job.stderr is None      # 大体积原文已清理
        assert job.result_summary["total"] == 1               # 既有字段保留
        retention = job.result_summary["retention"]
        assert retention["archived_at"] == "2026-09-29T00:00:00+00:00"
        assert retention["evidence"]["assertion_outcomes"]["unverified"] == 1
        assert retention["evidence"]["environment_blocked"] is True
        assert run.identifier == "TR-A"                       # 运行本身不删

    def test_second_apply_is_noop(self):
        old = NOW - timedelta(days=30)
        run = _run("TR-A", "done", old)
        job = _job(run, stdout=_stdout())
        apply_retention_plan(build_retention_plan(runs=[run], jobs=[job], now=NOW))
        again = build_retention_plan(runs=[run], jobs=[job], now=NOW)
        assert again.actions == []


class TestEvidenceStructureCompatibility:
    """旧/新 evidence 结构兼容（复核意见第 2 项）。

    TR-84 用**旧结构**归档（无 ``evidence_policy``、可能残留未知键）；此后新归档的
    evidence 带 ``evidence_policy`` 且字段经白名单裁剪。读取方（作业列表/详情序列化、
    报告预览的 result_summary 读取、再次归档的幂等判断）必须对两种结构都安全。
    """

    #: 旧结构特征：无 evidence_policy，且允许残留白名单前的未知键
    OLD_EVIDENCE = {
        "assertions": 3,
        "assertion_outcomes": {"pass": 2, "unverified": 1, "blocked": 0},
        "assertion_sample": ["rule_a:Case A", "rule_b:Case B"],
        "summary": {"total": 3, "pass": 2, "unverified": 1, "blocked": 0},
        "stdout_chars": 1234,
        "legacy_extra": "旧结构可能带白名单前的字段，读取方不得因此报错",
    }

    def _archived(self, evidence: dict):
        old = NOW - timedelta(days=30)
        run = _run("TR-OLD", "done", old)
        summary = {"total": 3, "passed": 2, "retention": {
            "archived_at": "2026-09-28T00:00:00+00:00",
            "reason": "历史运行归档",
            "evidence": evidence,
            "stderr_chars": 0,
        }}
        job = _job(run, stdout=None, stderr="", result_summary=summary)
        return run, job

    def test_old_and_new_evidence_pass_job_info_schema(self):
        """列表/详情序列化（TestRunScriptJobInfo）对两种结构都能读出。"""
        from app.schemas.enums import ScriptType
        from app.schemas.test_run import TestRunScriptJobInfo

        _, old_job = self._archived(dict(self.OLD_EVIDENCE))
        new_evidence = extract_evidence(_stdout())
        _, new_job = self._archived(new_evidence)

        base = dict(
            id=uuid4(), test_run_id=uuid4(),
            script_type=ScriptType.API_TEST, script_id=uuid4(),
            created_at=NOW,
        )
        old_info = TestRunScriptJobInfo(result_summary=old_job.result_summary, **base)
        new_info = TestRunScriptJobInfo(result_summary=new_job.result_summary, **base)
        assert "evidence_policy" not in old_info.result_summary["retention"]["evidence"]
        assert "evidence_policy" in new_info.result_summary["retention"]["evidence"]
        # 旧结构残留的未知键原样透出（只读不改写）
        assert "legacy_extra" in old_info.result_summary["retention"]["evidence"]

    def test_report_style_reads_work_on_both_structures(self):
        """报告预览 / map-jobs 口径的 result_summary 读取对两种结构一致。"""
        _, old_job = self._archived(dict(self.OLD_EVIDENCE))
        _, new_job = self._archived(extract_evidence(_stdout()))
        for job in (old_job, new_job):
            summary = job.result_summary
            assert summary.get("total", 0) == 3           # 既有结论字段不受 retention 影响
            assert summary.get("passed", 0) == 2
            assert summary.get("failed", 0) == 0
            retention = summary.get("retention")
            assert isinstance(retention, dict)
            assert retention["evidence"]["assertions"] == 3

    def test_rerun_archive_never_rewrites_old_structure(self):
        """重复执行归档：旧结构作业被幂等跳过，retention 块逐字节保持原样。"""
        import copy

        run, job = self._archived(dict(self.OLD_EVIDENCE))
        before = copy.deepcopy(job.result_summary)
        plan = build_retention_plan(runs=[run], jobs=[job], now=NOW)
        assert plan.actions == []
        assert plan.skipped["already_archived"] == 1
        assert job.result_summary == before


class TestRetentionEndpointAuth:
    """复核意见第 1 项：归档 API 必须接统一鉴权（超管），confirm=true 不能替代权限控制。

    依赖本体（401/403/allow+deny 审计）的用例见 test_web_script_reviews_api.py；
    这里锁定**本端点的接线**：current_user 走 get_current_superuser。
    """

    def test_retention_endpoint_uses_superuser_dependency(self):
        import inspect

        from fastapi.params import Depends

        from app.api.deps import get_current_superuser
        from app.api.v2.test_runs import apply_test_run_retention

        dep = inspect.signature(apply_test_run_retention).parameters["current_user"].default
        assert isinstance(dep, Depends)
        assert dep.dependency is get_current_superuser

    async def test_unauthenticated_request_gets_401(self):
        """无 token 请求在到达 DB/归档逻辑之前就被依赖层 401 拦截。"""
        import httpx
        from fastapi import FastAPI

        from app.api.v2.test_runs import router as test_runs_router

        app = FastAPI()
        app.include_router(test_runs_router, prefix="/api/v2")
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v2/projects/PR-2/test-runs/retention",
                json={"dry_run": True},
            )
        assert resp.status_code == 401, resp.text


class TestDiskRetention:
    def _workspace(self, tmp_path: Path) -> Path:
        old_dir = tmp_path / "playwright-report-1783332610731-169a0285"
        old_dir.mkdir(parents=True)
        (old_dir / "index.html").write_text("x" * 2048, encoding="utf-8")
        new_dir = tmp_path / "playwright-report-1790589910732-1b05a828"
        new_dir.mkdir(parents=True)
        (new_dir / "index.html").write_text("y" * 512, encoding="utf-8")
        old_time = (NOW - timedelta(days=60)).timestamp()
        import os
        os.utime(old_dir, (old_time, old_time))
        return tmp_path

    def test_plan_skips_referenced_and_recent_dirs(self, tmp_path: Path):
        workspace = self._workspace(tmp_path)
        referenced = ["playwright-reports/x/playwright-report-1783332610731-169a0285/report.zip"]
        plan = build_disk_retention_plan(workspace, referenced, now=NOW, older_than_days=30)
        assert plan["dir_count"] == 0
        assert plan["protected_by_db"] == 1
        assert plan["too_new"] == 1

    def test_plan_lists_unreferenced_old_dirs(self, tmp_path: Path):
        workspace = self._workspace(tmp_path)
        plan = build_disk_retention_plan(workspace, [], now=NOW, older_than_days=30)
        assert plan["dir_count"] == 1
        assert plan["dirs"][0]["name"] == "playwright-report-1783332610731-169a0285"
        assert plan["bytes"] >= 2048

    def test_apply_removes_only_planned_dir(self, tmp_path: Path):
        workspace = self._workspace(tmp_path)
        plan = build_disk_retention_plan(workspace, [], now=NOW, older_than_days=30)
        stats = apply_disk_retention_plan(plan)
        assert stats["removed_dirs"] == 1
        assert not (workspace / "playwright-report-1783332610731-169a0285").exists()
        assert (workspace / "playwright-report-1790589910732-1b05a828").exists()  # 新目录保留

    def test_apply_refuses_path_outside_workspace(self, tmp_path: Path):
        workspace = tmp_path / "ws"
        workspace.mkdir()
        outside = tmp_path / "playwright-report-outside"
        outside.mkdir()
        plan = {
            "workspace": str(workspace),
            "dirs": [{"name": outside.name, "path": str(outside), "bytes": 1}],
        }
        stats = apply_disk_retention_plan(plan)
        assert stats["removed_dirs"] == 0
        assert stats["error_count"] >= 1 and "越界" in stats["errors"][0]
        assert outside.exists()

    def test_apply_refuses_dir_name_not_matching_runner_pattern(self, tmp_path: Path):
        workspace = tmp_path / "ws"
        workspace.mkdir()
        keep = workspace / "important-manual-report"
        keep.mkdir()
        plan = {
            "workspace": str(workspace),
            "dirs": [{"name": keep.name, "path": str(keep), "bytes": 1}],
        }
        stats = apply_disk_retention_plan(plan)
        assert stats["removed_dirs"] == 0
        assert keep.exists()
        assert any("命名不匹配" in err for err in stats["errors"])

    def test_stale_dir_is_reported_separately(self, tmp_path: Path):
        """复核意见：失效软链（已不存在的目录）单独记账，不混进删除失败。"""
        workspace = tmp_path / "ws"
        workspace.mkdir()
        missing = workspace / "playwright-report-1790000000000-deadbeef"
        plan = {
            "workspace": str(workspace),
            "dirs": [{"name": missing.name, "path": str(missing), "bytes": 0}],
        }
        stats = apply_disk_retention_plan(plan)
        assert stats["removed_dirs"] == 0
        assert stats["stale_link_count"] == 1
        assert stats["error_count"] == 0
        assert "失效软链" in stats["stale_links"][0]
