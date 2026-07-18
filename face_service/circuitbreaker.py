"""Circuit breakers for outbound dependencies.

The service talks to flaky things: webhook receivers, SSO providers, SMS gateways.
Hammering a dependency that is already down wastes time, piles up timeouts, and can
make an outage worse. A circuit breaker watches the failure stream per dependency
and, once failures cross a threshold, "opens" — short-circuiting calls for a cooldown
so the caller fails fast instead of waiting. After the cooldown it goes "half-open"
and lets a single trial through; success closes it, failure re-opens it.

  * ``allow``    should a call to this dependency be attempted right now? Returns
                 the state and, when open, when it will next probe.
  * ``record``   report the outcome of an attempted call (ok / fail); drives the
                 state machine.
  * ``state`` / ``reset`` — inspect or force-close a breaker.

States: ``closed`` (normal), ``open`` (failing fast), ``half_open`` (one probe in
flight). This is the classic three-state breaker, made pure and pull-based so the
caller supplies ``now`` and the module never sleeps.

Registry: ``circuitbreaker.json`` (env ``FACE_CIRCUITBREAKER_FILE``).
"""

from __future__ import annotations

import time
from typing import Optional

from ._registry import Registry

_reg = Registry("FACE_CIRCUITBREAKER_FILE", "circuitbreaker.json")

_DEFAULTS = {"threshold": 5, "cooldown": 30, "half_open_max": 1}


def _key(tenant: Optional[str], name: str) -> str:
    return _reg.scoped(tenant, (name or '').strip())


def configure(tenant: Optional[str], name: str, threshold: int = 5,
              cooldown: int = 30) -> dict:
    if not (name or "").strip():
        raise ValueError("dependency name is required.")
    if int(threshold) < 1:
        raise ValueError("threshold must be >= 1.")
    if int(cooldown) < 1:
        raise ValueError("cooldown must be >= 1.")
    cfg = {"threshold": int(threshold), "cooldown": int(cooldown), "half_open_max": 1}
    with _reg.mutate() as data:
        b = data.setdefault(_key(tenant, name), _new())
        b["cfg"] = cfg
    return {"name": (name or "").strip(), **cfg}


def _new() -> dict:
    return {"state": "closed", "failures": 0, "opened_at": None,
            "probes": 0, "cfg": dict(_DEFAULTS)}


def allow(tenant: Optional[str], name: str, now: Optional[int] = None) -> dict:
    now = int(now if now is not None else time.time())
    with _reg.mutate() as data:
        b = data.setdefault(_key(tenant, name), _new())
        cfg = b.get("cfg", _DEFAULTS)
        if b["state"] == "open":
            opened = b["opened_at"] if b["opened_at"] is not None else now
            if now - opened >= cfg["cooldown"]:
                b["state"] = "half_open"
                b["probes"] = 0
            else:
                return {"allowed": False, "state": "open",
                        "retry_at": opened + cfg["cooldown"]}
        if b["state"] == "half_open":
            if b["probes"] >= cfg["half_open_max"]:
                return {"allowed": False, "state": "half_open"}
            b["probes"] += 1
            return {"allowed": True, "state": "half_open"}
        return {"allowed": True, "state": "closed"}


def record(tenant: Optional[str], name: str, ok: bool,
           now: Optional[int] = None) -> dict:
    now = int(now if now is not None else time.time())
    with _reg.mutate() as data:
        b = data.setdefault(_key(tenant, name), _new())
        cfg = b.get("cfg", _DEFAULTS)
        if ok:
            b["state"] = "closed"
            b["failures"] = 0
            b["opened_at"] = None
            b["probes"] = 0
        else:
            if b["state"] == "half_open":
                b["state"] = "open"
                b["opened_at"] = now
                b["probes"] = 0
            else:
                b["failures"] += 1
                if b["failures"] >= cfg["threshold"]:
                    b["state"] = "open"
                    b["opened_at"] = now
        return {"state": b["state"], "failures": b["failures"]}


def state(tenant: Optional[str], name: str) -> dict:
    b = _reg.load().get(_key(tenant, name))
    if not b:
        return {"exists": False, "state": "closed"}
    return {"exists": True, "state": b["state"], "failures": b["failures"],
            "opened_at": b["opened_at"]}


def reset(tenant: Optional[str], name: str) -> bool:
    with _reg.mutate() as data:
        b = data.get(_key(tenant, name))
        if not b:
            return False
        cfg = b.get("cfg", dict(_DEFAULTS))
        data[_key(tenant, name)] = {**_new(), "cfg": cfg}
    return True
