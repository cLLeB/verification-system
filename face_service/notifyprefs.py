"""Notification preferences — per-person channel and category opt-in/out.

Not everyone wants every notification on every channel. Respecting that is both courtesy
and, for marketing-adjacent messages, a legal requirement. This subsystem stores per-
subject preferences — which channels (email/sms/push) are enabled, per category — and
resolves a single question the sender asks: "may I notify this person about this
category on this channel right now?" It layers a global opt-out over per-category, per-
channel settings.

  * ``set_channel``    enable/disable a channel for a subject (global for that channel).
  * ``set_category``   enable/disable a specific category on a specific channel.
  * ``opt_out_all`` / ``opt_in_all`` — a master switch (e.g. unsubscribe link).
  * ``should_notify``  the resolved yes/no for (subject, category, channel).
  * ``channels_for``   which channels to actually use for a category.

Resolution order: global opt-out wins; then the channel must be enabled; then, if the
category has an explicit setting it applies, else the default is to allow (opt-out
model for transactional categories). ``transactional`` categories can be marked
mandatory by the caller by simply not consulting prefs — this module governs the rest.

Registry: ``notifyprefs.json`` (env ``FACE_NOTIFYPREFS_FILE``).
"""

from __future__ import annotations

from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_NOTIFYPREFS_FILE", "notifyprefs.json")

_CHANNELS = ("email", "sms", "push")


def _rec(data: dict, tenant: Optional[str], subject: str) -> dict:
    subj = (subject or "").strip()
    return data.setdefault(_reg.norm(tenant), {}).setdefault(
        subj, {"opted_out": False, "channels": {}, "categories": {}})


def set_channel(tenant: Optional[str], subject: str, channel: str, enabled: bool) -> dict:
    channel = (channel or "").strip().lower()
    if channel not in _CHANNELS:
        raise ValueError(f"channel must be one of {_CHANNELS}.")
    with _reg.mutate() as data:
        _rec(data, tenant, subject)["channels"][channel] = bool(enabled)
    return {"channel": channel, "enabled": bool(enabled)}


def set_category(tenant: Optional[str], subject: str, category: str, channel: str,
                 enabled: bool) -> dict:
    channel = (channel or "").strip().lower()
    category = (category or "").strip()
    if channel not in _CHANNELS:
        raise ValueError(f"channel must be one of {_CHANNELS}.")
    if not category:
        raise ValueError("category is required.")
    with _reg.mutate() as data:
        rec = _rec(data, tenant, subject)
        rec["categories"].setdefault(category, {})[channel] = bool(enabled)
    return {"category": category, "channel": channel, "enabled": bool(enabled)}


def opt_out_all(tenant: Optional[str], subject: str) -> None:
    with _reg.mutate() as data:
        _rec(data, tenant, subject)["opted_out"] = True


def opt_in_all(tenant: Optional[str], subject: str) -> None:
    with _reg.mutate() as data:
        _rec(data, tenant, subject)["opted_out"] = False


def _load(tenant: Optional[str], subject: str) -> Optional[dict]:
    return (_reg.load().get(_reg.norm(tenant)) or {}).get((subject or "").strip())


def should_notify(tenant: Optional[str], subject: str, category: str,
                  channel: str) -> bool:
    channel = (channel or "").strip().lower()
    if channel not in _CHANNELS:
        return False
    rec = _load(tenant, subject)
    if not rec:
        return True                     # no prefs set -> default allow
    if rec["opted_out"]:
        return False
    if not rec["channels"].get(channel, True):
        return False
    cat = rec["categories"].get((category or "").strip())
    if cat is not None and channel in cat:
        return cat[channel]
    return True


def channels_for(tenant: Optional[str], subject: str, category: str) -> List[str]:
    return [c for c in _CHANNELS if should_notify(tenant, subject, category, c)]
