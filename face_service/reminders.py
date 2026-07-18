"""Scheduled reminders / ticklers with optional recurrence.

Compliance work is full of "do X by date Y": renew a certificate, re-run an access
review, re-attest a policy, follow up on a visitor. This subsystem is a lightweight
tickler file — schedule a reminder for a future time, optionally recurring, then pull
the ones that have come due. It is deliberately pull-based (the caller polls ``due``
on a timer) so the module never runs a scheduler thread of its own.

  * ``schedule``   a reminder at a due time, optional ``every`` seconds recurrence.
  * ``due``        reminders whose time has arrived and are unacknowledged; a
                   recurring one rolls its due time forward instead of firing forever.
  * ``acknowledge`` mark a one-shot reminder done (recurring ones auto-advance).
  * ``snooze``     push a reminder's due time out.
  * ``upcoming``   pending reminders sorted by due time.

Recurrence advances in whole periods past ``now`` so a reminder that was missed for
several cycles fires once and catches up, rather than firing once per missed period.

Registry: ``reminders.json`` (env ``FACE_REMINDERS_FILE``).
"""

from __future__ import annotations

import time
import uuid
from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_REMINDERS_FILE", "reminders.json")


def schedule(tenant: Optional[str], text: str, due_at: int,
             every: Optional[int] = None, target: str = "") -> dict:
    text = (text or "").strip()
    if not text:
        raise ValueError("reminder text is required.")
    if every is not None and int(every) <= 0:
        raise ValueError("recurrence 'every' must be positive.")
    rem = {"id": "rem_" + uuid.uuid4().hex[:8], "text": text,
           "due": int(due_at), "every": int(every) if every else None,
           "target": (target or "").strip(), "acked": False,
           "fired": 0, "created": int(time.time())}
    with _reg.mutate() as data:
        data.setdefault(_reg.norm(tenant), {})[rem["id"]] = rem
    return {"id": rem["id"], "due": rem["due"], "recurring": bool(rem["every"])}


def due(tenant: Optional[str], now: Optional[int] = None) -> List[dict]:
    now = int(now if now is not None else time.time())
    fired: List[dict] = []
    with _reg.mutate() as data:
        for rem in (data.get(_reg.norm(tenant)) or {}).values():
            if rem["acked"] or rem["due"] > now:
                continue
            fired.append({"id": rem["id"], "text": rem["text"],
                          "target": rem["target"], "due": rem["due"]})
            rem["fired"] += 1
            if rem["every"]:
                # advance past now in whole periods
                periods = ((now - rem["due"]) // rem["every"]) + 1
                rem["due"] += periods * rem["every"]
            else:
                rem["acked"] = True
    return sorted(fired, key=lambda r: r["due"])


def acknowledge(tenant: Optional[str], reminder_id: str) -> bool:
    with _reg.mutate() as data:
        rem = (data.get(_reg.norm(tenant)) or {}).get((reminder_id or "").strip())
        if not rem or rem["acked"]:
            return False
        rem["acked"] = True
    return True


def snooze(tenant: Optional[str], reminder_id: str, until: int) -> bool:
    with _reg.mutate() as data:
        rem = (data.get(_reg.norm(tenant)) or {}).get((reminder_id or "").strip())
        if not rem:
            return False
        rem["due"] = int(until)
        rem["acked"] = False
    return True


def upcoming(tenant: Optional[str], now: Optional[int] = None,
             horizon: Optional[int] = None) -> List[dict]:
    now = int(now if now is not None else time.time())
    out = []
    for rem in (_reg.load().get(_reg.norm(tenant)) or {}).values():
        if rem["acked"]:
            continue
        if horizon is not None and rem["due"] > now + horizon:
            continue
        out.append({"id": rem["id"], "text": rem["text"], "due": rem["due"],
                    "recurring": bool(rem["every"])})
    return sorted(out, key=lambda r: r["due"])
