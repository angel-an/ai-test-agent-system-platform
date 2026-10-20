"""Opt-in read-only polling. Never retry a business write or hide read errors."""
from dataclasses import dataclass
import math
import time


@dataclass(frozen=True)
class ConsistencyPolicy:
    timeout: float = 10
    interval: float = 1

    def __post_init__(self):
        if not all(math.isfinite(v) and v > 0 for v in (self.timeout, self.interval)):
            raise ValueError("timeout and interval must be finite and positive")


class ConsistencyTimeout(AssertionError):
    def __init__(self, observations):
        self.observations = observations
        super().__init__(f"CONSISTENCY_TIMEOUT (原因待定位): {observations}")


def wait_for_consistency(read, matches, policy=None, *, clock=time.monotonic,
                         sleep=time.sleep, observe=None):
    """read must have its own network timeout; values must be safe to report.

    The deadline limits polling, not an in-flight synchronous network call.
    Exceptions propagate; a timeout is not proof of a server defect.
    """
    policy = policy or ConsistencyPolicy()
    start = clock()
    observations = []
    while True:
        value = read()
        elapsed = clock() - start
        sample = {"elapsed_seconds": round(elapsed, 3), "value": value}
        observations.append(sample)
        if observe:
            observe(sample)
        if elapsed <= policy.timeout and matches(value):
            return {"status": "passed" if len(observations) == 1 else "passed_after_wait",
                    "value": value, "observations": observations}
        remaining = policy.timeout - (clock() - start)
        if remaining <= 0:
            raise ConsistencyTimeout(observations)
        sleep(min(policy.interval, remaining))
