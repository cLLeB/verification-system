"""Device registry — kiosks and verifier phones as first-class citizens.

Until now the platform only knew *keys*: any number of kiosks could share one
verify key, and nothing recorded which physical device did what, when it was
last alive, or how to cut ONE stolen kiosk off without rotating a whole fleet.
This registry gives every device its own identity:

  * **Pairing** — an admin/portal mints a short-lived, single-use pairing code
    (``pc_`` + 160-bit token, stored hashed — the invite pattern). The device
    redeems it once at ``POST /v1/devices/pair`` (the code IS the auth) and
    receives its ``device_id`` plus its OWN freshly-minted verify key. Raw
    code and key are returned exactly once each.
  * **Device-bound key** — the key is created via [[keys]] with the device
    name, so audit ``actor`` fields already attribute traffic per device.
  * **Heartbeat** — devices report in on ``/v1/devices/heartbeat`` (app
    version, platform, battery, whatever fits in ``info``); the console shows
    last-seen so a dead kiosk is visible before someone walks up to it.
  * **Disable = revoke** — disabling a device revokes its key immediately
    (via ``keys.revoke_key``), with zero added cost on the request path: an
    unpaired/disabled device simply has no valid key.

Registry: ``devices.json`` (env ``FACE_DEVICES_FILE``), same JSON/lock/env
pattern as [[keys]] / [[invites]].
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
import time
from typing import List, Optional

DEVICES_FILE = os.environ.get("FACE_DEVICES_FILE", "devices.json")

PAIRING_TTL_SECONDS = 15 * 60          # a pairing code is a 15-minute, one-shot secret

_lock = threading.Lock()


def _hash(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def _load() -> dict:
    if not os.path.exists(DEVICES_FILE):
        return {"devices": {}, "pairings": {}}
    try:
        with open(DEVICES_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        data.setdefault("devices", {})
        data.setdefault("pairings", {})
        return data
    except (OSError, ValueError):
        return {"devices": {}, "pairings": {}}


def _save(data: dict) -> None:
    with open(DEVICES_FILE, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    try:
        os.chmod(DEVICES_FILE, 0o600)
    except OSError:
        pass


def _norm(tenant: Optional[str]) -> str:
    return (tenant or "default").strip() or "default"


# --- pairing ------------------------------------------------------------------
def create_pairing(tenant: Optional[str], name: str, by: str = "") -> dict:
    """Mint a pairing code for a named device slot. The RAW code is returned
    once (only its hash is stored); it expires in 15 minutes and burns on use."""
    name = (name or "").strip()
    if not name:
        raise ValueError("A device name is required (e.g. 'Front gate kiosk').")
    t = _norm(tenant)
    raw = "pc_" + secrets.token_urlsafe(20)
    rec = {"device_id": "dv_" + secrets.token_hex(5), "tenant": t, "name": name,
           "created": int(time.time()),
           "expires": int(time.time() + PAIRING_TTL_SECONDS),
           "created_by": by or "", "used": None}
    with _lock:
        data = _load()
        data["pairings"][_hash(raw)] = rec
        _save(data)
    return {"pairing_code": raw, "device_id": rec["device_id"], "tenant": t,
            "name": name, "expires": rec["expires"]}


def redeem(code: str, key_minter) -> Optional[dict]:
    """Exchange a live pairing code for the device record + its own verify key.
    ``key_minter(name, tenant)`` mints the key (injected so this module stays
    free of a hard [[keys]] dependency and tests can fake it). Returns None for
    an unknown / expired / already-used code — the caller answers 404 and the
    failed attempt costs an attacker one rate-limited request."""
    if not code:
        return None
    h = _hash(code)
    with _lock:
        data = _load()
        rec = data["pairings"].get(h)
        if rec is None or rec.get("used") or time.time() > rec["expires"]:
            return None
        rec["used"] = int(time.time())
        key = key_minter(rec["name"], rec["tenant"])
        device = {"device_id": rec["device_id"], "tenant": rec["tenant"],
                  "name": rec["name"], "paired_at": int(time.time()),
                  "key_id": key["key_id"], "disabled": False,
                  "last_seen": None, "info": {}}
        data["devices"][rec["device_id"]] = device
        _save(data)
    return {"device_id": device["device_id"], "tenant": device["tenant"],
            "name": device["name"], "api_key": key["api_key"],
            "key_id": key["key_id"], "signing_secret": key.get("signing_secret")}


# --- fleet state ----------------------------------------------------------------
def heartbeat(device_id: str, tenant: Optional[str],
              info: Optional[dict] = None) -> Optional[dict]:
    """Record a device check-in. The caller has already authenticated with the
    device's key; the tenant must match so one tenant's key can never touch
    another tenant's device row. Returns the fresh record, or None."""
    t = _norm(tenant)
    with _lock:
        data = _load()
        dev = data["devices"].get((device_id or "").strip())
        if dev is None or dev["tenant"] != t or dev.get("disabled"):
            return None
        dev["last_seen"] = int(time.time())
        if isinstance(info, dict):
            # keep it small: a heartbeat is a status ping, not a log sink
            dev["info"] = {str(k)[:40]: str(v)[:200] for k, v in list(info.items())[:12]}
        _save(data)
        return dict(dev)


def get(device_id: str) -> Optional[dict]:
    dev = _load()["devices"].get((device_id or "").strip())
    return dict(dev) if dev else None


def for_key(key_id: str) -> Optional[dict]:
    """The device a key belongs to, if any (audit attribution, heartbeat auth)."""
    if not key_id:
        return None
    for dev in _load()["devices"].values():
        if dev.get("key_id") == key_id:
            return dict(dev)
    return None


def list_for(tenant: Optional[str]) -> List[dict]:
    t = _norm(tenant)
    out = [dict(d) for d in _load()["devices"].values() if d["tenant"] == t]
    return sorted(out, key=lambda d: d["name"].lower())


def disable(device_id: str, tenant: Optional[str], key_revoker) -> Optional[dict]:
    """Disable a device and revoke its key via ``key_revoker(key_id)`` — the
    kiosk is cut off at the next request. The row stays for the audit trail.
    Returns the updated record, or None if the device isn't this tenant's."""
    t = _norm(tenant)
    with _lock:
        data = _load()
        dev = data["devices"].get((device_id or "").strip())
        if dev is None or dev["tenant"] != t:
            return None
        dev["disabled"] = True
        dev["disabled_at"] = int(time.time())
        _save(data)
    if dev.get("key_id"):
        key_revoker(dev["key_id"])
    return dict(dev)


def rename(device_id: str, tenant: Optional[str], name: str) -> Optional[dict]:
    name = (name or "").strip()
    if not name:
        return None
    t = _norm(tenant)
    with _lock:
        data = _load()
        dev = data["devices"].get((device_id or "").strip())
        if dev is None or dev["tenant"] != t:
            return None
        dev["name"] = name
        _save(data)
        return dict(dev)


def remove_tenant(tenant: Optional[str], key_revoker=None) -> int:
    """Offboarding: drop the tenant's devices + pending pairings, revoking any
    device keys via ``key_revoker`` when provided. Returns devices removed."""
    t = _norm(tenant)
    with _lock:
        data = _load()
        gone = [d for d in data["devices"].values() if d["tenant"] == t]
        data["devices"] = {i: d for i, d in data["devices"].items() if d["tenant"] != t}
        data["pairings"] = {h: p for h, p in data["pairings"].items() if p["tenant"] != t}
        _save(data)
    if key_revoker is not None:
        for d in gone:
            if d.get("key_id"):
                key_revoker(d["key_id"])
    return len(gone)
