"""Appeals — due process for people who were blocked, denied, or quarantined.

Automated controls sometimes get it wrong: a legitimate person is locked out, quarantined
on a false spoof signal, or caught by an over-broad block. Fair systems give them a way to
contest it. This subsystem is a lightweight appeals workflow: a subject files an appeal
against a specific action, a reviewer upholds or overturns it with a rationale, and the
outcome is recorded — the audit trail a regulator or ombudsman expects.

  * ``submit``     file an appeal against an action (lockout / quarantine / denial),
                   with the appellant's statement.
  * ``assign``     route an appeal to a reviewer.
  * ``decide``     uphold (block stands) or overturn (block should be lifted), with a
                   required rationale; an overturn signals the caller to release the
                   underlying control.
  * ``status`` / ``queue`` — inspect one, or list open appeals for a reviewer.

Each appellant may have at most one open appeal per action at a time, preventing spam;
a decided appeal is terminal, and a fresh appeal must be filed to contest again.

Registry: ``appeals.json`` (env ``FACE_APPEALS_FILE``).
"""

from __future__ import annotations

import time
import uuid
from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_APPEALS_FILE", "appeals.json")

_ACTIONS = ("lockout", "quarantine", "denial", "block", "other")


def submit(tenant: Optional[str], subject: str, action: str, statement: str,
           reference: str = "", now: Optional[int] = None) -> dict:
    subject = (subject or "").strip()
    action = (action or "").strip().lower()
    statement = (statement or "").strip()
    if not subject or not statement:
        raise ValueError("subject and statement are required.")
    if action not in _ACTIONS:
        raise ValueError(f"action must be one of {_ACTIONS}.")
    now = int(now if now is not None else time.time())
    with _reg.mutate() as data:
        t = data.setdefault(_reg.norm(tenant), {})
        for a in t.values():
            if a["subject"] == subject and a["action"] == action and a["state"] == "open":
                return {"ok": False, "reason": "duplicate-open-appeal", "id": a["id"]}
        aid = "apl_" + uuid.uuid4().hex[:10]
        t[aid] = {"id": aid, "subject": subject, "action": action,
                  "statement": statement, "reference": (reference or "").strip(),
                  "state": "open", "reviewer": None, "decision": None,
                  "rationale": None, "filed": now}
    return {"ok": True, "id": aid}


def assign(tenant: Optional[str], appeal_id: str, reviewer: str) -> bool:
    with _reg.mutate() as data:
        a = (data.get(_reg.norm(tenant)) or {}).get((appeal_id or "").strip())
        if not a or a["state"] != "open":
            return False
        a["reviewer"] = (reviewer or "").strip()
    return True


def decide(tenant: Optional[str], appeal_id: str, overturn: bool, rationale: str,
           by: str = "", now: Optional[int] = None) -> dict:
    rationale = (rationale or "").strip()
    if not rationale:
        raise ValueError("a rationale is required for the decision.")
    now = int(now if now is not None else time.time())
    with _reg.mutate() as data:
        a = (data.get(_reg.norm(tenant)) or {}).get((appeal_id or "").strip())
        if not a:
            return {"ok": False, "reason": "unknown-appeal"}
        if a["state"] != "open":
            return {"ok": False, "reason": "already-decided"}
        a["state"] = "decided"
        a["decision"] = "overturned" if overturn else "upheld"
        a["rationale"] = rationale
        a["decided_by"] = (by or "").strip()
        a["decided_at"] = now
    return {"ok": True, "decision": a["decision"],
            "release_recommended": overturn, "subject": a["subject"],
            "action": a["action"]}


def status(tenant: Optional[str], appeal_id: str) -> dict:
    a = (_reg.load().get(_reg.norm(tenant)) or {}).get((appeal_id or "").strip())
    if not a:
        return {"exists": False}
    return {"exists": True, "id": a["id"], "subject": a["subject"],
            "action": a["action"], "state": a["state"], "decision": a["decision"],
            "reviewer": a["reviewer"]}


def queue(tenant: Optional[str], reviewer: Optional[str] = None) -> List[dict]:
    who = (reviewer or "").strip()
    out = []
    for a in (_reg.load().get(_reg.norm(tenant)) or {}).values():
        if a["state"] != "open":
            continue
        if who and a["reviewer"] != who:
            continue
        out.append({"id": a["id"], "subject": a["subject"], "action": a["action"],
                    "filed": a["filed"]})
    return sorted(out, key=lambda a: a["filed"])
