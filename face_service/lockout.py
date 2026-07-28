"""Progressive account lockout on repeated failed verifications.

Repeated failed matches against one identity are a red flag: a spoofing campaign, a
look-alike probing the system, or a broken enrolment. This subsystem tracks failures
per subject in a sliding window and locks the account for a growing duration each
time the threshold is hit - the classic exponential-backoff lockout that frustrates
attackers without permanently punishing a legitimate user who eventually succeeds.

  * ``record_failure`` count a failed verify; may trip a lockout.
  * ``record_success`` clear the failure streak (a good match resets everything).
  * ``is_locked``      is the subject currently locked, and until when?
  * ``gate``           post-match helper: turn a would-be success into a denial while
                       locked, and feed failures/successes back in one call.
  * ``unlock``         manual admin override.

Lock duration escalates by lockout count: base, 2×, 4×, … capped. Failures older
than the window are ignored, so a slow trickle never accumulates into a lock.

Registry: ``lockout.json`` (env ``FACE_LOCKOUT_FILE``).
"""

from __future__ import annotations

import time
from typing import Optional

from ._registry import Registry

_reg = Registry("FACE_LOCKOUT_FILE", "lockout.json")

_DEFAULTS = {"threshold": 5, "window": 300, "base_lock": 300, "max_lock": 3600}


def _key(tenant: Optional[str], subject: str) -> str:
    return _reg.scoped(tenant, (subject or '').strip())


def configure(tenant: Optional[str], threshold: int = 5, window: int = 300,
              base_lock: int = 300, max_lock: int = 3600) -> dict:
    if int(threshold) < 1:
        raise ValueError("threshold must be >= 1.")
    if int(window) < 1 or int(base_lock) < 1:
        raise ValueError("window and base_lock must be positive.")
    cfg = {"threshold": int(threshold), "window": int(window),
           "base_lock": int(base_lock), "max_lock": int(max_lock)}
    with _reg.mutate() as data:
        data.setdefault("__cfg__", {})[_reg.norm(tenant)] = cfg
    return cfg


def _cfg(data: dict, tenant: Optional[str]) -> dict:
    return (data.get("__cfg__", {}) or {}).get(_reg.norm(tenant), _DEFAULTS)


def record_failure(tenant: Optional[str], subject: str, now: Optional[int] = None) -> dict:
    now = int(now if now is not None else time.time())
    with _reg.mutate() as data:
        cfg = _cfg(data, tenant)
        rec = data.setdefault(_key(tenant, subject),
                              {"fails": [], "lock_until": 0, "lock_count": 0})
        if rec["lock_until"] > now:
            return {"locked": True, "until": rec["lock_until"]}
        rec["fails"] = [t for t in rec["fails"] if now - t < cfg["window"]]
        rec["fails"].append(now)
        if len(rec["fails"]) >= cfg["threshold"]:
            rec["lock_count"] += 1
            dur = min(cfg["base_lock"] * (2 ** (rec["lock_count"] - 1)), cfg["max_lock"])
            rec["lock_until"] = now + dur
            rec["fails"] = []
            return {"locked": True, "until": rec["lock_until"], "duration": dur,
                    "lock_count": rec["lock_count"]}
        return {"locked": False, "fails": len(rec["fails"]),
                "remaining": cfg["threshold"] - len(rec["fails"])}


def record_success(tenant: Optional[str], subject: str) -> None:
    with _reg.mutate() as data:
        rec = data.get(_key(tenant, subject))
        if rec:
            rec["fails"] = []
            rec["lock_until"] = 0
            rec["lock_count"] = 0


def is_locked(tenant: Optional[str], subject: str, now: Optional[int] = None) -> dict:
    now = int(now if now is not None else time.time())
    rec = _reg.load().get(_key(tenant, subject))
    if not rec or rec["lock_until"] <= now:
        return {"locked": False}
    return {"locked": True, "until": rec["lock_until"],
            "seconds_left": rec["lock_until"] - now}


def unlock(tenant: Optional[str], subject: str) -> bool:
    with _reg.mutate() as data:
        rec = data.get(_key(tenant, subject))
        if not rec:
            return False
        rec["fails"] = []
        rec["lock_until"] = 0
    return True


def gate(tenant: Optional[str], result: dict, subject: str,
         now: Optional[int] = None) -> dict:
    """Enforce lockout around a biometric result and feed the outcome back."""
    now = int(now if now is not None else time.time())
    out = dict(result)
    lk = is_locked(tenant, subject, now)
    if lk["locked"]:
        out["success"] = False
        out["code"] = "LOCKED_OUT"
        out["message"] = f"Account locked for {lk['seconds_left']}s after repeated failures."
        return out
    if out.get("success"):
        record_success(tenant, subject)
    else:
        info = record_failure(tenant, subject, now)
        if info.get("locked"):
            out["code"] = "LOCKED_OUT"
            out["message"] = "Account locked after repeated failures."
    return out
