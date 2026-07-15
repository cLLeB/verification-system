"""Active-liveness challenges — prove a live person, not a photo or video.

Passive liveness scores texture; active liveness asks the person to *do* something
unpredictable and checks they did it — blink twice, turn left, smile, read a
number. Because the action is chosen by the server at capture time, a pre-recorded
video of the target cannot satisfy it. This subsystem issues a random challenge
from a configured repertoire, binds it to a short-lived id, and verifies the
client's claimed response against what was asked, once, in time.

  * ``issue``   pick a random action, return {id, action, expires}.
  * ``verify``  confirm the response matches the issued action, single-use, fresh.
  * ``gate``    fold a passed/failed challenge into a verify result.

The repertoire is tenant-configurable so deployments can match their UI's
supported prompts. This is orthogonal to the biometric match — it proves liveness,
not identity.

Registry: ``challenge.json`` (env ``FACE_CHALLENGE_FILE``).
"""

from __future__ import annotations

import secrets
import time
import uuid
from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_CHALLENGE_FILE", "challenge.json")

DEFAULT_ACTIONS = ["blink", "turn_left", "turn_right", "smile", "nod"]
DEFAULT_TTL = 30


def _doc(data: dict, tenant: str) -> dict:
    d = data.setdefault(tenant, {})
    d.setdefault("actions", list(DEFAULT_ACTIONS))
    d.setdefault("pending", {})    # id -> {action, expires}
    return d


def set_actions(tenant: Optional[str], actions: List[str]) -> List[str]:
    clean = [a.strip().lower() for a in actions if a and a.strip()]
    if not clean:
        raise ValueError("at least one action is required.")
    with _reg.mutate() as data:
        _doc(data, _reg.norm(tenant))["actions"] = clean
    return clean


def issue(tenant: Optional[str], ttl: int = DEFAULT_TTL,
          now: Optional[int] = None) -> dict:
    now = int(now if now is not None else time.time())
    t = _reg.norm(tenant)
    with _reg.mutate() as data:
        doc = _doc(data, t)
        # sweep expired
        doc["pending"] = {k: v for k, v in doc["pending"].items() if v["expires"] > now}
        action = secrets.choice(doc["actions"])
        cid = "ch_" + uuid.uuid4().hex[:12]
        doc["pending"][cid] = {"action": action, "expires": now + max(1, int(ttl))}
    return {"id": cid, "action": action, "expires": now + max(1, int(ttl))}


def verify(tenant: Optional[str], challenge_id: str, response: str,
           now: Optional[int] = None) -> bool:
    cid = (challenge_id or "").strip()
    resp = (response or "").strip().lower()
    now = int(now if now is not None else time.time())
    t = _reg.norm(tenant)
    with _reg.mutate() as data:
        pending = _doc(data, t)["pending"]
        rec = pending.pop(cid, None)      # single use
        if not rec:
            return False
        return rec["expires"] > now and rec["action"] == resp


def gate(tenant: Optional[str], result: dict, challenge_id: Optional[str] = None,
         response: Optional[str] = None, now: Optional[int] = None) -> dict:
    """Require a passed liveness challenge on a verify RESULT (mutates+returns)."""
    if not result.get("success"):
        return result
    if not verify(tenant, challenge_id or "", response or "", now):
        result["success"] = False
        result["code"] = "liveness_failed"
        result["message"] = "Active-liveness challenge not satisfied."
    else:
        result["liveness"] = "active_passed"
    return result
