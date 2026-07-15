"""Visitor pre-registration — expect a guest, then check them in on arrival.

Front desks run smoother when visitors are expected: a host pre-registers a guest
for a day with the person they are here to see, the guest gets a reference code,
and on arrival reception (or a self-service kiosk) checks them in against the
expected list. Unexpected arrivals are flagged; no-shows are visible at end of day.

  * ``register``  expect a visitor (name, host, date, ref auto-issued).
  * ``check_in``  mark an expected visitor arrived (by ref or name+date).
  * ``check_out`` mark them departed.
  * ``expected`` / ``on_site`` / ``no_shows`` — the desk's live views.

This is scheduling metadata, distinct from the biometric [[invites]] flow (which
enrols a template). A pre-registration can carry an invite token for self-enrol,
but on its own it is just "we are expecting this person".

Registry: ``previsit.json`` (env ``FACE_PREVISIT_FILE``).
"""

from __future__ import annotations

import time
import uuid
from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_PREVISIT_FILE", "previsit.json")


def register(tenant: Optional[str], visitor: str, host: str, date: str,
             company: str = "") -> dict:
    visitor = (visitor or "").strip()
    host = (host or "").strip()
    date = (date or "").strip()
    if not visitor or not host or not date:
        raise ValueError("visitor, host and date are required.")
    rec = {"ref": "pv_" + uuid.uuid4().hex[:10], "visitor": visitor, "host": host,
           "date": date, "company": company or "", "status": "expected",
           "checked_in": None, "checked_out": None, "created": int(time.time())}
    with _reg.mutate() as data:
        data.setdefault(_reg.norm(tenant), {})[rec["ref"]] = rec
    return dict(rec)


def _find(data_t: dict, ref: str) -> Optional[str]:
    return ref if ref in data_t else None


def check_in(tenant: Optional[str], ref: str, now: Optional[int] = None) -> dict:
    t = _reg.norm(tenant)
    now = int(now if now is not None else time.time())
    with _reg.mutate() as data:
        rec = (data.get(t) or {}).get((ref or "").strip())
        if not rec:
            return {"status": "unknown", "flagged": True}
        rec["status"] = "on_site"
        rec["checked_in"] = now
        return dict(rec)


def check_out(tenant: Optional[str], ref: str, now: Optional[int] = None) -> bool:
    t = _reg.norm(tenant)
    now = int(now if now is not None else time.time())
    with _reg.mutate() as data:
        rec = (data.get(t) or {}).get((ref or "").strip())
        if not rec or rec["status"] != "on_site":
            return False
        rec["status"] = "departed"
        rec["checked_out"] = now
    return True


def _by_status(tenant: Optional[str], date: str, status: str) -> List[dict]:
    return [dict(r) for r in (_reg.load().get(_reg.norm(tenant)) or {}).values()
            if r["date"] == date and r["status"] == status]


def expected(tenant: Optional[str], date: str) -> List[dict]:
    return _by_status(tenant, date, "expected")


def on_site(tenant: Optional[str], date: str) -> List[dict]:
    return _by_status(tenant, date, "on_site")


def no_shows(tenant: Optional[str], date: str) -> List[dict]:
    return _by_status(tenant, date, "expected")
