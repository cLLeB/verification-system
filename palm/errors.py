"""Palm engine errors (carry a user-facing reason + code).

Subclass of the shared ``BiometricError`` so service code can catch one base type
across face and palm, while palm keeps its own type and codes.
"""

from __future__ import annotations

from biometric.core.errors import BiometricError


class PalmError(BiometricError):
    """A palm capture that cannot be enrolled/matched (with actionable feedback)."""
