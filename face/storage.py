"""Encrypted-at-rest store of face embeddings (one file per identity).

Reuses the fingerprint package's crypto (Fernet/AES). Embeddings are stored as
base64 float32 so the JSON is compact and lossless.
"""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np

from . import crypto
from .config import FaceConfig, CONFIG


@dataclass
class FaceTemplate:
    user_id: str
    embeddings: List[np.ndarray] = field(default_factory=list)


def _enc(emb: np.ndarray) -> str:
    return base64.b64encode(np.asarray(emb, dtype=np.float32).tobytes()).decode("ascii")


def _dec(s: str) -> np.ndarray:
    return np.frombuffer(base64.b64decode(s), dtype=np.float32)


class FaceStore:
    def __init__(self, cfg: FaceConfig = CONFIG) -> None:
        self.cfg = cfg
        self.db_path = cfg.db_path
        os.makedirs(self.db_path, exist_ok=True)
        self._cipher = crypto.get_cipher(self.db_path)

    @property
    def encrypted(self) -> bool:
        return self._cipher is not None

    def _path(self, user_id: str) -> str:
        safe = "".join(c for c in user_id if c.isalnum() or c in ("-", "_", " ")).strip()
        return os.path.join(self.db_path, f"{safe}.json")

    def _write(self, tmpl: FaceTemplate) -> None:
        payload = json.dumps({
            "user_id": tmpl.user_id,
            "embeddings": [_enc(e) for e in tmpl.embeddings],
        }).encode("utf-8")
        if self._cipher is not None:
            payload = self._cipher.encrypt(payload)
        with open(self._path(tmpl.user_id), "wb") as fh:
            fh.write(payload)

    def _read(self, path: str) -> Optional[FaceTemplate]:
        try:
            with open(path, "rb") as fh:
                raw = fh.read()
        except OSError:
            return None
        if self._cipher is not None:
            try:
                raw = self._cipher.decrypt(raw)
            except Exception:
                return None
        try:
            data = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None
        return FaceTemplate(user_id=data["user_id"],
                            embeddings=[_dec(s) for s in data.get("embeddings", [])])

    def load(self, user_id: str) -> Optional[FaceTemplate]:
        return self._read(self._path(user_id))

    def load_all(self) -> List[FaceTemplate]:
        out: List[FaceTemplate] = []
        for name in os.listdir(self.db_path):
            if name.endswith(".json"):
                t = self._read(os.path.join(self.db_path, name))
                if t is not None:
                    out.append(t)
        return out

    def add_embedding(self, user_id: str, emb: np.ndarray) -> FaceTemplate:
        tmpl = self.load(user_id) or FaceTemplate(user_id=user_id)
        tmpl.embeddings.append(np.asarray(emb, dtype=np.float32))
        # keep only the most recent samples_per_user impressions
        if len(tmpl.embeddings) > self.cfg.samples_per_user:
            tmpl.embeddings = tmpl.embeddings[-self.cfg.samples_per_user:]
        self._write(tmpl)
        return tmpl

    def list_users(self) -> List[str]:
        return [t.user_id for t in self.load_all()]

    def delete(self, user_id: str) -> bool:
        path = self._path(user_id)
        if os.path.exists(path):
            os.remove(path)
            return True
        return False
