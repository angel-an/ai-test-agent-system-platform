# -*- coding: utf-8 -*-
"""作业失败分类与执行生命周期控制（2026-09-29 复核）。

口径（复核要求）：
- FAILED 且无断言证据**不能统一**映射 BLOCKED——脚本崩溃与登录前置失败必须区分；
- 只有确认前置/环境类才归 BLOCKED，脚本/执行器错误单独标明（FAILED）；
- 不得覆盖同一用例已有的有效断言结果（只升级 NOT_EXECUTED）。
"""

from __future__ import annotations

from app.services.execution.engine import classify_job_failure


class TestClassifyJobFailure:
    def test_login_precondition(self):
        kind, label = classify_job_failure(
            "企业控制台登录失败（验证码 OCR 重试 10 次后仍未离开登录页）"
        )
        assert kind == "precondition"
        assert label == "执行前置失败"

    def test_token_auth_precondition(self):
        assert classify_job_failure("401 unauthorized, token expired")[0] == "precondition"

    def test_environment_timeout(self):
        assert classify_job_failure("测试执行超时（超过 600 秒）")[0] == "environment"
        assert classify_job_failure("connect ECONNREFUSED 10.0.0.1:443")[0] == "environment"
        assert classify_job_failure("request blocked by WAF")[0] == "environment"

    def test_script_error(self):
        assert classify_job_failure("ReferenceError: foo is not defined")[0] == "script"
        assert classify_job_failure("TypeError: Cannot read properties of null")[0] == "script"
        assert classify_job_failure("waiting for locator '#x' timed out")[0] == "script"

    def test_unknown_keeps_failed_semantics(self):
        kind, label = classify_job_failure("some weird failure")
        assert kind == "unknown"  # 不包装成前置失败 → 用例级 FAILED
        assert label == "未分类失败"

    def test_empty_is_unknown(self):
        assert classify_job_failure("")[0] == "unknown"
        assert classify_job_failure(None)[0] == "unknown"

    def test_timeout_marker_is_environment_not_script(self):
        """'作业超时' 自身是环境/执行器类，且不应被 'timeout' 关键词误判为脚本。"""
        kind, _ = classify_job_failure("[作业超时] 超过 1800 秒，已请求取消并终止")
        assert kind == "environment"
