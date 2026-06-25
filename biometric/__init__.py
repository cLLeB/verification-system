"""Modality-agnostic biometric core.

The generic machinery — template storage, the per-tenant search index, cosine
matching, encryption-at-rest — that both the face and palm modalities share. A
*profile* (see ``biometric.profile``) plugs a specific modality (its encoder,
embedding dimension, thresholds, store location, liveness) into this core.

Face behaviour is preserved exactly: ``face/`` re-exports these modules as thin
shims, so every ``from face.x import Y`` keeps working unchanged.
"""
