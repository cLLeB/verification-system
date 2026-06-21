"""Engine exception hierarchy."""


class FingerprintError(Exception):
    """Base class for all engine errors."""


class QualityError(FingerprintError):
    """Capture quality is too poor to process reliably (ask user to recapture)."""


class EnhancementError(FingerprintError):
    """Ridge enhancement failed to find any fingerprint structure."""
