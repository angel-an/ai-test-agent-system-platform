# -*- coding: utf-8 -*-
"""P0-4 测试：统一执行门控。"""

import os

import pytest

from app.platform.execution_gates import (
    GateConfig,
    ExecutionGate,
    should_skip_live,
    should_run_risky,
    should_run_auth_negative,
    check_live_gate,
    check_risky_gate,
    get_gate_status,
)


class TestGateConfig:
    def test_default_all_false(self):
        cfg = GateConfig()
        assert cfg.live_api is False
        assert cfg.live_web is False
        assert cfg.run_risky_negative is False
        assert cfg.auth_negative is False
        assert cfg.fail_closed is False

    def test_from_env_all_off(self, monkeypatch):
        monkeypatch.delenv("LIVE_API", raising=False)
        monkeypatch.delenv("LIVE_WEB", raising=False)
        monkeypatch.delenv("RUN_RISKY_NEG", raising=False)
        cfg = GateConfig.from_env()
        assert cfg.is_safe_mode() is True

    def test_from_env_live_api_on(self, monkeypatch):
        monkeypatch.setenv("LIVE_API", "1")
        cfg = GateConfig.from_env()
        assert cfg.live_api is True
        assert cfg.is_safe_mode() is False

    def test_from_env_live_web_on(self, monkeypatch):
        monkeypatch.setenv("LIVE_WEB", "1")
        cfg = GateConfig.from_env()
        assert cfg.live_web is True

    def test_from_env_risky_on(self, monkeypatch):
        monkeypatch.setenv("RUN_RISKY_NEG", "1")
        cfg = GateConfig.from_env()
        assert cfg.run_risky_negative is True

    def test_from_env_auth_neg_on(self, monkeypatch):
        monkeypatch.setenv("AUTH_NEG", "1")
        cfg = GateConfig.from_env()
        assert cfg.auth_negative is True

    def test_from_env_fail_closed_on(self, monkeypatch):
        monkeypatch.setenv("FAIL_CLOSED", "1")
        cfg = GateConfig.from_env()
        assert cfg.fail_closed is True

    def test_any_live(self):
        assert GateConfig(live_api=True).any_live() is True
        assert GateConfig(live_web=True).any_live() is True
        assert GateConfig().any_live() is False


class TestShouldSkipLive:
    def test_default_skip(self, monkeypatch):
        monkeypatch.delenv("LIVE_API", raising=False)
        monkeypatch.delenv("LIVE_WEB", raising=False)
        assert should_skip_live() is True

    def test_live_api_no_skip(self, monkeypatch):
        monkeypatch.setenv("LIVE_API", "1")
        assert should_skip_live() is False


class TestShouldRunRisky:
    def test_default_false(self, monkeypatch):
        monkeypatch.delenv("RUN_RISKY_NEG", raising=False)
        assert should_run_risky() is False

    def test_explicit_true(self, monkeypatch):
        monkeypatch.setenv("RUN_RISKY_NEG", "1")
        assert should_run_risky() is True


class TestShouldRunAuthNegative:
    def test_default_false(self, monkeypatch):
        monkeypatch.delenv("AUTH_NEG", raising=False)
        assert should_run_auth_negative() is False

    def test_explicit_true(self, monkeypatch):
        monkeypatch.setenv("AUTH_NEG", "1")
        assert should_run_auth_negative() is True


class TestCheckLiveGate:
    def test_api_allowed(self):
        cfg = GateConfig(live_api=True)
        allowed, reason = check_live_gate("api", cfg)
        assert allowed is True
        assert reason is None

    def test_api_denied(self):
        cfg = GateConfig(live_api=False)
        allowed, reason = check_live_gate("api", cfg)
        assert allowed is False
        assert "LIVE_API" in (reason or "")

    def test_web_allowed(self):
        cfg = GateConfig(live_web=True)
        allowed, reason = check_live_gate("web", cfg)
        assert allowed is True

    def test_web_denied(self):
        cfg = GateConfig(live_web=False)
        allowed, reason = check_live_gate("web", cfg)
        assert allowed is False
        assert "LIVE_WEB" in (reason or "")


class TestCheckRiskyGate:
    def test_positive_allowed(self):
        cfg = GateConfig(run_risky_negative=False)
        allowed, reason = check_risky_gate("正向", cfg)
        assert allowed is True

    def test_negative_denied(self):
        cfg = GateConfig(run_risky_negative=False)
        allowed, reason = check_risky_gate("反向", cfg)
        assert allowed is False
        assert "RUN_RISKY_NEG" in (reason or "")

    def test_boundary_denied(self):
        cfg = GateConfig(run_risky_negative=False)
        allowed, reason = check_risky_gate("边界", cfg)
        assert allowed is False

    def test_negative_allowed(self):
        cfg = GateConfig(run_risky_negative=True)
        allowed, reason = check_risky_gate("反向", cfg)
        assert allowed is True

    def test_none_allowed(self):
        cfg = GateConfig(run_risky_negative=False)
        allowed, _ = check_risky_gate(None, cfg)
        assert allowed is True


class TestExecutionGate:
    def test_check_all_pass(self):
        gate = ExecutionGate(GateConfig(live_api=True, run_risky_negative=True))
        allowed, reasons = gate.check_all("api", "正向")
        assert allowed is True
        assert reasons == []

    def test_check_all_live_denied(self):
        gate = ExecutionGate(GateConfig())
        allowed, reasons = gate.check_all("api", "正向")
        assert allowed is False
        assert any("LIVE_API" in r for r in reasons)

    def test_check_all_risky_denied(self):
        gate = ExecutionGate(GateConfig(live_api=True))
        allowed, reasons = gate.check_all("api", "反向")
        assert allowed is False
        assert any("RUN_RISKY_NEG" in r for r in reasons)

    def test_is_safe_regression(self):
        gate = ExecutionGate(GateConfig())
        assert gate.is_safe_regression() is True

    def test_is_not_safe_regression(self):
        gate = ExecutionGate(GateConfig(live_api=True))
        assert gate.is_safe_regression() is False

    def test_require_live_fail_closed_raises(self, monkeypatch):
        monkeypatch.setenv("FAIL_CLOSED", "1")
        gate = ExecutionGate(GateConfig(fail_closed=True))
        with pytest.raises(RuntimeError, match="fail-closed"):
            gate.require_live("api")

    def test_require_live_dev_degradation(self, monkeypatch):
        monkeypatch.delenv("FAIL_CLOSED", raising=False)
        gate = ExecutionGate(GateConfig(fail_closed=False))
        # 降级模式不抛异常
        gate.require_live("api")


class TestGetGateStatus:
    def test_returns_dict(self, monkeypatch):
        monkeypatch.setenv("LIVE_API", "1")
        monkeypatch.setenv("RUN_RISKY_NEG", "1")
        status = get_gate_status()
        assert status["live_api"] is True
        assert status["run_risky_negative"] is True
        assert status["safe_mode"] is False
