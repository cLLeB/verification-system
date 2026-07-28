"""Escalation policies - page the next tier when an alert goes unacknowledged.

An alert (a duress trigger, a tamper event, a spoof spike) is only useful if a
human actually responds. Escalation policies encode "notify tier 1; if nobody
acknowledges within N minutes, escalate to tier 2; then tier 3" so an unattended
incident climbs the chain instead of dying in an inbox.

  * ``define``      a policy: ordered tiers, each with recipients and a timeout.
  * ``trigger``     open an incident on a policy; notifies tier 0 immediately.
  * ``acknowledge`` a responder claims the incident; escalation stops.
  * ``due``         given the current time, which incidents have breached their
                    tier timeout and must advance - returns the tier to notify
                    now (and records the advance) or marks the chain exhausted.
  * ``resolve`` / ``status`` - close and inspect.

``due`` is pull-based and deterministic: the caller runs it on a timer, and it
advances each unacknowledged incident by exactly the tiers whose timeouts have
elapsed, returning the recipients to page. This keeps the module pure - it never
sends anything itself.

Registry: ``escalation.json`` (env ``FACE_ESCALATION_FILE``).
"""

from __future__ import annotations

import time
import uuid
from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_ESCALATION_FILE", "escalation.json")


def define(tenant: Optional[str], name: str, tiers: List[dict]) -> dict:
    """Each tier: {"recipients": [...], "timeout": seconds}. Last tier's timeout
    is ignored (nowhere left to escalate)."""
    name = (name or "").strip()
    if not name:
        raise ValueError("policy name is required.")
    clean: List[dict] = []
    for i, tr in enumerate(tiers or []):
        rcpts = sorted({(r or "").strip() for r in tr.get("recipients", [])
                        if (r or "").strip()})
        if not rcpts:
            raise ValueError(f"tier {i} has no recipients.")
        clean.append({"recipients": rcpts, "timeout": int(tr.get("timeout", 300))})
    if not clean:
        raise ValueError("a policy needs at least one tier.")
    pol = {"id": "esc_" + uuid.uuid4().hex[:8], "name": name, "tiers": clean}
    with _reg.mutate() as data:
        data.setdefault(_reg.norm(tenant), {"policies": {}, "incidents": {}})["policies"][pol["id"]] = pol
    return {"id": pol["id"], "name": name, "tiers": len(clean)}


def _root(data: dict, tenant: Optional[str]) -> dict:
    return data.setdefault(_reg.norm(tenant), {"policies": {}, "incidents": {}})


def trigger(tenant: Optional[str], policy_id: str, subject: str = "",
            now: Optional[int] = None) -> dict:
    now = int(now if now is not None else time.time())
    with _reg.mutate() as data:
        root = _root(data, tenant)
        pol = root["policies"].get((policy_id or "").strip())
        if not pol:
            return {"ok": False, "reason": "unknown-policy"}
        inc = {"id": "inc_" + uuid.uuid4().hex[:10], "policy": pol["id"],
               "subject": (subject or "").strip(), "opened": now, "tier": 0,
               "tier_since": now, "acked": None, "resolved": None,
               "exhausted": False}
        root["incidents"][inc["id"]] = inc
        rcpts = pol["tiers"][0]["recipients"]
    return {"ok": True, "id": inc["id"], "tier": 0, "notify": rcpts}


def acknowledge(tenant: Optional[str], incident_id: str, who: str,
                now: Optional[int] = None) -> bool:
    now = int(now if now is not None else time.time())
    with _reg.mutate() as data:
        inc = _root(data, tenant)["incidents"].get((incident_id or "").strip())
        if not inc or inc["acked"] or inc["resolved"]:
            return False
        inc["acked"] = {"by": (who or "").strip(), "at": now}
    return True


def due(tenant: Optional[str], now: Optional[int] = None) -> List[dict]:
    """Advance any unacknowledged incident whose current tier has timed out.

    Returns one entry per incident that moved, naming the tier now responsible
    and whom to page (empty ``notify`` when the chain is exhausted).
    """
    now = int(now if now is not None else time.time())
    out: List[dict] = []
    with _reg.mutate() as data:
        root = _root(data, tenant)
        for inc in root["incidents"].values():
            if inc["acked"] or inc["resolved"] or inc["exhausted"]:
                continue
            pol = root["policies"].get(inc["policy"])
            if not pol:
                continue
            moved = False
            while inc["tier"] < len(pol["tiers"]) - 1:
                timeout = pol["tiers"][inc["tier"]]["timeout"]
                if now - inc["tier_since"] < timeout:
                    break
                inc["tier"] += 1
                inc["tier_since"] = inc["tier_since"] + timeout
                moved = True
            if moved:
                out.append({"incident": inc["id"], "tier": inc["tier"],
                            "notify": pol["tiers"][inc["tier"]]["recipients"]})
            elif inc["tier"] == len(pol["tiers"]) - 1:
                timeout = pol["tiers"][inc["tier"]]["timeout"]
                if now - inc["tier_since"] >= timeout:
                    inc["exhausted"] = True
                    out.append({"incident": inc["id"], "tier": inc["tier"],
                                "notify": [], "exhausted": True})
    return out


def resolve(tenant: Optional[str], incident_id: str, now: Optional[int] = None) -> bool:
    now = int(now if now is not None else time.time())
    with _reg.mutate() as data:
        inc = _root(data, tenant)["incidents"].get((incident_id or "").strip())
        if not inc or inc["resolved"]:
            return False
        inc["resolved"] = now
    return True


def status(tenant: Optional[str], incident_id: str) -> dict:
    inc = (_reg.load().get(_reg.norm(tenant), {}).get("incidents") or {}).get(
        (incident_id or "").strip())
    if not inc:
        return {"exists": False}
    state = ("resolved" if inc["resolved"] else "acked" if inc["acked"]
             else "exhausted" if inc["exhausted"] else "active")
    return {"exists": True, "id": inc["id"], "tier": inc["tier"],
            "state": state, "acked": inc["acked"], "subject": inc["subject"]}
