"""Identity tags — arbitrary labels on identities, and queries over them.

Deployments constantly need to group people: "contractor", "vip", "floor-3",
"nightshift", "under-18". Rather than bolt a fixed schema onto the identity
record, this subsystem lets a tenant attach free tags to any user_id and query
them back — the primitive that per-group policies, reports and bulk actions build
on. Tags are plain lowercased strings; a person can carry many.

  * ``add`` / ``remove`` tags on an identity.
  * ``tags_of`` a person; ``members`` of a tag; ``all_tags`` with counts.
  * ``has`` a quick membership check for a policy gate.
  * ``any_of`` / ``all_of`` set queries across tags.

Registry: ``tags.json`` (env ``FACE_TAGS_FILE``).
"""

from __future__ import annotations

from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_TAGS_FILE", "tags.json")


def _clean(tag: str) -> str:
    return (tag or "").strip().lower()


def add(tenant: Optional[str], user_id: str, *new_tags: str) -> List[str]:
    uid = (user_id or "").strip()
    if not uid:
        raise ValueError("user_id is required.")
    clean = [_clean(t) for t in new_tags if _clean(t)]
    with _reg.mutate() as data:
        cur = data.setdefault(_reg.norm(tenant), {}).setdefault(uid, [])
        for t in clean:
            if t not in cur:
                cur.append(t)
        cur.sort()
        out = list(cur)
    return out


def remove(tenant: Optional[str], user_id: str, *drop: str) -> List[str]:
    t = _reg.norm(tenant)
    uid = (user_id or "").strip()
    dropset = {_clean(x) for x in drop}
    with _reg.mutate() as data:
        cur = (data.get(t) or {}).get(uid, [])
        cur[:] = [x for x in cur if x not in dropset]
        if not cur:
            (data.get(t) or {}).pop(uid, None)
        out = list(cur)
    return out


def tags_of(tenant: Optional[str], user_id: str) -> List[str]:
    return list((_reg.load().get(_reg.norm(tenant)) or {}).get((user_id or "").strip()) or [])


def has(tenant: Optional[str], user_id: str, tag: str) -> bool:
    return _clean(tag) in tags_of(tenant, user_id)


def members(tenant: Optional[str], tag: str) -> List[str]:
    tag = _clean(tag)
    return sorted(uid for uid, ts in (_reg.load().get(_reg.norm(tenant)) or {}).items()
                  if tag in ts)


def any_of(tenant: Optional[str], user_id: str, *tags: str) -> bool:
    have = set(tags_of(tenant, user_id))
    return any(_clean(t) in have for t in tags)


def all_of(tenant: Optional[str], user_id: str, *tags: str) -> bool:
    have = set(tags_of(tenant, user_id))
    return all(_clean(t) in have for t in tags)


def all_tags(tenant: Optional[str]) -> dict:
    counts: dict = {}
    for ts in (_reg.load().get(_reg.norm(tenant)) or {}).values():
        for t in ts:
            counts[t] = counts.get(t, 0) + 1
    return dict(sorted(counts.items()))
