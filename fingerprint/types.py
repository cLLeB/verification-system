"""Immutable data types shared across the fingerprint engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Tuple


@dataclass(frozen=True)
class Minutia:
    """A single minutia point in image coordinates.

    x: column, y: row (pixels). theta: ridge direction in radians [-pi, pi].
    kind: 'termination' or 'bifurcation'.
    """

    x: float
    y: float
    theta: float
    kind: str


@dataclass(frozen=True)
class Sample:
    """One captured impression.

    Carries BOTH representations so the decision can fuse two independent
    matchers: our minutiae set, and a SourceAFIS template (bytes; empty if the
    SourceAFIS backend is unavailable).
    """

    minutiae: Tuple[Minutia, ...]
    quality: float = 0.0
    saf_template: bytes = b""


@dataclass(frozen=True)
class Template:
    """An enrolled identity: one or more impressions of the same finger."""

    user_id: str
    samples: Tuple[Sample, ...]
    version: int = 2  # v2 == minutiae engine; v1 was raw ORB descriptors.


@dataclass(frozen=True)
class MatchResult:
    """Outcome of comparing one probe against one enrolled user (fused)."""

    user_id: str
    score: float            # our minutiae matcher, normalised [0, 1]
    matched_minutiae: int   # raw aligned correspondences (best sample)
    saf_score: float = 0.0  # SourceAFIS similarity (open-ended; ~40 = match)
    rank: float = 0.0       # fused, threshold-normalised score for ranking
    accepted: bool = False  # did either matcher accept at its threshold?


@dataclass(frozen=True)
class Decision:
    """Final accept/reject decision for a verify/identify request."""

    granted: bool
    user_id: Optional[str]
    score: float
    margin: float
    reason: str
    candidates: Tuple[MatchResult, ...] = field(default_factory=tuple)
