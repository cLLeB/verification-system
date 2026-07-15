"""SLA timers — hold operational items to a deadline and surface breaches.

Operational work triggered by this platform has clocks on it: an access request
awaiting approval, a flagged verify awaiting review, a device-down ticket awaiting
a fix. This subsystem starts a timer when such an item opens, tied to a target
duration; at any moment it can list what is due soon and what has breached, and it
records the actual resolution time when the item closes — the raw material for an
SLA report.

  * ``start``    open a timer for an item (class -> default target, overridable).
  * ``stop``     close it; returns elapsed + whether it met the target.
  * ``breached`` open items already past target; ``due_soon`` within a margin.
  * ``report``   met/breached counts and average resolution per class.

Registry: ``sla.json`` (env ``FACE_SLA_FILE``).
"""

from __future__ import annotations

import time
from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_SLA_FILE", "sla.json")


def _doc(data: dict, tenant: str) -> dict:
    d = data.setdefault(tenant, {})
    d.setdefault("targets", {})    # class -> seconds
    d.setdefault("open", {})       # item_id -> {class, start, target}
    d.setdefault("closed", [])     # {class, elapsed, met}
    return d


def set_target(tenant: Optional[str], item_class: str, seconds: int) -> int:
    with _reg.mutate() as data:
        _doc(data, _reg.norm(tenant))["targets"][(item_class or "").strip()] = max(1, int(seconds))
    return max(1, int(seconds))


def start(tenant: Optional[str], item_id: str, item_class: str,
          target: Optional[int] = None, now: Optional[int] = None) -> dict:
    iid = (item_id or "").strip()
    cls = (item_class or "").strip()
    if not iid or not cls:
        raise ValueError("item_id and item_class are required.")
    now = int(now if now is not None else time.time())
    t = _reg.norm(tenant)
    with _reg.mutate() as data:
        doc = _doc(data, t)
        tgt = int(target) if target is not None else doc["targets"].get(cls, 3600)
        doc["open"][iid] = {"class": cls, "start": now, "target": tgt}
    return {"item_id": iid, "class": cls, "due_at": now + tgt}


def stop(tenant: Optional[str], item_id: str, now: Optional[int] = None) -> dict:
    t = _reg.norm(tenant)
    now = int(now if now is not None else time.time())
    with _reg.mutate() as data:
        doc = _doc(data, t)
        item = doc["open"].pop((item_id or "").strip(), None)
        if not item:
            return {"item_id": (item_id or "").strip(), "status": "unknown"}
        elapsed = now - item["start"]
        met = elapsed <= item["target"]
        doc["closed"].append({"class": item["class"], "elapsed": elapsed, "met": met})
    return {"item_id": (item_id or "").strip(), "elapsed": elapsed, "met": met}


def breached(tenant: Optional[str], now: Optional[int] = None) -> List[dict]:
    now = int(now if now is not None else time.time())
    doc = _doc(_reg.load(), _reg.norm(tenant))
    return sorted(({"item_id": iid, "class": it["class"],
                    "over_by": now - it["start"] - it["target"]}
                   for iid, it in doc["open"].items()
                   if now - it["start"] > it["target"]),
                  key=lambda r: -r["over_by"])


def due_soon(tenant: Optional[str], margin: int = 300,
             now: Optional[int] = None) -> List[dict]:
    now = int(now if now is not None else time.time())
    doc = _doc(_reg.load(), _reg.norm(tenant))
    out = []
    for iid, it in doc["open"].items():
        remaining = it["start"] + it["target"] - now
        if 0 <= remaining <= margin:
            out.append({"item_id": iid, "class": it["class"], "remaining": remaining})
    return sorted(out, key=lambda r: r["remaining"])


def report(tenant: Optional[str]) -> dict:
    doc = _doc(_reg.load(), _reg.norm(tenant))
    by: dict = {}
    for c in doc["closed"]:
        b = by.setdefault(c["class"], {"met": 0, "breached": 0, "total_elapsed": 0, "n": 0})
        b["met" if c["met"] else "breached"] += 1
        b["total_elapsed"] += c["elapsed"]
        b["n"] += 1
    for cls, b in by.items():
        b["avg_elapsed"] = round(b["total_elapsed"] / b["n"], 1) if b["n"] else 0
        del b["total_elapsed"]
    return by
