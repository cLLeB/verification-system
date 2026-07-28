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

Durability & concurrency guarantees:
  * **Atomic writes** - ``save`` writes a sibling temp file and ``os.replace``s it
    over the target, so a reader never observes a half-written file and a crash
    mid-write leaves the previous good file intact (no truncation/corruption).
  * **Corruption is surfaced, not swallowed** - ``load`` on a damaged file moves it
    aside to ``<path>.corrupt.<ts>`` and returns ``{}`` rather than silently
    discarding it, so the bad data can be recovered and the failure is visible.
  * **Cross-process safe** - ``mutate`` holds both an in-process lock and an OS
    advisory file lock (fcntl/msvcrt), so concurrent workers on the same file
    serialise their read-modify-write instead of losing updates.

Use ``Registry.scoped(tenant, *parts)`` to build a flat composite key: it escapes
the separator so ``("a", "x:evil")`` and ``("a:x", "evil")`` never collide.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import threading
import time
from typing import Iterator, Optional

try:                                        # POSIX advisory locks
    import fcntl                            # type: ignore
    _HAVE_FCNTL = True
except ImportError:
    _HAVE_FCNTL = False

try:                                        # Windows advisory locks
    import msvcrt                           # type: ignore
    _HAVE_MSVCRT = True
except ImportError:
    _HAVE_MSVCRT = False


@contextlib.contextmanager
def _os_file_lock(lock_path: str, timeout: float = 15.0) -> Iterator[None]:
    """Best-effort cross-process exclusive lock on a sidecar ``.lock`` file.

    Falls back to a no-op only if neither fcntl nor msvcrt is available (which
    does not happen on supported platforms). The in-process threading lock in
    ``mutate`` still guarantees intra-process safety regardless.
    """
    fh = open(lock_path, "a+")
    try:
        deadline = time.time() + timeout
        while True:
            try:
                if _HAVE_FCNTL:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                elif _HAVE_MSVCRT:
                    fh.seek(0)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError:
                if time.time() >= deadline:
                    raise TimeoutError(f"could not acquire lock: {lock_path}")
                time.sleep(0.02)
        try:
            yield
        finally:
            try:
                if _HAVE_FCNTL:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
                elif _HAVE_MSVCRT:
                    fh.seek(0)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
    finally:
        fh.close()


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
        except OSError:
            return {}
        except ValueError:
            # Damaged JSON: preserve it for recovery instead of silently losing it.
            try:
                os.replace(p, f"{p}.corrupt.{int(time.time())}")
            except OSError:
                pass
            return {}

    def save(self, data: dict) -> None:
        p = self.path()
        tmp = f"{p}.tmp.{os.getpid()}.{threading.get_ident()}"
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, p)                 # atomic on Windows and POSIX
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
        try:
            os.chmod(p, 0o600)
        except OSError:
            pass

    @contextlib.contextmanager
    def mutate(self) -> Iterator[dict]:
        """Load under an in-process + cross-process lock, yield, save on clean exit."""
        with self._lock:
            with _os_file_lock(self.path() + ".lock"):
                data = self.load()
                yield data
                self.save(data)

    @staticmethod
    def norm(tenant: Optional[str]) -> str:
        return (tenant or "default").strip() or "default"

    @staticmethod
    def scoped(tenant: Optional[str], *parts) -> str:
        """A flat composite key whose segments can't collide across boundaries.

        Escapes the ``:`` separator (and the escape char) in every segment, so
        ``scoped("a", "x:evil") != scoped("a:x", "evil")``.
        """
        def esc(s) -> str:
            return str(s).replace("\\", "\\\\").replace(":", "\\:")
        return ":".join([esc(Registry.norm(tenant))] + [esc(p) for p in parts])
