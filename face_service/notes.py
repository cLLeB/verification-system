"""Case notes — append-only operator annotations on an identity.

Operators accumulate context on people that belongs nowhere in the biometric
record: "left the company, badge retained", "flagged by reception 12 Jul",
"cleared after review". This subsystem is a simple append-only notebook per
identity: notes are added with an author and timestamp and can be read back in
order, but never edited or deleted individually — the history is the point (an
operator should not be able to quietly rewrite what they wrote). A whole
identity's notes can be purged only as part of erasing that identity.

  * ``add``    append a note (author + text).
  * ``list``   notes for an identity, oldest first.
  * ``latest`` the most recent note (for a summary row).
  * ``purge``  drop all notes for an identity (erasure only).

Registry: ``notes.json`` (env ``FACE_NOTES_FILE``).
"""

from __future__ import annotations

import time
from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_NOTES_FILE", "notes.json")


def add(tenant: Optional[str], user_id: str, text: str, author: str = "") -> dict:
    uid = (user_id or "").strip()
    text = (text or "").strip()
    if not uid:
        raise ValueError("user_id is required.")
    if not text:
        raise ValueError("note text is required.")
    note = {"seq": 0, "text": text, "author": author or "", "at": int(time.time())}
    with _reg.mutate() as data:
        lst = data.setdefault(_reg.norm(tenant), {}).setdefault(uid, [])
        note["seq"] = len(lst)
        lst.append(note)
    return dict(note)


def list(tenant: Optional[str], user_id: str) -> List[dict]:
    return [dict(n) for n in
            (_reg.load().get(_reg.norm(tenant)) or {}).get((user_id or "").strip()) or []]


def latest(tenant: Optional[str], user_id: str) -> Optional[dict]:
    notes = list(tenant, user_id)
    return notes[-1] if notes else None


def count(tenant: Optional[str], user_id: str) -> int:
    return len(list(tenant, user_id))


def purge(tenant: Optional[str], user_id: str) -> bool:
    t = _reg.norm(tenant)
    with _reg.mutate() as data:
        return (data.get(t) or {}).pop((user_id or "").strip(), None) is not None
