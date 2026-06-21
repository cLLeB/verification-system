"""Contactless fingerprint biometric engine.

A minutiae-based fingerprint recognition pipeline designed for contactless
(camera) capture. Replaces the previous ORB-on-skeleton approach, which could
not actually distinguish identities.

Public API:
    from fingerprint import enroll, verify, identify, process_image
"""

from .config import Config
from .types import Minutia, Template, MatchResult, Decision


def __getattr__(name):
    # Lazy-load the high-level API so the lower-level modules (enhance, minutiae,
    # matcher) remain importable even while api.py and its deps are being built.
    if name in {
        "enroll", "verify", "identify", "process_image",
        "list_users", "delete_user",
    }:
        from . import api
        return getattr(api, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "Config",
    "Minutia",
    "Template",
    "MatchResult",
    "Decision",
    "enroll",
    "verify",
    "identify",
    "process_image",
    "list_users",
    "delete_user",
]
