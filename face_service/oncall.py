"""On-call rotations — resolve who is responsible at any given moment.

Escalation policies name *roles* ("supervisor"); on-call rotations resolve a role
to the *person* actually carrying the pager right now. A rotation cycles a roster
of responders on a fixed shift length starting from an anchor time, so "who is the
on-call supervisor at 3am Tuesday" is a pure, deterministic lookup. One-off
overrides let a swapped shift take precedence without editing the roster.

  * ``define``    a rotation: ordered members, shift length, anchor start.
  * ``whoisoncall`` resolve the responder for a timestamp (honouring overrides).
  * ``override``  pin a specific member for a time window (holiday cover, swaps).
  * ``upcoming``  the next N shift hand-offs from a given time — for calendars.

The rotation math is anchor + floor((t - anchor) / shift) indexed round-robin into
the roster, so it needs no stored per-shift state and stays correct arbitrarily far
into the future.

Registry: ``oncall.json`` (env ``FACE_ONCALL_FILE``).
"""

from __future__ import annotations

import time
import uuid
from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_ONCALL_FILE", "oncall.json")

_DAY = 86400


def define(tenant: Optional[str], name: str, members: List[str],
           shift_seconds: int = 7 * _DAY, anchor: Optional[int] = None) -> dict:
    name = (name or "").strip()
    if not name:
        raise ValueError("rotation name is required.")
    mem = [m.strip() for m in (members or []) if (m or "").strip()]
    if not mem:
        raise ValueError("a rotation needs at least one member.")
    if int(shift_seconds) <= 0:
        raise ValueError("shift length must be positive.")
    anchor = int(anchor if anchor is not None else time.time())
    rot = {"id": "rot_" + uuid.uuid4().hex[:8], "name": name, "members": mem,
           "shift": int(shift_seconds), "anchor": anchor, "overrides": []}
    with _reg.mutate() as data:
        data.setdefault(_reg.norm(tenant), {})[rot["id"]] = rot
    return {"id": rot["id"], "name": name, "members": mem}


def _get(tenant: Optional[str], rot_id: str) -> Optional[dict]:
    return (_reg.load().get(_reg.norm(tenant)) or {}).get((rot_id or "").strip())


def _base_member(rot: dict, when: int) -> str:
    idx = ((when - rot["anchor"]) // rot["shift"]) % len(rot["members"])
    return rot["members"][idx]


def whoisoncall(tenant: Optional[str], rot_id: str,
                when: Optional[int] = None) -> dict:
    when = int(when if when is not None else time.time())
    rot = _get(tenant, rot_id)
    if not rot:
        return {"exists": False}
    for ov in rot.get("overrides", []):
        if ov["start"] <= when < ov["end"]:
            return {"exists": True, "member": ov["member"], "source": "override",
                    "override": ov["id"]}
    return {"exists": True, "member": _base_member(rot, when), "source": "rotation"}


def override(tenant: Optional[str], rot_id: str, member: str,
             start: int, end: int) -> dict:
    member = (member or "").strip()
    if not member:
        raise ValueError("override member is required.")
    if int(end) <= int(start):
        raise ValueError("override end must be after start.")
    ov = {"id": "ov_" + uuid.uuid4().hex[:8], "member": member,
          "start": int(start), "end": int(end)}
    with _reg.mutate() as data:
        rot = (data.get(_reg.norm(tenant)) or {}).get((rot_id or "").strip())
        if not rot:
            return {"ok": False, "reason": "unknown-rotation"}
        rot.setdefault("overrides", []).append(ov)
    return {"ok": True, "id": ov["id"]}


def upcoming(tenant: Optional[str], rot_id: str, count: int = 4,
             when: Optional[int] = None) -> List[dict]:
    when = int(when if when is not None else time.time())
    rot = _get(tenant, rot_id)
    if not rot:
        return []
    shift = rot["shift"]
    # start of the shift currently containing `when`
    n = (when - rot["anchor"]) // shift
    out: List[dict] = []
    for i in range(max(0, int(count))):
        start = rot["anchor"] + (n + i) * shift
        out.append({"start": start, "end": start + shift,
                    "member": _base_member(rot, start)})
    return out
