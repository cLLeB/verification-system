"""Finite state machines — a reusable engine for constrained lifecycles.

Many subsystems here have a lifecycle with legal and illegal transitions: [[tickets]]
(open→in_progress→resolved→closed), [[appeals]], [[invoicing]], [[kyc]], [[circuitbreaker]].
Each hand-rolls its transition rules. This subsystem factors that out into a reusable FSM:
define the states and allowed transitions once, then drive instances through events, with
illegal transitions rejected and a full transition history kept for audit.

  * ``define``          a machine: states, an initial state, and ``event`` transitions
                        ``{"from": s, "event": e, "to": t}``.
  * ``create_instance`` start an instance of a machine at its initial state.
  * ``fire``            apply an event to an instance; advances state or rejects.
  * ``state`` / ``allowed_events`` / ``history`` — inspect an instance.

A wildcard ``from`` of ``*`` makes an event valid from any state (e.g. a ``cancel`` that can
happen anytime). Transitions are validated at ``define`` time against the declared states so
a typo can't create an unreachable rule.

Registry: ``statemachine.json`` (env ``FACE_STATEMACHINE_FILE``).
"""

from __future__ import annotations

import time
import uuid
from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_STATEMACHINE_FILE", "statemachine.json")


def _root(data: dict, tenant: Optional[str]) -> dict:
    return data.setdefault(_reg.norm(tenant), {"machines": {}, "instances": {}})


def define(tenant: Optional[str], name: str, states: List[str], initial: str,
           transitions: List[dict]) -> dict:
    name = (name or "").strip()
    if not name:
        raise ValueError("machine name is required.")
    states = [s.strip() for s in (states or []) if (s or "").strip()]
    if len(states) < 1:
        raise ValueError("at least one state is required.")
    initial = (initial or "").strip()
    if initial not in states:
        raise ValueError("initial must be one of the states.")
    trans = []
    for t in transitions or []:
        frm = (t.get("from") or "").strip()
        evt = (t.get("event") or "").strip()
        to = (t.get("to") or "").strip()
        if not evt or not to:
            raise ValueError("transition needs 'event' and 'to'.")
        if to not in states:
            raise ValueError(f"transition target not a state: {to}")
        if frm != "*" and frm not in states:
            raise ValueError(f"transition source not a state: {frm}")
        trans.append({"from": frm, "event": evt, "to": to})
    with _reg.mutate() as data:
        _root(data, tenant)["machines"][name] = {"name": name, "states": states,
                                                 "initial": initial, "transitions": trans}
    return {"name": name, "states": states, "initial": initial}


def create_instance(tenant: Optional[str], machine: str, instance_id: Optional[str] = None,
                    now: Optional[int] = None) -> dict:
    machine = (machine or "").strip()
    now = int(now if now is not None else time.time())
    with _reg.mutate() as data:
        root = _root(data, tenant)
        m = root["machines"].get(machine)
        if not m:
            return {"ok": False, "reason": "unknown-machine"}
        iid = (instance_id or "").strip() or ("fsm_" + uuid.uuid4().hex[:10])
        root["instances"][iid] = {"id": iid, "machine": machine, "state": m["initial"],
                                  "history": [], "created": now}
    return {"ok": True, "id": iid, "state": m["initial"]}


def _find_transition(m: dict, state: str, event: str) -> Optional[dict]:
    for t in m["transitions"]:
        if t["event"] == event and (t["from"] == state or t["from"] == "*"):
            return t
    return None


def fire(tenant: Optional[str], instance_id: str, event: str,
         now: Optional[int] = None) -> dict:
    event = (event or "").strip()
    now = int(now if now is not None else time.time())
    with _reg.mutate() as data:
        root = _root(data, tenant)
        inst = root["instances"].get((instance_id or "").strip())
        if not inst:
            return {"ok": False, "reason": "unknown-instance"}
        m = root["machines"][inst["machine"]]
        t = _find_transition(m, inst["state"], event)
        if not t:
            return {"ok": False, "reason": "illegal-transition",
                    "state": inst["state"], "event": event}
        prev = inst["state"]
        inst["state"] = t["to"]
        inst["history"].append({"from": prev, "event": event, "to": t["to"], "at": now})
        return {"ok": True, "from": prev, "to": t["to"]}


def state(tenant: Optional[str], instance_id: str) -> dict:
    inst = (_reg.load().get(_reg.norm(tenant), {}).get("instances") or {}).get(
        (instance_id or "").strip())
    if not inst:
        return {"exists": False}
    return {"exists": True, "id": inst["id"], "machine": inst["machine"],
            "state": inst["state"]}


def allowed_events(tenant: Optional[str], instance_id: str) -> List[str]:
    root = _reg.load().get(_reg.norm(tenant)) or {"machines": {}, "instances": {}}
    inst = (root.get("instances") or {}).get((instance_id or "").strip())
    if not inst:
        return []
    m = root["machines"][inst["machine"]]
    return sorted({t["event"] for t in m["transitions"]
                   if t["from"] == inst["state"] or t["from"] == "*"})


def history(tenant: Optional[str], instance_id: str) -> List[dict]:
    inst = (_reg.load().get(_reg.norm(tenant), {}).get("instances") or {}).get(
        (instance_id or "").strip())
    return list(inst["history"]) if inst else []
