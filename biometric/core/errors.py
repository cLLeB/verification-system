"""Biometric capture/match errors that carry a user-facing reason + code."""

from __future__ import annotations


class BiometricError(Exception):
    """A capture that cannot be enrolled/matched (with actionable feedback).

    Base class for every modality's error type, so service code can catch one
    type across face and palm while each modality keeps its own subclass.
    """

    def __init__(self, message: str, code: str = "low_quality") -> None:
        super().__init__(message)
        self.message = message
        self.code = code
