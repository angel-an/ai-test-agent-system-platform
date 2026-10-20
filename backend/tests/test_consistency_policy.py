import pytest
from app.utils.consistency import ConsistencyPolicy, ConsistencyTimeout, wait_for_consistency


def run(values, timeout=2):
    now = [0]
    samples = iter(values)
    def sleep(seconds):
        now[0] += seconds
    return wait_for_consistency(lambda: next(samples), lambda x: x == 1,
                                ConsistencyPolicy(timeout, 1),
                                clock=lambda: now[0], sleep=sleep)


def test_immediate_success():
    assert run([1])["status"] == "passed"


def test_delayed_success_preserves_timeline():
    result = run([0, 0, 1])
    assert result["status"] == "passed_after_wait"
    assert [s["elapsed_seconds"] for s in result["observations"]] == [0, 1, 2]


def test_timeout_preserves_evidence():
    with pytest.raises(ConsistencyTimeout) as exc:
        run([0, -1, 0])
    assert len(exc.value.observations) == 3


def test_read_errors_are_not_retried():
    def read():
        raise ValueError("bad account")
    with pytest.raises(ValueError, match="bad account"):
        wait_for_consistency(read, bool)


@pytest.mark.parametrize("timeout,interval", [(0, 1), (1, 0), (-1, 1), (float('nan'), 1), (1, float('inf'))])
def test_invalid_policy(timeout, interval):
    with pytest.raises(ValueError):
        ConsistencyPolicy(timeout, interval)


def test_pr2_exact_money_and_explicit_field(monkeypatch):
    import importlib.util
    import sys
    from pathlib import Path
    root = Path(__file__).parent / "api" / "pr2_storage_recharge"
    monkeypatch.syspath_prepend(str(root))
    spec = importlib.util.spec_from_file_location("pr2_money_helpers", root / "helpers.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert not module.delta_within(0, 0.01)
    assert not module.delta_within(-0.01, 0.01)
    assert module.delta_within(0.01, 0.01)
    monkeypatch.setattr(module, "account_rows", lambda *args: [{"principalBalance": "1.23"}])
    monkeypatch.delenv("PR2_BALANCE_FIELD", raising=False)
    with pytest.raises(AssertionError, match="Configure"):
        module.get_balance(None, "test")
    monkeypatch.setenv("PR2_BALANCE_FIELD", "principalBalance")
    assert str(module.get_balance(None, "test")) == "1.23"
    monkeypatch.setenv("PR2_BALANCE_FIELD", "balance")
    with pytest.raises(AssertionError, match="Missing"):
        module.get_balance(None, "test")
