"""Optional encryption-at-rest for stored face templates (shim).

Re-exports the shared, modality-agnostic implementation in
``biometric.core.crypto``. Kept as ``face.crypto`` so existing imports work; the
FACE_DB_KEY / FP_DB_KEY passphrase env vars still apply (BIO_DB_KEY is also
honoured), so existing encrypted face databases decrypt unchanged.
"""

from __future__ import annotations

from biometric.core.crypto import get_cipher, available  # noqa: F401

__all__ = ["get_cipher", "available"]
