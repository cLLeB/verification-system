"""Subscription plans - tiers of features and limits a tenant is entitled to.

Commercially the platform is sold in tiers (Starter, Pro, Enterprise), each unlocking
features and raising limits. Rather than scatter ``if tenant == ...`` checks, this
subsystem is a plan catalog: define tiers with boolean feature entitlements and numeric
limits, subscribe a tenant to a tier, then ask "may this tenant use feature X" and
"what is their limit for Y". It complements per-key metering - plans set the ceilings,
metering counts against them.

  * ``define_plan``  a tier with ``features`` (set of enabled capability names) and
                     ``limits`` (named numeric caps; ``None``/absent = unlimited).
  * ``subscribe``    put a tenant on a plan.
  * ``can``          is a feature entitled on the tenant's current plan?
  * ``limit``        the numeric limit for a key (None = unlimited / no plan).
  * ``within_limit`` convenience: is ``current`` strictly under the tenant's limit?

Subscribing to an unknown plan fails loudly so a billing mistake can't silently grant
an empty entitlement set. A tenant with no subscription is denied all gated features
and has no limits raised - safe by default.

Registry: ``plans.json`` (env ``FACE_PLANS_FILE``).
"""

from __future__ import annotations

from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_PLANS_FILE", "plans.json")


def _root(data: dict) -> dict:
    return data.setdefault("__catalog__", {})


def define_plan(name: str, features: Optional[List[str]] = None,
                limits: Optional[dict] = None) -> dict:
    name = (name or "").strip().lower()
    if not name:
        raise ValueError("plan name is required.")
    feats = sorted({(f or "").strip() for f in (features or []) if (f or "").strip()})
    lims = {}
    for k, v in (limits or {}).items():
        if v is None:
            lims[k] = None
        else:
            lims[k] = int(v)
    plan = {"name": name, "features": feats, "limits": lims}
    with _reg.mutate() as data:
        _root(data)[name] = plan
    return {"name": name, "features": feats, "limits": lims}


def subscribe(tenant: Optional[str], plan_name: str) -> dict:
    plan_name = (plan_name or "").strip().lower()
    with _reg.mutate() as data:
        if plan_name not in _root(data):
            raise ValueError(f"unknown plan: {plan_name}")
        data.setdefault("__subs__", {})[_reg.norm(tenant)] = plan_name
    return {"tenant": _reg.norm(tenant), "plan": plan_name}


def _plan_for(tenant: Optional[str]) -> Optional[dict]:
    data = _reg.load()
    plan_name = (data.get("__subs__", {}) or {}).get(_reg.norm(tenant))
    if not plan_name:
        return None
    return (data.get("__catalog__", {}) or {}).get(plan_name)


def current_plan(tenant: Optional[str]) -> Optional[str]:
    plan = _plan_for(tenant)
    return plan["name"] if plan else None


def can(tenant: Optional[str], feature: str) -> bool:
    plan = _plan_for(tenant)
    return bool(plan) and (feature or "").strip() in plan["features"]


def limit(tenant: Optional[str], key: str) -> Optional[int]:
    plan = _plan_for(tenant)
    if not plan:
        return None
    return plan["limits"].get((key or "").strip())


def within_limit(tenant: Optional[str], key: str, current: int) -> bool:
    lim = limit(tenant, key)
    if lim is None:
        return True                # unlimited or no plan restriction on this key
    return int(current) < lim
