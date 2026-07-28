"""Legal holds - preserve specific identities against erasure.

Data-protection law grants a right to erasure, and this platform honours it - but
that right yields when the data is under a **legal hold**: an active investigation,
litigation, or a regulator's preservation order. Deleting held data can itself be
an offence (spoliation). This subsystem lets a tenant place a named hold on a
user_id; while any hold is active, erasure/offboarding paths must refuse.

  * ``place``    opens a hold (matter reference + who placed it).
  * ``release``  lifts one hold by its id; erasure is possible again only when
                 the last hold on that user is gone.
  * ``is_held``  the single check the delete paths call before erasing.
  * ``guard``    raises ``HeldError`` so a caller can fail loudly instead of
                 silently skipping a deletion.

A user can carry several concurrent holds (different matters); all must be
released before the data may go. Enforcement is at the erasure boundary, never
at verify - a held person still authenticates normally.

Registry: ``legalhold.json`` (env ``FACE_LEGALHOLD_FILE``).
"""

from __future__ import annotations

import time
import uuid
from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_LEGALHOLD_FILE", "legalhold.json")


class HeldError(RuntimeError):
    """Raised when an operation would erase data under an active legal hold."""


def place(tenant: Optional[str], user_id: str, matter: str, by: str = "") -> dict:
    uid = (user_id or "").strip()
    matter = (matter or "").strip()
    if not uid:
        raise ValueError("user_id is required.")
    if not matter:
        raise ValueError("a matter reference is required for a legal hold.")
    t = _reg.norm(tenant)
    hold = {"id": "lh_" + uuid.uuid4().hex[:12], "matter": matter,
            "by": by or "", "placed_at": int(time.time())}
    with _reg.mutate() as data:
        data.setdefault(t, {}).setdefault(uid, []).append(hold)
    return hold


def release(tenant: Optional[str], user_id: str, hold_id: str) -> bool:
    t = _reg.norm(tenant)
    uid = (user_id or "").strip()
    with _reg.mutate() as data:
        holds = (data.get(t) or {}).get(uid) or []
        n = len(holds)
        holds[:] = [h for h in holds if h["id"] != hold_id]
        if not holds:
            (data.get(t) or {}).pop(uid, None)
        changed = len(holds) != n
    return changed


def holds_on(tenant: Optional[str], user_id: str) -> List[dict]:
    return list((_reg.load().get(_reg.norm(tenant)) or {}).get((user_id or "").strip()) or [])


def is_held(tenant: Optional[str], user_id: str) -> bool:
    return bool(holds_on(tenant, user_id))


def guard(tenant: Optional[str], user_id: str) -> None:
    """Raise HeldError if the user is under any active hold. Delete paths call
    this before erasing."""
    hs = holds_on(tenant, user_id)
    if hs:
        raise HeldError(f"'{user_id}' is under {len(hs)} legal hold(s): "
                        f"{', '.join(h['matter'] for h in hs)}.")


def list_for(tenant: Optional[str]) -> List[dict]:
    out = []
    for uid, holds in sorted((_reg.load().get(_reg.norm(tenant)) or {}).items()):
        for h in holds:
            out.append({"user_id": uid, **h})
    return out
