"""Auto-router — decide whether an image is a face, a palm, both, or neither.

The front door of the whole system: callers never declare a modality. Each entry
point hands a frame to ``route()``, which runs both modalities' fast presence
probes and picks where it goes. The decision logic is pure and dependency-free;
the actual probes (InsightFace detector, MediaPipe Hands) are injected by the
service layer, so this module imports no models and is trivially testable.

Routing rules:
  * face present, palm absent   -> "face"
  * palm present, face absent   -> "palm"
  * both present                -> "both"  (e.g. one image holding a face and a
                                            palm, to tie both to one identity)
  * neither                     -> "none"
When both fire, ``prefer`` (a tenant/route hint) can force a single modality.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional, Tuple

import numpy as np

FACE = "face"
PALM = "palm"
BOTH = "both"
NONE = "none"

# A presence probe takes an image and returns (present?, confidence 0..1).
Probe = Callable[[np.ndarray], Tuple[bool, float]]


@dataclass(frozen=True)
class RouteResult:
    modality: str            # "face" | "palm" | "both" | "none"
    face_present: bool
    palm_present: bool
    face_score: float
    palm_score: float

    @property
    def modalities(self) -> list:
        """The concrete modalities to act on (expands 'both')."""
        if self.modality == BOTH:
            return [FACE, PALM]
        if self.modality in (FACE, PALM):
            return [self.modality]
        return []


def decide(face_present: bool, face_score: float,
           palm_present: bool, palm_score: float,
           prefer: Optional[str] = None) -> str:
    if face_present and palm_present:
        if prefer in (FACE, PALM):
            return prefer
        return BOTH
    if face_present:
        return FACE
    if palm_present:
        return PALM
    return NONE


def route(image: np.ndarray, face_probe: Probe, palm_probe: Probe,
          prefer: Optional[str] = None, *, short_circuit: bool = False,
          primary: str = FACE, confident: float = 0.0) -> RouteResult:
    """Run the presence probes and decide. Probes must fail soft (return
    ``(False, 0.0)`` if their model is unavailable), so a missing model just makes
    that modality absent rather than failing the request.

    ``short_circuit`` (used for verify/identify, where the subject presents exactly
    ONE modality) runs the ``primary`` probe first and, if it fires with confidence
    ``>= confident``, returns immediately WITHOUT running the second probe — halving
    the routing cost for the common case. It never short-circuits enrolment, so a
    combined face+palm image can still enrol both. Accuracy is unaffected: only the
    cheap presence check is skipped; the matching encoder always runs at full
    fidelity on the routed modality."""
    if short_circuit:
        first = face_probe if primary == FACE else palm_probe
        present, score = first(image)
        if present and score >= confident:
            if primary == FACE:
                return RouteResult(FACE, True, False, float(score), 0.0)
            return RouteResult(PALM, False, True, 0.0, float(score))
        other_present, other_score = (palm_probe if primary == FACE else face_probe)(image)
        if primary == FACE:
            f_present, f_score, p_present, p_score = present, score, other_present, other_score
        else:
            f_present, f_score, p_present, p_score = other_present, other_score, present, score
    else:
        f_present, f_score = face_probe(image)
        p_present, p_score = palm_probe(image)
    modality = decide(f_present, f_score, p_present, p_score, prefer)
    return RouteResult(modality=modality, face_present=f_present, palm_present=p_present,
                       face_score=float(f_score), palm_score=float(p_score))
