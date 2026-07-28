"""Token-bucket rate limiting.

Endpoints need protection from bursts and sustained abuse alike. A token bucket is the
standard primitive: a bucket holds up to ``burst`` tokens and refills at ``rate`` tokens
per second; each request spends tokens and is allowed only if enough are available. It
permits short bursts (up to the bucket size) while bounding the long-run average - more
forgiving than a fixed window and smoother than a sliding count. This complements the
sliding-window [[velocity]] control with a different shape of limit.

  * ``configure``  a named limiter: refill ``rate`` per second and ``burst`` capacity.
  * ``allow``      attempt to spend ``cost`` tokens; refills lazily from elapsed time.
                   Returns whether it passed and how many tokens remain / retry-after.
  * ``peek``       current token level without consuming.
  * ``reset``      refill the bucket to full.

Refill is computed from wall-clock deltas (caller may inject ``now``), so no background
timer is needed and the limiter is exact and deterministic under test.

Registry: ``ratelimit.json`` (env ``FACE_RATELIMIT_FILE``).
"""

from __future__ import annotations

import time
from typing import Optional

from ._registry import Registry

_reg = Registry("FACE_RATELIMIT_FILE", "ratelimit.json")


def _key(tenant: Optional[str], name: str) -> str:
    return _reg.scoped(tenant, (name or '').strip())


def configure(tenant: Optional[str], name: str, rate: float, burst: float,
              now: Optional[int] = None) -> dict:
    if not (name or "").strip():
        raise ValueError("limiter name is required.")
    if float(rate) <= 0 or float(burst) <= 0:
        raise ValueError("rate and burst must be positive.")
    now = float(now if now is not None else time.time())
    with _reg.mutate() as data:
        data[_key(tenant, name)] = {"rate": float(rate), "burst": float(burst),
                                    "tokens": float(burst), "updated": now}
    return {"name": (name or "").strip(), "rate": float(rate), "burst": float(burst)}


def _refill(b: dict, now: float) -> None:
    elapsed = max(0.0, now - b["updated"])
    b["tokens"] = min(b["burst"], b["tokens"] + elapsed * b["rate"])
    b["updated"] = now


def allow(tenant: Optional[str], name: str, cost: float = 1.0,
          now: Optional[int] = None) -> dict:
    now = float(now if now is not None else time.time())
    cost = float(cost)
    with _reg.mutate() as data:
        b = data.get(_key(tenant, name))
        if not b:
            return {"allowed": False, "reason": "unconfigured"}
        _refill(b, now)
        if b["tokens"] >= cost:
            b["tokens"] -= cost
            return {"allowed": True, "remaining": round(b["tokens"], 3)}
        deficit = cost - b["tokens"]
        return {"allowed": False, "remaining": round(b["tokens"], 3),
                "retry_after": round(deficit / b["rate"], 3)}


def peek(tenant: Optional[str], name: str, now: Optional[int] = None) -> dict:
    now = float(now if now is not None else time.time())
    b = _reg.load().get(_key(tenant, name))
    if not b:
        return {"exists": False}
    tokens = min(b["burst"], b["tokens"] + max(0.0, now - b["updated"]) * b["rate"])
    return {"exists": True, "tokens": round(tokens, 3), "burst": b["burst"]}


def reset(tenant: Optional[str], name: str, now: Optional[int] = None) -> bool:
    now = float(now if now is not None else time.time())
    with _reg.mutate() as data:
        b = data.get(_key(tenant, name))
        if not b:
            return False
        b["tokens"] = b["burst"]
        b["updated"] = now
    return True
