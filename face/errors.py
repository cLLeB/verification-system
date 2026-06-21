"""Face engine errors that carry a user-facing reason."""

from __future__ import annotations


class FaceError(Exception):
    """A capture that cannot be enrolled/matched (with actionable feedback)."""

    def __init__(self, message: str, code: str = "low_quality") -> None:
        super().__init__(message)
        self.message = message
        self.code = code
