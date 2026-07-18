"""Device provisioning — onboard readers with one-time claim codes.

Rolling out a fleet of capture devices needs a safe bootstrap: an operator pre-creates a
provisioning code, the device is powered on and claims the code once to receive its
identity and configuration, and the code is then spent so it can't be reused. This
subsystem is that enrolment handshake for *devices* (as [[ssohandoff]] is for people),
handing back the config a device needs to join a [[devicegroups]] policy set.

  * ``issue``    create a one-time provisioning code for a device model, carrying the
                 config to deliver, with an expiry.
  * ``claim``    a device redeems a code once, receiving its config and being recorded
                 as provisioned; a second claim (or after expiry) is rejected.
  * ``revoke``   invalidate an unclaimed code.
  * ``devices``  list provisioned devices; ``pending`` lists unclaimed codes.

A claimed code is bound to the claiming device id, so re-presenting it — even by the same
device — returns the stored config idempotently rather than provisioning a second device.

Registry: ``provisioning.json`` (env ``FACE_PROVISIONING_FILE``).
"""

from __future__ import annotations

import secrets as _secrets
import time
from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_PROVISIONING_FILE", "provisioning.json")


def _root(data: dict, tenant: Optional[str]) -> dict:
    return data.setdefault(_reg.norm(tenant), {"codes": {}, "devices": {}})


def issue(tenant: Optional[str], model: str = "", config: Optional[dict] = None,
          ttl: int = 86400, now: Optional[int] = None) -> dict:
    if int(ttl) <= 0:
        raise ValueError("ttl must be positive.")
    now = int(now if now is not None else time.time())
    code = "prov_" + _secrets.token_hex(5)
    rec = {"code": code, "model": (model or "").strip(), "config": config or {},
           "expires": now + int(ttl), "claimed_by": None, "issued": now}
    with _reg.mutate() as data:
        _root(data, tenant)["codes"][code] = rec
    return {"code": code, "expires": rec["expires"]}


def claim(tenant: Optional[str], code: str, device_id: str,
          now: Optional[int] = None) -> dict:
    code = (code or "").strip()
    device_id = (device_id or "").strip()
    if not device_id:
        raise ValueError("device_id is required.")
    now = int(now if now is not None else time.time())
    with _reg.mutate() as data:
        root = _root(data, tenant)
        rec = root["codes"].get(code)
        if not rec:
            return {"ok": False, "reason": "unknown-code"}
        if rec["claimed_by"] is not None:
            if rec["claimed_by"] == device_id:
                return {"ok": True, "config": rec["config"], "model": rec["model"],
                        "idempotent": True}
            return {"ok": False, "reason": "already-claimed"}
        if now >= rec["expires"]:
            return {"ok": False, "reason": "expired"}
        rec["claimed_by"] = device_id
        rec["claimed_at"] = now
        root["devices"][device_id] = {"device_id": device_id, "model": rec["model"],
                                      "config": rec["config"], "provisioned": now,
                                      "code": code}
    return {"ok": True, "config": rec["config"], "model": rec["model"],
            "idempotent": False}


def revoke(tenant: Optional[str], code: str) -> bool:
    with _reg.mutate() as data:
        rec = _root(data, tenant)["codes"].get((code or "").strip())
        if not rec or rec["claimed_by"] is not None:
            return False
        rec["expires"] = 0            # force-expire
    return True


def devices(tenant: Optional[str]) -> List[dict]:
    root = _reg.load().get(_reg.norm(tenant)) or {"devices": {}}
    return sorted(({"device_id": d["device_id"], "model": d["model"],
                    "provisioned": d["provisioned"]}
                   for d in root["devices"].values()), key=lambda d: d["device_id"])


def pending(tenant: Optional[str], now: Optional[int] = None) -> List[dict]:
    now = int(now if now is not None else time.time())
    root = _reg.load().get(_reg.norm(tenant)) or {"codes": {}}
    return sorted(({"code": c["code"], "model": c["model"], "expires": c["expires"]}
                   for c in root["codes"].values()
                   if c["claimed_by"] is None and c["expires"] > now),
                  key=lambda c: c["expires"])
