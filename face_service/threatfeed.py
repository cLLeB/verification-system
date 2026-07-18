"""Threat-intelligence feed — block known-bad indicators at verify time.

Security teams and partner feeds publish indicators of compromise: device fingerprints
seen in fraud, image hashes of known presentation-attack photos, IPs behind
credential-stuffing. This subsystem ingests those indicators (with a source and an
optional expiry) and answers, in one lookup, "is this thing on a blocklist right now".
It complements [[honeytokens]] (bait) and [[iprules]] (static network policy) by
carrying dynamic, expiring intel from many sources.

  * ``add``        one indicator: type, value, source, optional TTL seconds.
  * ``bulk_add``   ingest a batch from a feed.
  * ``check``      is (type, value) currently listed and unexpired? Returns the
                   matching sources.
  * ``gate``       post-match helper: deny a verification whose context matches any
                   active indicator (device / ip / image-hash / subject).
  * ``purge_expired`` drop indicators past their TTL; ``count`` for dashboards.

Indicators are keyed by (type, value); multiple sources can list the same value and
each carries its own expiry, so the indicator stays hot until the *last* source's TTL
lapses — intel doesn't disappear because one feed rotated it out.

Registry: ``threatfeed.json`` (env ``FACE_THREATFEED_FILE``).
"""

from __future__ import annotations

import time
from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_THREATFEED_FILE", "threatfeed.json")

_TYPES = ("device_fp", "image_hash", "ip", "subject")


def _key(itype: str, value: str) -> str:
    return f"{itype}|{value}"


def add(tenant: Optional[str], itype: str, value: str, source: str = "manual",
        ttl: Optional[int] = None, now: Optional[int] = None) -> dict:
    itype = (itype or "").strip().lower()
    if itype not in _TYPES:
        raise ValueError(f"type must be one of {_TYPES}.")
    value = (value or "").strip()
    if not value:
        raise ValueError("indicator value is required.")
    now = int(now if now is not None else time.time())
    expires = None if ttl is None else now + int(ttl)
    with _reg.mutate() as data:
        ind = data.setdefault(_reg.norm(tenant), {}).setdefault(
            _key(itype, value), {"type": itype, "value": value, "sources": {}})
        ind["sources"][(source or "manual").strip()] = {"added": now, "expires": expires}
    return {"type": itype, "value": value, "expires": expires}


def bulk_add(tenant: Optional[str], indicators: List[dict], source: str = "feed",
             ttl: Optional[int] = None, now: Optional[int] = None) -> dict:
    added = 0
    for ind in indicators or []:
        try:
            add(tenant, ind.get("type"), ind.get("value"),
                ind.get("source", source), ind.get("ttl", ttl), now)
            added += 1
        except ValueError:
            continue
    return {"added": added}


def _active_sources(ind: dict, now: int) -> List[str]:
    return [s for s, meta in ind["sources"].items()
            if meta["expires"] is None or meta["expires"] > now]


def check(tenant: Optional[str], itype: str, value: str,
          now: Optional[int] = None) -> dict:
    now = int(now if now is not None else time.time())
    ind = (_reg.load().get(_reg.norm(tenant)) or {}).get(
        _key((itype or "").strip().lower(), (value or "").strip()))
    if not ind:
        return {"listed": False}
    sources = _active_sources(ind, now)
    return {"listed": bool(sources), "sources": sorted(sources),
            "type": ind["type"], "value": ind["value"]}


def gate(tenant: Optional[str], result: dict, device_fp: str = "", ip: str = "",
         image_hash: str = "", subject: str = "", now: Optional[int] = None) -> dict:
    """Deny a match whose context hits any active indicator."""
    out = dict(result)
    if not out.get("success"):
        return out
    checks = [("device_fp", device_fp), ("ip", ip),
              ("image_hash", image_hash), ("subject", subject)]
    for itype, value in checks:
        if not value:
            continue
        hit = check(tenant, itype, value, now)
        if hit["listed"]:
            out["success"] = False
            out["code"] = "THREAT_BLOCKED"
            out["message"] = f"Blocked by threat feed ({itype})."
            out["threat"] = {"type": itype, "sources": hit["sources"]}
            return out
    return out


def purge_expired(tenant: Optional[str], now: Optional[int] = None) -> dict:
    now = int(now if now is not None else time.time())
    removed = 0
    with _reg.mutate() as data:
        t = data.get(_reg.norm(tenant)) or {}
        for key in list(t.keys()):
            ind = t[key]
            ind["sources"] = {s: m for s, m in ind["sources"].items()
                              if m["expires"] is None or m["expires"] > now}
            if not ind["sources"]:
                del t[key]
                removed += 1
    return {"removed": removed}


def count(tenant: Optional[str], now: Optional[int] = None) -> int:
    now = int(now if now is not None else time.time())
    return sum(1 for ind in (_reg.load().get(_reg.norm(tenant)) or {}).values()
               if _active_sources(ind, now))
