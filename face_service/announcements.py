"""Announcements — targeted "what's new" notices with per-user read state.

Admins and end users should learn about relevant changes — a new liveness prompt, a
policy update, scheduled maintenance — without being spammed with irrelevant ones. This
subsystem publishes announcements targeted at an audience, tracks which users have read
which, and serves each user their unread, still-relevant notices. It is a small,
self-contained in-product messaging surface, distinct from the transactional [[digest]]
and [[alerts]] paths.

  * ``publish``   an announcement (title, body) to an audience, with optional expiry.
  * ``feed``      a subject's announcements (those targeting them / everyone), newest
                  first, with a per-item ``read`` flag; ``unread_only`` to filter.
  * ``mark_read`` record that a subject has read an announcement.
  * ``unread_count`` how many a subject has not yet read.
  * ``retract``   pull an announcement (published in error).

Audience is a simple tag matched against a subject's audience tags, plus the special
``all`` audience that reaches everyone. Expired or retracted announcements never appear
in a feed, so the "what's new" surface stays current on its own.

Registry: ``announcements.json`` (env ``FACE_ANNOUNCEMENTS_FILE``).
"""

from __future__ import annotations

import time
import uuid
from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_ANNOUNCEMENTS_FILE", "announcements.json")


def _root(data: dict, tenant: Optional[str]) -> dict:
    return data.setdefault(_reg.norm(tenant), {"items": {}, "reads": {}})


def publish(tenant: Optional[str], title: str, body: str, audience: str = "all",
            expires_at: Optional[int] = None, now: Optional[int] = None) -> dict:
    title = (title or "").strip()
    if not title:
        raise ValueError("title is required.")
    now = int(now if now is not None else time.time())
    item = {"id": "ann_" + uuid.uuid4().hex[:10], "title": title,
            "body": (body or "").strip(), "audience": (audience or "all").strip() or "all",
            "expires": int(expires_at) if expires_at is not None else None,
            "published": now, "retracted": False}
    with _reg.mutate() as data:
        _root(data, tenant)["items"][item["id"]] = item
    return {"id": item["id"], "audience": item["audience"]}


def _visible(item: dict, audiences: set, now: int) -> bool:
    if item["retracted"]:
        return False
    if item["expires"] is not None and now >= item["expires"]:
        return False
    return item["audience"] == "all" or item["audience"] in audiences


def feed(tenant: Optional[str], subject: str, audiences: Optional[List[str]] = None,
         unread_only: bool = False, now: Optional[int] = None) -> List[dict]:
    now = int(now if now is not None else time.time())
    subject = (subject or "").strip()
    auds = {(a or "").strip() for a in (audiences or []) if (a or "").strip()}
    root = _reg.load().get(_reg.norm(tenant)) or {"items": {}, "reads": {}}
    read_set = set(root.get("reads", {}).get(subject, []))
    out = []
    for item in root["items"].values():
        if not _visible(item, auds, now):
            continue
        is_read = item["id"] in read_set
        if unread_only and is_read:
            continue
        out.append({"id": item["id"], "title": item["title"], "body": item["body"],
                    "published": item["published"], "read": is_read})
    return sorted(out, key=lambda a: -a["published"])


def mark_read(tenant: Optional[str], subject: str, announcement_id: str) -> bool:
    subject = (subject or "").strip()
    aid = (announcement_id or "").strip()
    with _reg.mutate() as data:
        root = _root(data, tenant)
        if aid not in root["items"]:
            return False
        reads = root["reads"].setdefault(subject, [])
        if aid not in reads:
            reads.append(aid)
    return True


def unread_count(tenant: Optional[str], subject: str,
                 audiences: Optional[List[str]] = None, now: Optional[int] = None) -> int:
    return len(feed(tenant, subject, audiences, unread_only=True, now=now))


def retract(tenant: Optional[str], announcement_id: str) -> bool:
    with _reg.mutate() as data:
        item = _root(data, tenant)["items"].get((announcement_id or "").strip())
        if not item or item["retracted"]:
            return False
        item["retracted"] = True
    return True
