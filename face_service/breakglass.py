"""Break-glass — deliberate, time-boxed, loudly-logged emergency access.

Sometimes the rules must yield: a medic needs into a locked ward, an on-call
engineer must reach a system at 3am and the usual approver is unreachable. The
wrong answer is a permanent backdoor. Break-glass is the right answer: a named
override that is explicit, expires on its own, and is impossible to use quietly.

  * ``activate``  opens an override for a scope (a door, an action) for a fixed
    number of seconds, with a mandatory reason and the person who broke the
    glass. Returns a token.
  * ``active``    True while the window holds.
  * ``uses``      every check while active is counted, so the after-action review
    sees exactly how much the override was leaned on.
  * ``close``     end it early once the emergency passes.

Nothing here is silent: activation and expiry are meant to be mirrored to
[[hashchain]] and alerting by the caller. Defaults to a 15-minute window.

Registry: ``breakglass.json`` (env ``FACE_BREAKGLASS_FILE``).
"""

from __future__ import annotations

import time
import uuid
from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_BREAKGLASS_FILE", "breakglass.json")

DEFAULT_TTL = 15 * 60


def _scope(scope: str) -> str:
    return (scope or "default").strip() or "default"


def activate(tenant: Optional[str], scope: str, reason: str, by: str,
             ttl: int = DEFAULT_TTL, now: Optional[int] = None) -> dict:
    reason = (reason or "").strip()
    by = (by or "").strip()
    if not reason:
        raise ValueError("break-glass requires a reason.")
    if not by:
        raise ValueError("break-glass requires the activating identity.")
    now = int(now if now is not None else time.time())
    t = _reg.norm(tenant)
    rec = {"token": "bg_" + uuid.uuid4().hex[:12], "scope": _scope(scope),
           "reason": reason, "by": by, "activated_at": now,
           "expires_at": now + max(1, int(ttl)), "uses": 0, "closed": False}
    with _reg.mutate() as data:
        data.setdefault(t, {})[_scope(scope)] = rec
    return dict(rec)


def _current(tenant: Optional[str], scope: str) -> Optional[dict]:
    return (_reg.load().get(_reg.norm(tenant)) or {}).get(_scope(scope))


def active(tenant: Optional[str], scope: str, now: Optional[int] = None) -> bool:
    rec = _current(tenant, scope)
    now = int(now if now is not None else time.time())
    return bool(rec and not rec.get("closed") and rec.get("expires_at", 0) > now)


def check(tenant: Optional[str], scope: str, now: Optional[int] = None) -> bool:
    """Test the override AND count the use (for the after-action report)."""
    if not active(tenant, scope, now):
        return False
    t = _reg.norm(tenant)
    with _reg.mutate() as data:
        rec = (data.get(t) or {}).get(_scope(scope))
        if rec:
            rec["uses"] = rec.get("uses", 0) + 1
    return True


def close(tenant: Optional[str], scope: str, now: Optional[int] = None) -> bool:
    t = _reg.norm(tenant)
    with _reg.mutate() as data:
        rec = (data.get(t) or {}).get(_scope(scope))
        if not rec or rec.get("closed"):
            return False
        rec["closed"] = True
        rec["closed_at"] = int(now if now is not None else time.time())
    return True


def report(tenant: Optional[str]) -> List[dict]:
    return [dict(v) for v in sorted((_reg.load().get(_reg.norm(tenant)) or {}).values(),
                                    key=lambda r: r.get("activated_at", 0))]
