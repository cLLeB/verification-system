"""Device tamper monitoring — trust readings only from sealed, untampered readers.

An access reader is only trustworthy if nobody has opened its enclosure, swapped its
camera, or spliced its wiring. Devices report a tamper switch and an enclosure-seal
value on check-in; this subsystem tracks that state, raises a tamper event when the
seal breaks or the switch trips, and lets a gate refuse to honour verifications from a
reader currently in a tampered state — a physical-security control that biometric
matching alone can't provide.

  * ``commission``  register a device with its expected seal value.
  * ``report``      a device check-in with (tamper_switch, seal); trips or clears a
                    tamper condition and logs the transition.
  * ``status``      current tamper state and history count for a device.
  * ``clear``       an operator resolves a tamper after physical inspection (records
                    who, and requires the seal to be re-set).
  * ``gate``        post-match helper: deny a verification captured on a tampered
                    device, regardless of the biometric result.

A tamper, once tripped, stays latched until an operator clears it even if the switch
returns to normal — so a brief intrusion can't silently self-heal and hide itself.

Registry: ``tamper.json`` (env ``FACE_TAMPER_FILE``).
"""

from __future__ import annotations

import time
from typing import Optional

from ._registry import Registry

_reg = Registry("FACE_TAMPER_FILE", "tamper.json")


def commission(tenant: Optional[str], device: str, seal: str) -> dict:
    device = (device or "").strip()
    seal = (seal or "").strip()
    if not device or not seal:
        raise ValueError("device and seal are required.")
    rec = {"device": device, "seal": seal, "tampered": False,
           "events": 0, "last_report": None, "cleared_by": None}
    with _reg.mutate() as data:
        data.setdefault(_reg.norm(tenant), {})[device] = rec
    return {"device": device, "sealed": True}


def _get(data: dict, tenant: Optional[str], device: str) -> Optional[dict]:
    return (data.get(_reg.norm(tenant)) or {}).get((device or "").strip())


def report(tenant: Optional[str], device: str, tamper_switch: bool = False,
           seal: Optional[str] = None, now: Optional[int] = None) -> dict:
    now = int(now if now is not None else time.time())
    with _reg.mutate() as data:
        rec = _get(data, tenant, device)
        if not rec:
            return {"ok": False, "reason": "unknown-device"}
        rec["last_report"] = now
        seal_broken = seal is not None and (seal or "").strip() != rec["seal"]
        tripped_now = bool(tamper_switch) or seal_broken
        newly = tripped_now and not rec["tampered"]
        if tripped_now:
            rec["tampered"] = True                 # latch on
            if newly:
                rec["events"] += 1
        return {"ok": True, "tampered": rec["tampered"], "newly_tripped": newly,
                "reason": ("seal-mismatch" if seal_broken else
                           "switch" if tamper_switch else None)}


def status(tenant: Optional[str], device: str) -> dict:
    rec = (_reg.load().get(_reg.norm(tenant)) or {}).get((device or "").strip())
    if not rec:
        return {"exists": False}
    return {"exists": True, "device": rec["device"], "tampered": rec["tampered"],
            "events": rec["events"], "last_report": rec["last_report"]}


def clear(tenant: Optional[str], device: str, operator: str, new_seal: str) -> dict:
    operator = (operator or "").strip()
    new_seal = (new_seal or "").strip()
    if not operator:
        raise ValueError("clearing operator must be recorded.")
    if not new_seal:
        raise ValueError("a new seal value is required when clearing.")
    with _reg.mutate() as data:
        rec = _get(data, tenant, device)
        if not rec:
            return {"ok": False, "reason": "unknown-device"}
        if not rec["tampered"]:
            return {"ok": False, "reason": "not-tampered"}
        rec["tampered"] = False
        rec["seal"] = new_seal
        rec["cleared_by"] = operator
    return {"ok": True, "device": (device or "").strip()}


def gate(tenant: Optional[str], result: dict, device: str) -> dict:
    """Refuse to honour a match captured on a tampered reader."""
    out = dict(result)
    st = status(tenant, device)
    if st.get("exists") and st["tampered"]:
        out["success"] = False
        out["code"] = "DEVICE_TAMPERED"
        out["message"] = "Reader is flagged as tampered; verification withheld."
    return out
