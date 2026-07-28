"""Retry backoff - compute delays for resilient retry loops.

Retries against flaky dependencies ([[webhooks]] delivery, SSO, SMS) must space out or they
amplify an outage. This subsystem computes backoff delays for a given attempt number under
the common strategies, with jitter to avoid the thundering-herd problem where many clients
retry in lockstep. It complements [[circuitbreaker]] (which stops retrying entirely) by
shaping the spacing while retries continue. Pure and stateless.

  * ``delay``     the wait before attempt ``n`` (1-based) under a strategy, capped, with
                  optional jitter; seedable for deterministic tests.
  * ``schedule``  the delays for the first ``n`` attempts, as a list.
  * ``total_time`` the sum of a schedule - the worst-case time before giving up.

Strategies: ``fixed`` (constant ``base``), ``linear`` (``base * n``), ``exponential``
(``base * 2**(n-1)``), each capped at ``cap``. Jitter modes: ``none``; ``full`` (uniform in
``[0, d]`` - AWS's recommended default); ``equal`` (``d/2 + uniform[0, d/2]``).
"""

from __future__ import annotations

import random
from typing import List, Optional

_STRATEGIES = ("fixed", "linear", "exponential")
_JITTERS = ("none", "full", "equal")


def _base_delay(attempt: int, base: float, strategy: str, cap: float) -> float:
    if strategy == "fixed":
        d = base
    elif strategy == "linear":
        d = base * attempt
    else:  # exponential
        d = base * (2 ** (attempt - 1))
    return min(d, cap)


def delay(attempt: int, base: float = 1.0, cap: float = 60.0,
          strategy: str = "exponential", jitter: str = "full",
          rng: Optional[random.Random] = None) -> float:
    attempt = int(attempt)
    if attempt < 1:
        raise ValueError("attempt must be >= 1.")
    if base <= 0 or cap <= 0:
        raise ValueError("base and cap must be positive.")
    if strategy not in _STRATEGIES:
        raise ValueError(f"strategy must be one of {_STRATEGIES}.")
    if jitter not in _JITTERS:
        raise ValueError(f"jitter must be one of {_JITTERS}.")
    d = _base_delay(attempt, float(base), strategy, float(cap))
    r = rng or random
    if jitter == "full":
        return round(r.uniform(0, d), 6)
    if jitter == "equal":
        return round(d / 2 + r.uniform(0, d / 2), 6)
    return round(d, 6)


def schedule(attempts: int, base: float = 1.0, cap: float = 60.0,
             strategy: str = "exponential", jitter: str = "none",
             seed: Optional[int] = None) -> List[float]:
    attempts = int(attempts)
    if attempts < 1:
        raise ValueError("attempts must be >= 1.")
    rng = random.Random(seed) if seed is not None else None
    return [delay(n, base, cap, strategy, jitter, rng) for n in range(1, attempts + 1)]


def total_time(attempts: int, base: float = 1.0, cap: float = 60.0,
               strategy: str = "exponential") -> float:
    return round(sum(schedule(attempts, base, cap, strategy, jitter="none")), 6)
