"""Face recognition engine — ArcFace embeddings (InsightFace, CPU/ONNX).

Mirrors the fingerprint package's shape: an engine produces a template (here a
512-d embedding) from an image, a matcher compares by cosine similarity, an
encrypted store persists templates, and a thin api exposes enroll/verify/
identify as plain dict envelopes for the Flask service.
"""

from .config import FaceConfig, CONFIG

__all__ = ["FaceConfig", "CONFIG"]
