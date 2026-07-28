"""Segments - named, reusable audiences defined by tag rules.

Operators repeatedly want "everyone who is a contractor but not offboarded" or
"night-shift on floor 3". Rather than recompute that by hand each time, a segment
saves the rule - a small include/exclude set of tags - under a name, and resolves
it live against the current [[tags]] data. Segments are the handle other features
target: send a notice to a segment, apply a policy to a segment, export a segment.

  * ``define``   save a segment: ``all`` tags (AND), ``any`` tags (OR), ``none``
                 tags (NOT). A member must match all/any and none of these.
  * ``members``  resolve the segment against current tags.
  * ``matches``  does one identity fall in the segment?

Registry: ``segments.json`` (env ``FACE_SEGMENTS_FILE``) - stores only rules; the
membership always comes live from tags, so it is never stale.
"""

from __future__ import annotations

from typing import List, Optional

from ._registry import Registry
from . import tags as tagmod

_reg = Registry("FACE_SEGMENTS_FILE", "segments.json")


def _clean(items) -> List[str]:
    return sorted({(x or "").strip().lower() for x in (items or []) if (x or "").strip()})


def define(tenant: Optional[str], name: str, all: Optional[List[str]] = None,
           any: Optional[List[str]] = None, none: Optional[List[str]] = None) -> dict:
    name = (name or "").strip()
    if not name:
        raise ValueError("segment name is required.")
    rule = {"all": _clean(all), "any": _clean(any), "none": _clean(none)}
    if not (rule["all"] or rule["any"] or rule["none"]):
        raise ValueError("a segment needs at least one tag rule.")
    with _reg.mutate() as data:
        data.setdefault(_reg.norm(tenant), {})[name] = rule
    return {"name": name, **rule}


def delete(tenant: Optional[str], name: str) -> bool:
    t = _reg.norm(tenant)
    with _reg.mutate() as data:
        return (data.get(t) or {}).pop((name or "").strip(), None) is not None


def rule(tenant: Optional[str], name: str) -> Optional[dict]:
    r = (_reg.load().get(_reg.norm(tenant)) or {}).get((name or "").strip())
    return dict(r) if r else None


def _matches_rule(have: set, r: dict) -> bool:
    if r["all"] and not all(t in have for t in r["all"]):
        return False
    if r["any"] and not any(t in have for t in r["any"]):
        return False
    if r["none"] and any(t in have for t in r["none"]):
        return False
    return True


def matches(tenant: Optional[str], name: str, user_id: str) -> bool:
    r = rule(tenant, name)
    if not r:
        return False
    return _matches_rule(set(tagmod.tags_of(tenant, user_id)), r)


def members(tenant: Optional[str], name: str) -> List[str]:
    r = rule(tenant, name)
    if not r:
        return []
    # candidate universe = everyone who carries any referenced tag
    referenced = set(r["all"]) | set(r["any"]) | set(r["none"])
    candidates: set = set()
    for tag in referenced:
        candidates.update(tagmod.members(tenant, tag))
    return sorted(uid for uid in candidates
                  if _matches_rule(set(tagmod.tags_of(tenant, uid)), r))


def names(tenant: Optional[str]) -> List[str]:
    return sorted(_reg.load().get(_reg.norm(tenant)) or {})
