"""Contactless palm-print modality.

A second biometric alongside face, built on the shared ``biometric`` core. The
pipeline mirrors face: detect → ROI normalise → embed → match, producing an
L2-normalised embedding that flows through the same template store, search index,
and matcher as face — but in its own per-tenant directory and vector space, never
cross-matched with face.

Stack: MediaPipe Hands for hand/ROI detection (``palm.roi``), a CCNet-family
encoder exported to ONNX for embeddings (``palm.engine``), a quality gate
(``palm.roi.quality``) and passive anti-spoof (``palm.liveness``). The ONNX
weights are an external asset (see ``palm/models/README_MODEL.md``); when absent,
``engine.available()`` is False and the modality is simply unavailable, exactly as
face is without its model.
"""

from .profile import PALM_PROFILE  # noqa: F401  (registers the palm profile on import)
