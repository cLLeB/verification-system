"""Per-tenant face match index (shim over ``biometric.core.index``).

Face vectors are 512-d (ArcFace), so this re-exports the shared, dimension-generic
index with the face default. All behaviour — exact numpy backend, encrypted
persistence, restart replay, the per-tenant cache and ``on_add``/``on_remove``/
``invalidate`` helpers — is unchanged; palm uses the same core with its own dim
and its own (separate) store directory, so the two never share a cache entry.
"""

from __future__ import annotations

from biometric.core.index import (          # noqa: F401  (re-exported API)
    TenantIndex,
    Hit,
    get_index,
    on_add,
    on_remove,
    invalidate,
    _cached,
    _DIM,
    _USE_ANN,
    _HAS_HNSW,
    _NumpyBackend,
    _HnswBackend,
)

__all__ = [
    "TenantIndex", "Hit", "get_index", "on_add", "on_remove", "invalidate", "_DIM",
]
