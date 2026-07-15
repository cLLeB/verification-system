"""Identity aliases — resolve many external IDs to one canonical person.

The same human shows up under different keys in different systems: an HR employee
number, a badge id, an email, a legacy enrolment id. When two of those turn out to
be the same person, you do not want to re-enrol — you want to say "these are
aliases of one identity". This subsystem keeps an alias -> canonical map per
tenant, so any surface can normalise an incoming id to the canonical one before
applying policy, logging, or counting.

  * ``link``     point an alias at a canonical id (chains are collapsed).
  * ``resolve``  canonical id for any id (itself if unaliased).
  * ``aliases_of`` every alias that resolves to a canonical id.
  * ``unlink``   detach an alias.

Cycles are refused; linking A->B when B->A already exists raises. Resolution is
transitive-safe because links always store the fully-resolved canonical target.

Registry: ``aliases.json`` (env ``FACE_ALIASES_FILE``).
"""

from __future__ import annotations

from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_ALIASES_FILE", "aliases.json")


def resolve(tenant: Optional[str], user_id: str) -> str:
    uid = (user_id or "").strip()
    return (_reg.load().get(_reg.norm(tenant)) or {}).get(uid, uid)


def link(tenant: Optional[str], alias: str, canonical: str) -> dict:
    alias = (alias or "").strip()
    canonical = (canonical or "").strip()
    if not alias or not canonical:
        raise ValueError("alias and canonical are required.")
    if alias == canonical:
        raise ValueError("alias and canonical cannot be identical.")
    t = _reg.norm(tenant)
    # collapse: if canonical is itself an alias, point at its target
    canonical = resolve(t, canonical)
    if resolve(t, canonical) == alias:
        raise ValueError("linking would create a cycle.")
    with _reg.mutate() as data:
        m = data.setdefault(t, {})
        m[alias] = canonical
        # re-point anything that pointed at the alias to the new canonical
        for a, c in list(m.items()):
            if c == alias:
                m[a] = canonical
    return {"alias": alias, "canonical": canonical}


def unlink(tenant: Optional[str], alias: str) -> bool:
    t = _reg.norm(tenant)
    with _reg.mutate() as data:
        return (data.get(t) or {}).pop((alias or "").strip(), None) is not None


def aliases_of(tenant: Optional[str], canonical: str) -> List[str]:
    canonical = resolve(tenant, canonical)
    return sorted(a for a, c in (_reg.load().get(_reg.norm(tenant)) or {}).items()
                  if c == canonical)


def is_alias(tenant: Optional[str], user_id: str) -> bool:
    return (user_id or "").strip() in (_reg.load().get(_reg.norm(tenant)) or {})
