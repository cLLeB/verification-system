"""Template persistence (repository pattern).

Templates are stored as versioned JSON, one file per user. We deliberately do
NOT use pickle: unpickling attacker-controlled files is a remote-code-execution
risk, and the old v1 `.pkl` files (raw ORB descriptors) are incompatible with
the v2 minutiae engine anyway.
"""

from __future__ import annotations

import base64
import json
import os
import re
from typing import List, Optional

from . import crypto as _crypto
from .config import Config, CONFIG
from .types import Minutia, Sample, Template

_SAFE_ID = re.compile(r"[^A-Za-z0-9_.\-]")


def _safe_filename(user_id: str) -> str:
    cleaned = _SAFE_ID.sub("_", user_id.strip())
    if not cleaned:
        raise ValueError("Invalid user_id")
    return cleaned


def _template_to_dict(t: Template) -> dict:
    return {
        "version": t.version,
        "user_id": t.user_id,
        "samples": [
            {
                "quality": s.quality,
                "minutiae": [[m.x, m.y, m.theta, m.kind] for m in s.minutiae],
                "saf": base64.b64encode(s.saf_template).decode("ascii") if s.saf_template else "",
            }
            for s in t.samples
        ],
    }


def _template_from_dict(d: dict) -> Template:
    samples = []
    for s in d.get("samples", []):
        minutiae = tuple(
            Minutia(x=float(x), y=float(y), theta=float(th), kind=str(kind))
            for x, y, th, kind in s.get("minutiae", [])
        )
        saf_b64 = s.get("saf", "")
        saf = base64.b64decode(saf_b64) if saf_b64 else b""
        samples.append(Sample(minutiae=minutiae, quality=float(s.get("quality", 0.0)),
                              saf_template=saf))
    return Template(
        user_id=str(d["user_id"]),
        samples=tuple(samples),
        version=int(d.get("version", 2)),
    )


class TemplateStore:
    """File-backed store of enrolled templates."""

    def __init__(self, cfg: Config = CONFIG):
        self.db_path = cfg.db_path
        self.cfg = cfg
        os.makedirs(self.db_path, exist_ok=True)
        # None => plaintext JSON; a Fernet cipher => encrypted at rest.
        self._cipher = _crypto.get_cipher(self.db_path)

    @property
    def encrypted(self) -> bool:
        return self._cipher is not None

    def _path(self, user_id: str) -> str:
        return os.path.join(self.db_path, f"{_safe_filename(user_id)}.json")

    def _decode(self, raw: bytes) -> dict:
        """Parse a template file, transparently handling encrypted or plaintext.

        Raises ValueError on a key mismatch (encrypted file but wrong/missing
        FP_DB_KEY) so callers can skip/handle it rather than crash.
        """
        looks_encrypted = raw[:6] == b"gAAAAA"  # Fernet token prefix
        if looks_encrypted:
            if self._cipher is None:
                raise ValueError("Template is encrypted but FP_DB_KEY is not set.")
            try:
                return json.loads(self._cipher.decrypt(raw).decode("utf-8"))
            except _crypto.InvalidToken as exc:
                raise ValueError("Could not decrypt template (wrong FP_DB_KEY?).") from exc
        # Plaintext file. If a cipher is active it will be re-encrypted on next save.
        return json.loads(raw.decode("utf-8"))

    def exists(self, user_id: str) -> bool:
        return os.path.exists(self._path(user_id))

    def load(self, user_id: str) -> Optional[Template]:
        path = self._path(user_id)
        if not os.path.exists(path):
            return None
        try:
            with open(path, "rb") as fh:
                return _template_from_dict(self._decode(fh.read()))
        except (ValueError, json.JSONDecodeError, KeyError, OSError):
            # Unreadable (corrupt or wrong key) -> treat as absent; safe (no grant).
            return None

    def load_all(self) -> List[Template]:
        templates: List[Template] = []
        for fname in sorted(os.listdir(self.db_path)):
            if not fname.endswith(".json"):
                continue
            try:
                with open(os.path.join(self.db_path, fname), "rb") as fh:
                    templates.append(_template_from_dict(self._decode(fh.read())))
            except (json.JSONDecodeError, KeyError, OSError, ValueError):
                continue
        return templates

    def save(self, template: Template) -> None:
        path = self._path(template.user_id)
        tmp = path + ".tmp"
        raw = json.dumps(_template_to_dict(template)).encode("utf-8")
        if self._cipher is not None:
            raw = self._cipher.encrypt(raw)
        with open(tmp, "wb") as fh:
            fh.write(raw)
        os.replace(tmp, path)  # atomic write

    def add_sample(self, user_id: str, sample: Sample) -> Template:
        """Append a sample to a user's template (multi-sample enrolment).

        Keeps the highest-quality `samples_per_user` impressions.
        """
        existing = self.load(user_id)
        if existing is None:
            samples = (sample,)
        else:
            combined = list(existing.samples) + [sample]
            combined.sort(key=lambda s: s.quality, reverse=True)
            samples = tuple(combined[: self.cfg.samples_per_user])
        template = Template(user_id=user_id, samples=samples)
        self.save(template)
        return template

    def delete(self, user_id: str) -> bool:
        path = self._path(user_id)
        if os.path.exists(path):
            os.remove(path)
            return True
        return False

    def list_users(self) -> List[str]:
        return [t.user_id for t in self.load_all()]

    def legacy_pkl_users(self) -> List[str]:
        """Old v1 ORB templates that must be re-enrolled."""
        return [
            f[:-4] for f in os.listdir(self.db_path) if f.endswith(".pkl")
        ]
