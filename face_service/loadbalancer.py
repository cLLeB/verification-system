"""Weighted load balancing — pick a backend fairly across capacity.

When verification is served by several backend nodes of differing capacity, requests should
be spread in proportion to each node's weight, smoothly rather than in bursts, and skip
nodes marked unhealthy. This subsystem implements Nginx's *smooth weighted round-robin* —
the same weights produce an evenly interleaved sequence (a,a,b,a,c,... not a,a,a,b,c) — plus
health marking so a downed node is bypassed until it recovers. It pairs with [[hashring]]
(sticky sharding) for the cases where round-robin, not affinity, is what you want.

  * ``add_backend``   register a backend with a weight (and mark it up).
  * ``pick``          the next backend by smooth WRR among healthy backends.
  * ``mark_down`` / ``mark_up`` — take a backend out of / back into rotation.
  * ``remove`` / ``backends`` — manage the pool.

Smooth WRR keeps a mutable ``current_weight`` per backend; each pick adds the effective
weight to every current weight, selects the maximum, and subtracts the total — yielding a
sequence whose density matches the weights with minimal clustering.

Registry: ``loadbalancer.json`` (env ``FACE_LOADBALANCER_FILE``).
"""

from __future__ import annotations

from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_LOADBALANCER_FILE", "loadbalancer.json")


def _key(tenant: Optional[str], pool: str) -> str:
    return f"{_reg.norm(tenant)}::{(pool or 'default').strip() or 'default'}"


def add_backend(tenant: Optional[str], backend: str, weight: int = 1,
                pool: str = "default") -> dict:
    backend = (backend or "").strip()
    if not backend:
        raise ValueError("backend is required.")
    if int(weight) < 1:
        raise ValueError("weight must be >= 1.")
    with _reg.mutate() as data:
        p = data.setdefault(_key(tenant, pool), {})
        p[backend] = {"weight": int(weight), "current": 0, "up": True}
    return {"backend": backend, "weight": int(weight)}


def pick(tenant: Optional[str], pool: str = "default") -> Optional[str]:
    with _reg.mutate() as data:
        p = data.get(_key(tenant, pool))
        if not p:
            return None
        healthy = {b: m for b, m in p.items() if m["up"]}
        if not healthy:
            return None
        total = sum(m["weight"] for m in healthy.values())
        best, best_val = None, None
        for b in sorted(healthy):                 # sorted -> deterministic ties
            m = p[b]
            m["current"] += m["weight"]
            if best_val is None or m["current"] > best_val:
                best, best_val = b, m["current"]
        p[best]["current"] -= total
        return best


def mark_down(tenant: Optional[str], backend: str, pool: str = "default") -> bool:
    with _reg.mutate() as data:
        p = data.get(_key(tenant, pool)) or {}
        if (backend or "").strip() not in p:
            return False
        p[(backend or "").strip()]["up"] = False
    return True


def mark_up(tenant: Optional[str], backend: str, pool: str = "default") -> bool:
    with _reg.mutate() as data:
        p = data.get(_key(tenant, pool)) or {}
        m = p.get((backend or "").strip())
        if not m:
            return False
        m["up"] = True
    return True


def remove(tenant: Optional[str], backend: str, pool: str = "default") -> bool:
    with _reg.mutate() as data:
        p = data.get(_key(tenant, pool)) or {}
        return p.pop((backend or "").strip(), None) is not None


def backends(tenant: Optional[str], pool: str = "default") -> List[dict]:
    p = _reg.load().get(_key(tenant, pool)) or {}
    return sorted(({"backend": b, "weight": m["weight"], "up": m["up"]}
                   for b, m in p.items()), key=lambda x: x["backend"])
