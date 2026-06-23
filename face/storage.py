"""Encrypted-at-rest store of face templates (shim over ``biometric.core.store``).

Face templates are 512-d ArcFace embeddings stored in ``faces.db``. This wraps the
shared, modality-agnostic ``TemplateStore`` with the face enrolment policy from
``FaceConfig`` and keeps the historical public names (``FaceStore``,
``FaceTemplate``, ``_pack``/``_unpack``, ``_MAGIC``…) so existing callers and tests
are unchanged. Palm uses the same core store with its own DB file and directory.
"""

from __future__ import annotations

from biometric.core.store import (        # noqa: F401  (re-exported API)
    BioTemplate as FaceTemplate,
    TemplateStore,
    _MAGIC,
    _MAGIC_FT1,
    _HEADER,
    _pack,
    _unpack,
    _dec,
    _SRC_LIVE,
    _SRC_ID,
    _src_to_byte,
    _byte_to_src,
)
from .config import FaceConfig, CONFIG

_DB_FILE = "faces.db"

__all__ = ["FaceStore", "FaceTemplate", "_MAGIC", "_MAGIC_FT1", "_pack", "_unpack"]


class FaceStore(TemplateStore):
    """Face-flavoured ``TemplateStore``: pulls its enrolment policy from
    ``FaceConfig`` and stores under ``faces.db``. ``self.cfg`` is preserved for
    any caller that reaches for it."""

    def __init__(self, cfg: FaceConfig = CONFIG) -> None:
        self.cfg = cfg
        super().__init__(
            db_path=cfg.db_path,
            samples_per_user=cfg.samples_per_user,
            adaptive_novelty=cfg.adaptive_novelty,
            adaptive_max_samples=cfg.adaptive_max_samples,
            db_file=_DB_FILE,
        )
