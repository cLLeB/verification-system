"""Face engine errors (shim).

``FaceError`` is now a subclass of the shared ``BiometricError`` so service code
can catch one base type across face and palm, while existing ``FaceError`` raises
and ``except FaceError`` handlers keep working unchanged.
"""

from __future__ import annotations

from biometric.core.errors import BiometricError


class FaceError(BiometricError):
    """A face capture that cannot be enrolled/matched (with actionable feedback)."""
