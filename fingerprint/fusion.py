"""Score-level fusion of two independent matchers.

  * our minutiae matcher (rotation/translation Hough)  -> score in [0,1]
  * SourceAFIS (gold standard)                          -> open-ended score

A user is ACCEPTED if EITHER matcher clears its own threshold (OR-fusion), each
with its own safety check. Because the two algorithms fail on different genuine
captures, OR-fusion recovers genuine matches (lower false-reject) while an
impostor must still fool one of two independent algorithms at a conservative
threshold (false-accept stays low). For ranking/margin we use a
threshold-normalised score so "1.0" means "exactly at the accept boundary".

Benchmarked against z-score / tanh normalised SUM fusion (Jain et al.): at zero
false-accept, tanh-sum and z-sum were WORSE than OR; only a z-score weighted-sum
edged it, within noise, and it needs dataset-specific normalisation stats that
don't transfer across the contactless domain gap. OR is the robust default.
"""

from __future__ import annotations

from typing import List

from . import sourceafis as _saf
from .config import Config, CONFIG
from .matcher import match
from .types import MatchResult, Sample, Template


def _best_pair_scores(probe: Sample, template: Template, cfg: Config):
    """Best (our_score, our_matched, saf_score) across the template's samples."""
    best_our, best_matched, best_saf = 0.0, 0, 0.0
    saf_on = cfg.use_sourceafis and probe.saf_template and _saf.available()
    probe_min = list(probe.minutiae)
    for s in template.samples:
        our_score, matched = match(probe_min, list(s.minutiae), cfg)
        if our_score > best_our:
            best_our, best_matched = our_score, matched
        if saf_on and s.saf_template:
            sv = _saf.score(probe.saf_template, s.saf_template)
            if sv > best_saf:
                best_saf = sv
    return best_our, best_matched, best_saf


def compare(probe: Sample, template: Template, cfg: Config = CONFIG) -> MatchResult:
    our_score, matched, saf_score = _best_pair_scores(probe, template, cfg)

    accept_our = our_score >= cfg.match_threshold and matched >= cfg.min_matched_minutiae
    accept_saf = cfg.use_sourceafis and saf_score >= cfg.saf_threshold
    accepted = bool(accept_our or accept_saf)

    # Threshold-normalised rank: >=1.0 from whichever matcher is most confident.
    our_norm = our_score / cfg.match_threshold if cfg.match_threshold > 0 else 0.0
    saf_norm = saf_score / cfg.saf_threshold if cfg.saf_threshold > 0 else 0.0
    rank = max(our_norm, saf_norm)

    return MatchResult(
        user_id=template.user_id,
        score=round(our_score, 4),
        matched_minutiae=matched,
        saf_score=round(saf_score, 1),
        rank=round(rank, 4),
        accepted=accepted,
    )


def score_all(probe: Sample, templates: List[Template], cfg: Config = CONFIG) -> List[MatchResult]:
    results = [compare(probe, t, cfg) for t in templates]
    results.sort(key=lambda r: r.rank, reverse=True)
    return results
