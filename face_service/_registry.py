"""Tiny JSON-registry helper shared by the service subsystems.

Every subsystem in this package persists a small, human-readable JSON document
guarded by a lock, with the file path taken from an environment variable so
tests can point it at throwaway storage. That boilerplate was copied into each
module; this collapses it into one well-tested place:

    reg = Registry("FACE_THING_FILE", "thing.json")
    data = reg.load()
    reg.save(data)
    with reg.mutate() as data:        # load, hand you the dict, save on exit
        data["x"] = 1

The path is resolved on every access (not cached at import), so setting the env
var inside a test fixture takes effect immediately. Files are written 0600.
"""

from __future__ import annotations

import contextlib
import json
import os
import threading
from typing import Iterator, Optional


class Registry:
    def __init__(self, env_var: str, default_name: str):
        self._env = env_var
        self._default = default_name
        self._lock = threading.Lock()

    def path(self) -> str:
        return os.environ.get(self._env, self._default)

    def load(self) -> dict:
        p = self.path()
        if not os.path.exists(p):
            return {}
        try:
            with open(p, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return {}

    def save(self, data: dict) -> None:
        p = self.path()
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
        try:
            os.chmod(p, 0o600)
        except OSError:
            pass

    @contextlib.contextmanager
    def mutate(self) -> Iterator[dict]:
        """Load under the lock, yield the dict, save on clean exit."""
        with self._lock:
            data = self.load()
            yield data
            self.save(data)

    @staticmethod
    def norm(tenant: Optional[str]) -> str:
        return (tenant or "default").strip() or "default"
