"""Modality profile — the handle that plugs a specific biometric into the core.

A ``Profile`` bundles everything the shared core needs to store and match one
modality's templates: its embedding dimension, where its data lives (a per-modality
subdirectory + DB file under a tenant), its enrolment policy, and its match
thresholds. Face and palm are two profiles over the *same* ``TemplateStore`` and
``TenantIndex`` code.

Encoder / detector / liveness hooks (turning an image into an embedding, deciding
whether a frame contains this modality, anti-spoof) are attached by the modality
package and the router in later phases; this module owns only the storage + match
parameters so the core stays dependency-free and importable without any model.

Layout note: the **face** profile uses ``subdir=""`` and ``faces.db`` so its
on-disk location is byte-for-byte what it has always been (``<tenant>/faces.db``,
``<tenant>/index/``). Palm uses its own subdir, so the two never collide and are
never cross-searched.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from .core import index as _index
from .core.store import TemplateStore


@dataclass(frozen=True)
class Profile:
    name: str                       # "face" | "palm" — also the modality tag in APIs/audit
    dim: int                        # embedding dimension (512 for ArcFace)
    db_file: str                    # SQLite file name within the modality's directory
    subdir: str                     # per-modality subdir under a tenant dir ("" = tenant root)
    match_threshold: float          # accept if best cosine similarity >= this
    identify_margin: float          # 1:N: top must beat 2nd identity by this
    samples_per_user: int           # anchor embeddings stored per identity
    adaptive_novelty: float = 0.92  # skip near-duplicate adaptive captures (>= this cosine)
    adaptive_max_samples: int = 8   # total stored embeddings cap (anchors + adaptive)

    def store_path(self, tenant_db_path: str) -> str:
        """Directory holding this modality's data for a given tenant."""
        return tenant_db_path if not self.subdir else os.path.join(tenant_db_path, self.subdir)

    def make_store(self, tenant_db_path: str) -> TemplateStore:
        return TemplateStore(
            self.store_path(tenant_db_path),
            samples_per_user=self.samples_per_user,
            adaptive_novelty=self.adaptive_novelty,
            adaptive_max_samples=self.adaptive_max_samples,
            db_file=self.db_file,
        )

    def get_index(self, tenant_db_path: str, store: TemplateStore):
        return _index.get_index(self.store_path(tenant_db_path), store, dim=self.dim)


# --- registry ---------------------------------------------------------------
# The face profile mirrors the existing FaceConfig defaults exactly (dim 512,
# threshold 0.40, margin 0.06, 3 samples) and keeps face data at the tenant root.
FACE_PROFILE = Profile(
    name="face",
    dim=512,
    db_file="faces.db",
    subdir="",
    match_threshold=0.40,
    identify_margin=0.06,
    samples_per_user=3,
    adaptive_novelty=0.92,
    adaptive_max_samples=8,
)

_REGISTRY = {FACE_PROFILE.name: FACE_PROFILE}


def register(profile: Profile) -> None:
    _REGISTRY[profile.name] = profile


def get(name: str) -> Profile:
    return _REGISTRY[name]


def names() -> list:
    return list(_REGISTRY)
