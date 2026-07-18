"""Incident post-mortems — capture what happened and what will change.

After a significant incident (a spoof that got through, an outage, a mass false-reject),
a blameless post-mortem records the timeline, the root cause, and the follow-up actions
that stop it recurring. Crucially, the post-mortem isn't "done" until its action items
are — this subsystem tracks those to closure so lessons actually land. It complements
[[tickets]] (day-to-day work) and [[escalation]] (the live response) as the learning
record.

  * ``open``           start a post-mortem for an incident with a summary.
  * ``add_event``      append a timeline entry (time, description).
  * ``set_root_cause`` record the analysed cause.
  * ``add_action``     a corrective action with an owner and due date.
  * ``complete_action`` close an action item.
  * ``status``         completeness: has a root cause, open vs done actions, and
                       whether the post-mortem can be considered closed.

A post-mortem is ``closed`` only when it has a root cause and every action item is done —
encoding the discipline that a review with open actions is still live work.

Registry: ``postmortem.json`` (env ``FACE_POSTMORTEM_FILE``).
"""

from __future__ import annotations

import time
import uuid
from typing import Optional

from ._registry import Registry

_reg = Registry("FACE_POSTMORTEM_FILE", "postmortem.json")


def open(tenant: Optional[str], title: str, severity: str = "sev2",
         now: Optional[int] = None) -> dict:
    title = (title or "").strip()
    if not title:
        raise ValueError("title is required.")
    now = int(now if now is not None else time.time())
    pm = {"id": "pm_" + uuid.uuid4().hex[:10], "title": title,
          "severity": (severity or "sev2").strip(), "timeline": [],
          "root_cause": None, "actions": {}, "opened": now}
    with _reg.mutate() as data:
        data.setdefault(_reg.norm(tenant), {})[pm["id"]] = pm
    return {"id": pm["id"], "title": title}


def _get(data: dict, tenant: Optional[str], pid: str) -> Optional[dict]:
    return (data.get(_reg.norm(tenant)) or {}).get((pid or "").strip())


def add_event(tenant: Optional[str], pm_id: str, when: int, description: str) -> bool:
    description = (description or "").strip()
    if not description:
        raise ValueError("event description is required.")
    with _reg.mutate() as data:
        pm = _get(data, tenant, pm_id)
        if not pm:
            return False
        pm["timeline"].append({"when": int(when), "description": description})
        pm["timeline"].sort(key=lambda e: e["when"])
    return True


def set_root_cause(tenant: Optional[str], pm_id: str, cause: str) -> bool:
    cause = (cause or "").strip()
    if not cause:
        raise ValueError("root cause is required.")
    with _reg.mutate() as data:
        pm = _get(data, tenant, pm_id)
        if not pm:
            return False
        pm["root_cause"] = cause
    return True


def add_action(tenant: Optional[str], pm_id: str, description: str, owner: str,
               due: Optional[int] = None) -> dict:
    description = (description or "").strip()
    owner = (owner or "").strip()
    if not description or not owner:
        raise ValueError("action description and owner are required.")
    with _reg.mutate() as data:
        pm = _get(data, tenant, pm_id)
        if not pm:
            return {"ok": False, "reason": "unknown-postmortem"}
        aid = "act_" + uuid.uuid4().hex[:6]
        pm["actions"][aid] = {"id": aid, "description": description, "owner": owner,
                              "due": int(due) if due is not None else None,
                              "done": False}
    return {"ok": True, "action_id": aid}


def complete_action(tenant: Optional[str], pm_id: str, action_id: str,
                    now: Optional[int] = None) -> bool:
    now = int(now if now is not None else time.time())
    with _reg.mutate() as data:
        pm = _get(data, tenant, pm_id)
        if not pm:
            return False
        act = pm["actions"].get((action_id or "").strip())
        if not act or act["done"]:
            return False
        act["done"] = True
        act["completed_at"] = now
    return True


def status(tenant: Optional[str], pm_id: str) -> dict:
    pm = (_reg.load().get(_reg.norm(tenant)) or {}).get((pm_id or "").strip())
    if not pm:
        return {"exists": False}
    actions = pm["actions"].values()
    open_actions = [a["id"] for a in actions if not a["done"]]
    has_cause = pm["root_cause"] is not None
    return {"exists": True, "id": pm["id"], "title": pm["title"],
            "has_root_cause": has_cause, "timeline_entries": len(pm["timeline"]),
            "total_actions": len(pm["actions"]), "open_actions": sorted(open_actions),
            "closed": has_cause and not open_actions}
