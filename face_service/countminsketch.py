"""Count-Min Sketch - approximate frequencies of a high-volume stream.

Bloom filters answer "seen it?" and HyperLogLog answers "how many distinct?"; the
Count-Min Sketch answers "how often?" - estimating each item's frequency in sub-linear
memory. It's how you find heavy hitters (the device fingerprints hammering the API, the
most-used doors) without a per-item counter. This subsystem implements it with conservative
update (which reduces over-estimation) and a bounded top-K tracker for the heavy hitters.

  * ``create``       a sketch sized by width/depth (or from error/confidence bounds).
  * ``add`` / ``add_many`` - increment an item's count (one write for a batch).
  * ``estimate``     approximate count for an item (never underestimates).
  * ``heavy_hitters`` the tracked top items by estimated count.

Counts come from ``depth`` independent hash rows; the estimate is the minimum across rows,
which bounds the over-estimate by ``error * total`` with probability ``1 - 1/2**depth``.
Conservative update only raises the smallest touched cells, tightening estimates.

Registry: ``countminsketch.json`` (env ``FACE_COUNTMINSKETCH_FILE``).
"""

from __future__ import annotations

import hashlib
import math
from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_COUNTMINSKETCH_FILE", "countminsketch.json")


def _key(tenant: Optional[str], name: str) -> str:
    return _reg.scoped(tenant, (name or '').strip())


def _positions(item: str, width: int, depth: int) -> List[int]:
    data = str(item).encode("utf-8")
    h1 = int.from_bytes(hashlib.sha256(data).digest()[:8], "big")
    h2 = int.from_bytes(hashlib.sha256(data + b"\x01").digest()[:8], "big") | 1
    return [(h1 + i * h2) % width for i in range(depth)]


def create(tenant: Optional[str], name: str, width: int = 2000, depth: int = 5,
           track_top: int = 20) -> dict:
    if not (name or "").strip():
        raise ValueError("sketch name is required.")
    width, depth = int(width), int(depth)
    if width < 1 or depth < 1:
        raise ValueError("width and depth must be >= 1.")
    with _reg.mutate() as data:
        data[_key(tenant, name)] = {"w": width, "d": depth, "total": 0,
                                    "rows": [[0] * width for _ in range(depth)],
                                    "top": {}, "track_top": int(track_top)}
    return {"name": (name or "").strip(), "width": width, "depth": depth}


def create_from_bounds(tenant: Optional[str], name: str, error: float = 0.001,
                       confidence: float = 0.99, track_top: int = 20) -> dict:
    if not 0 < error < 1 or not 0 < confidence < 1:
        raise ValueError("error and confidence must be in (0, 1).")
    width = int(math.ceil(math.e / error))
    depth = int(math.ceil(math.log(1 / (1 - confidence))))
    return create(tenant, name, width=width, depth=max(1, depth), track_top=track_top)


def _bump(rec: dict, item: str, amount: int) -> int:
    pos = _positions(item, rec["w"], rec["d"])
    current = min(rec["rows"][r][pos[r]] for r in range(rec["d"]))
    new_val = current + amount
    for r in range(rec["d"]):                    # conservative update
        if rec["rows"][r][pos[r]] < new_val:
            rec["rows"][r][pos[r]] = new_val
    rec["total"] += amount
    top = rec["top"]
    top[item] = new_val
    if len(top) > rec["track_top"] * 4:          # prune occasionally
        keep = dict(sorted(top.items(), key=lambda kv: -kv[1])[:rec["track_top"]])
        rec["top"] = keep
    return new_val


def add(tenant: Optional[str], name: str, item: str, amount: int = 1) -> dict:
    with _reg.mutate() as data:
        rec = data.get(_key(tenant, name))
        if not rec:
            return {"ok": False, "reason": "unknown-sketch"}
        val = _bump(rec, str(item), int(amount))
    return {"ok": True, "estimate": val}


def add_many(tenant: Optional[str], name: str, items) -> dict:
    with _reg.mutate() as data:
        rec = data.get(_key(tenant, name))
        if not rec:
            return {"ok": False, "reason": "unknown-sketch"}
        n = 0
        for item in items or []:
            _bump(rec, str(item), 1)
            n += 1
    return {"ok": True, "added": n}


def estimate(tenant: Optional[str], name: str, item: str) -> Optional[int]:
    rec = _reg.load().get(_key(tenant, name))
    if not rec:
        return None
    pos = _positions(str(item), rec["w"], rec["d"])
    return min(rec["rows"][r][pos[r]] for r in range(rec["d"]))


def heavy_hitters(tenant: Optional[str], name: str, top: int = 10) -> List[dict]:
    rec = _reg.load().get(_key(tenant, name))
    if not rec:
        return []
    ranked = sorted(rec["top"].items(), key=lambda kv: -kv[1])[:int(top)]
    return [{"item": k, "estimate": v} for k, v in ranked]
