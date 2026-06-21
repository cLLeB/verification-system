"""Accept/reject decision logic (fused).

Two modes:
  * verify  (1:1) -- "is this probe the claimed user?"
  * identify(1:N) -- "which enrolled user, if any, is this probe?"

A grant requires that at least one of the two independent matchers (our minutiae
matcher OR SourceAFIS) accepts at its own conservative threshold, AND — for 1:N
— that the best user beats the runner-up by a margin (so we never report the
WRONG user when scores are close). This is what prevents false/"hallucinated"
results: an unenrolled finger is rejected, and an ambiguous one is rejected too.
"""

from __future__ import annotations

from typing import List

from . import fusion as _fusion
from .config import Config, CONFIG
from .types import Decision, Sample, Template


def identify(
    probe: Sample, templates: List[Template], cfg: Config = CONFIG
) -> Decision:
    if not templates:
        return Decision(False, None, 0.0, 0.0, "No users enrolled.", tuple())

    results = _fusion.score_all(probe, templates, cfg)
    best = results[0]
    second_rank = results[1].rank if len(results) > 1 else 0.0
    margin = round(best.rank - second_rank, 4)

    if not best.accepted:
        return Decision(
            False, None, best.score, margin,
            "ACCESS DENIED: fingerprint does not match any enrolled user.",
            tuple(results),
        )
    if margin < cfg.fused_margin:
        return Decision(
            False, None, best.score, margin,
            "ACCESS DENIED: ambiguous match (too close to multiple users). Recapture.",
            tuple(results),
        )
    return Decision(
        True, best.user_id, best.score, margin,
        f"ACCESS GRANTED: Welcome {best.user_id}!",
        tuple(results),
    )


def verify(
    probe: Sample, template: Template, cfg: Config = CONFIG
) -> Decision:
    result = _fusion.compare(probe, template, cfg)
    if result.accepted:
        return Decision(
            True, template.user_id, result.score, result.rank,
            f"ACCESS GRANTED: identity confirmed for {template.user_id}.",
            (result,),
        )
    return Decision(
        False, None, result.score, result.rank,
        f"ACCESS DENIED: fingerprint does not match {template.user_id}.",
        (result,),
    )
