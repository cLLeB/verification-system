"""Emergency contacts — who to reach for a person during an incident.

When a duress event, medical situation or evacuation involves a specific person, whoever
is responding needs their emergency contacts immediately and in the right order. This
subsystem stores a prioritised contact list per subject and returns it ready to dial —
a small but genuinely operational record that pairs with [[duress]] and [[mustering]].

  * ``add``       a contact for a subject (name, relationship, phone, priority).
  * ``update`` / ``remove`` — maintain the list.
  * ``contacts``  the subject's contacts, ordered by priority then name.
  * ``primary``   the single highest-priority contact (first to call).

Priority is a small integer where lower = called first; ties break by name so the order
is deterministic. Phone numbers are lightly normalised (kept as given, whitespace
trimmed) — this record is for humans to dial, not for machine validation.

Registry: ``emergencycontacts.json`` (env ``FACE_EMERGENCYCONTACTS_FILE``).
"""

from __future__ import annotations

import uuid
from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_EMERGENCYCONTACTS_FILE", "emergencycontacts.json")


def add(tenant: Optional[str], subject: str, name: str, phone: str,
        relationship: str = "", priority: int = 1) -> dict:
    subject = (subject or "").strip()
    name = (name or "").strip()
    phone = (phone or "").strip()
    if not subject or not name or not phone:
        raise ValueError("subject, name and phone are required.")
    contact = {"id": "ec_" + uuid.uuid4().hex[:8], "name": name, "phone": phone,
               "relationship": (relationship or "").strip(), "priority": int(priority)}
    with _reg.mutate() as data:
        data.setdefault(_reg.norm(tenant), {}).setdefault(subject, []).append(contact)
    return {"id": contact["id"], "subject": subject}


def _list(tenant: Optional[str], subject: str) -> List[dict]:
    return (_reg.load().get(_reg.norm(tenant)) or {}).get((subject or "").strip(), [])


def contacts(tenant: Optional[str], subject: str) -> List[dict]:
    return sorted(_list(tenant, subject),
                  key=lambda c: (c["priority"], c["name"].lower()))


def primary(tenant: Optional[str], subject: str) -> Optional[dict]:
    ordered = contacts(tenant, subject)
    return ordered[0] if ordered else None


def update(tenant: Optional[str], subject: str, contact_id: str, **fields) -> bool:
    allowed = {"name", "phone", "relationship", "priority"}
    with _reg.mutate() as data:
        lst = (data.get(_reg.norm(tenant)) or {}).get((subject or "").strip(), [])
        for c in lst:
            if c["id"] == (contact_id or "").strip():
                for k, v in fields.items():
                    if k not in allowed:
                        continue
                    if k == "priority":
                        c[k] = int(v)
                    elif k in ("name", "phone") and not str(v).strip():
                        raise ValueError(f"{k} cannot be empty.")
                    else:
                        c[k] = str(v).strip()
                return True
    return False


def remove(tenant: Optional[str], subject: str, contact_id: str) -> bool:
    cid = (contact_id or "").strip()
    with _reg.mutate() as data:
        t = data.get(_reg.norm(tenant)) or {}
        subj = (subject or "").strip()
        lst = t.get(subj, [])
        kept = [c for c in lst if c["id"] != cid]
        if len(kept) == len(lst):
            return False
        if kept:
            t[subj] = kept
        else:
            del t[subj]
    return True
