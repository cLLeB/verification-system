"""Statistical significance for A/B experiments — is the difference real?

[[experiments]] collects conversions per variant; before acting on a winner you must know
the difference isn't noise. This subsystem runs the standard two-proportion z-test on two
variants' conversion counts, returning the z statistic, a p-value, the observed lift, and a
significance verdict at a chosen alpha. It also estimates the sample size needed to detect
a given effect, so teams can plan how long to run a test. Pure statistics — no state beyond
what you pass in.

  * ``two_proportion_test``  compare (conversions_a/n_a) vs (conversions_b/n_b): z,
                             two-sided p-value, absolute & relative lift, and whether it
                             is significant at ``alpha``.
  * ``required_sample``      approximate per-variant sample size to detect a lift from a
                             baseline rate at given power/alpha.

The p-value uses a normal approximation (an erf-based standard-normal CDF), which is
accurate for the sample sizes A/B tests actually use. No SciPy dependency.
"""

from __future__ import annotations

import math
from typing import Optional


def _norm_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


# z critical values for common two-sided alpha / one-sided power
_Z = {0.10: 1.2816, 0.05: 1.6449, 0.025: 1.96, 0.01: 2.3263, 0.005: 2.5758}


def two_proportion_test(conversions_a: int, n_a: int, conversions_b: int, n_b: int,
                        alpha: float = 0.05) -> dict:
    ca, na, cb, nb = int(conversions_a), int(n_a), int(conversions_b), int(n_b)
    if na <= 0 or nb <= 0:
        raise ValueError("sample sizes must be positive.")
    if not (0 <= ca <= na and 0 <= cb <= nb):
        raise ValueError("conversions must be within [0, n].")
    pa, pb = ca / na, cb / nb
    pooled = (ca + cb) / (na + nb)
    se = math.sqrt(pooled * (1 - pooled) * (1 / na + 1 / nb))
    if se == 0:
        z = 0.0
    else:
        z = (pb - pa) / se
    p_value = 2 * (1 - _norm_cdf(abs(z)))
    return {"rate_a": round(pa, 6), "rate_b": round(pb, 6),
            "abs_lift": round(pb - pa, 6),
            "rel_lift": round((pb - pa) / pa, 6) if pa > 0 else None,
            "z": round(z, 4), "p_value": round(p_value, 6),
            "significant": p_value < alpha,
            "winner": ("b" if pb > pa else "a" if pa > pb else "tie") if p_value < alpha else None}


def required_sample(baseline_rate: float, min_detectable_lift: float,
                    alpha: float = 0.05, power: float = 0.8) -> int:
    """Approx per-variant n to detect a relative lift from baseline (two-sided)."""
    p1 = float(baseline_rate)
    if not 0 < p1 < 1:
        raise ValueError("baseline_rate must be in (0, 1).")
    p2 = p1 * (1 + float(min_detectable_lift))
    if not 0 < p2 < 1:
        raise ValueError("resulting rate out of (0,1); lift too large.")
    z_alpha = _Z.get(round(alpha / 2, 3), 1.96)
    z_power = _Z.get(round(1 - power, 3), 0.8416)
    pbar = (p1 + p2) / 2
    num = (z_alpha * math.sqrt(2 * pbar * (1 - pbar))
           + z_power * math.sqrt(p1 * (1 - p1) + p2 * (1 - p2))) ** 2
    denom = (p2 - p1) ** 2
    return int(math.ceil(num / denom))
