"""Consistent hashing ring - map keys to nodes with minimal reshuffling.

Sharding the gallery (or any workload) across several nodes needs a mapping from key →
node that stays stable when nodes join or leave: a naive ``hash(key) % n`` remaps almost
everything on a membership change. A consistent hashing ring remaps only the keys near the
changed node. This subsystem implements that ring with virtual nodes for even balance -
the technique behind Dynamo, Cassandra and memcached clients.

  * ``add_node``   place a node on the ring with ``vnodes`` virtual points (weight).
  * ``remove_node`` take a node off; only its keys move.
  * ``locate``     the node responsible for a key (first vnode clockwise).
  * ``locate_n``   the ``n`` distinct nodes for a key (for replication).
  * ``distribution`` approximate share of the keyspace per node.

Positions come from SHA-256 of ``node#vnode``; lookups binary-search the sorted ring. More
virtual nodes give smoother balance at the cost of a larger ring.

Registry: ``hashring.json`` (env ``FACE_HASHRING_FILE``).
"""

from __future__ import annotations

import bisect
import hashlib
from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_HASHRING_FILE", "hashring.json")


def _hash(s: str) -> int:
    return int.from_bytes(hashlib.sha256(s.encode("utf-8")).digest()[:8], "big")


def _key(tenant: Optional[str], ring: str) -> str:
    return _reg.scoped(tenant, (ring or 'default').strip() or 'default')


def _load(tenant: Optional[str], ring: str) -> dict:
    return _reg.load().get(_key(tenant, ring)) or {"nodes": {}, "points": []}


def add_node(tenant: Optional[str], node: str, vnodes: int = 100,
             ring: str = "default") -> dict:
    node = (node or "").strip()
    if not node:
        raise ValueError("node is required.")
    if int(vnodes) < 1:
        raise ValueError("vnodes must be >= 1.")
    with _reg.mutate() as data:
        rec = data.setdefault(_key(tenant, ring), {"nodes": {}, "points": []})
        if node in rec["nodes"]:
            return {"ok": False, "reason": "already-present"}
        rec["nodes"][node] = int(vnodes)
        points = [[_hash(f"{node}#{i}"), node] for i in range(int(vnodes))]
        rec["points"] = sorted(rec["points"] + points, key=lambda p: p[0])
    return {"ok": True, "node": node, "vnodes": int(vnodes)}


def remove_node(tenant: Optional[str], node: str, ring: str = "default") -> bool:
    node = (node or "").strip()
    with _reg.mutate() as data:
        rec = data.get(_key(tenant, ring))
        if not rec or node not in rec["nodes"]:
            return False
        del rec["nodes"][node]
        rec["points"] = [p for p in rec["points"] if p[1] != node]
    return True


def locate(tenant: Optional[str], key: str, ring: str = "default") -> Optional[str]:
    rec = _load(tenant, ring)
    points = rec["points"]
    if not points:
        return None
    positions = [p[0] for p in points]
    h = _hash(str(key))
    idx = bisect.bisect(positions, h) % len(points)
    return points[idx][1]


def locate_n(tenant: Optional[str], key: str, n: int, ring: str = "default") -> List[str]:
    rec = _load(tenant, ring)
    points = rec["points"]
    if not points:
        return []
    positions = [p[0] for p in points]
    h = _hash(str(key))
    start = bisect.bisect(positions, h) % len(points)
    result: List[str] = []
    for i in range(len(points)):
        node = points[(start + i) % len(points)][1]
        if node not in result:
            result.append(node)
            if len(result) >= int(n):
                break
    return result


def distribution(tenant: Optional[str], samples: int = 10000,
                 ring: str = "default") -> dict:
    rec = _load(tenant, ring)
    if not rec["points"]:
        return {}
    counts = {node: 0 for node in rec["nodes"]}
    positions = [p[0] for p in rec["points"]]
    for i in range(int(samples)):
        h = _hash(f"sample-{i}")
        idx = bisect.bisect(positions, h) % len(rec["points"])
        counts[rec["points"][idx][1]] += 1
    total = sum(counts.values()) or 1
    return {node: round(c / total, 4) for node, c in sorted(counts.items())}


def nodes(tenant: Optional[str], ring: str = "default") -> List[str]:
    return sorted(_load(tenant, ring)["nodes"].keys())
