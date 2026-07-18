"""HyperLogLog — estimate distinct counts of huge streams in tiny memory.

"How many unique subjects verified this month" over millions of events shouldn't require
storing every id. HyperLogLog estimates cardinality from a fixed-size register array with
a few kilobytes and a small, known error — the algorithm behind Redis ``PFCOUNT`` and most
analytics stacks. This subsystem provides it: add items to a named sketch, read the
estimated distinct count, and merge sketches (unions) — e.g. combine per-day sketches into
a month without re-scanning.

  * ``create``   a sketch with precision ``p`` (2**p registers; higher p = more accuracy).
  * ``add`` / ``add_many`` — hash an item (or a batch, one write) into the sketch.
  * ``count``    the estimated number of distinct items added.
  * ``merge``    union two sketches into a destination (max of registers).

Uses a 64-bit hash: the first ``p`` bits pick a register, the rest's leading-zero run
updates it. Estimation applies the standard bias corrections for small and large ranges,
giving a typical error around ``1.04/sqrt(2**p)``.

Registry: ``hyperloglog.json`` (env ``FACE_HYPERLOGLOG_FILE``).
"""

from __future__ import annotations

import hashlib
import math
from typing import Optional

from ._registry import Registry

_reg = Registry("FACE_HYPERLOGLOG_FILE", "hyperloglog.json")

_HASH_BITS = 64


def _key(tenant: Optional[str], name: str) -> str:
    return f"{_reg.norm(tenant)}::{(name or '').strip()}"


def _hash64(item: str) -> int:
    return int.from_bytes(hashlib.sha256(str(item).encode("utf-8")).digest()[:8], "big")


def create(tenant: Optional[str], name: str, precision: int = 14) -> dict:
    if not (name or "").strip():
        raise ValueError("sketch name is required.")
    p = int(precision)
    if not 4 <= p <= 16:
        raise ValueError("precision must be 4..16.")
    m = 1 << p
    with _reg.mutate() as data:
        data[_key(tenant, name)] = {"p": p, "registers": [0] * m}
    return {"name": (name or "").strip(), "registers": m}


def _alpha(m: int) -> float:
    if m == 16:
        return 0.673
    if m == 32:
        return 0.697
    if m == 64:
        return 0.709
    return 0.7213 / (1 + 1.079 / m)


def _observe(rec: dict, item: str) -> None:
    p = rec["p"]
    h = _hash64(item)
    idx = h >> (_HASH_BITS - p)                  # first p bits -> register
    w = (h << p) & ((1 << _HASH_BITS) - 1)       # remaining bits
    # rank = position of leftmost 1-bit in the (64-p)-bit window, + 1
    rank = 1
    remaining = _HASH_BITS - p
    mask = 1 << (_HASH_BITS - 1)
    while remaining > 0 and not (w & mask):
        rank += 1
        w <<= 1
        remaining -= 1
    if rank > rec["registers"][idx]:
        rec["registers"][idx] = rank


def add(tenant: Optional[str], name: str, item: str) -> dict:
    with _reg.mutate() as data:
        rec = data.get(_key(tenant, name))
        if not rec:
            return {"ok": False, "reason": "unknown-sketch"}
        _observe(rec, item)
    return {"ok": True}


def add_many(tenant: Optional[str], name: str, items) -> dict:
    """Add a batch of items in a single persisted update (far cheaper I/O)."""
    with _reg.mutate() as data:
        rec = data.get(_key(tenant, name))
        if not rec:
            return {"ok": False, "reason": "unknown-sketch"}
        n = 0
        for item in items or []:
            _observe(rec, item)
            n += 1
    return {"ok": True, "added": n}


def count(tenant: Optional[str], name: str) -> Optional[int]:
    rec = _reg.load().get(_key(tenant, name))
    if not rec:
        return None
    regs = rec["registers"]
    m = len(regs)
    alpha = _alpha(m)
    raw = alpha * m * m / sum(2.0 ** -r for r in regs)
    if raw <= 2.5 * m:                              # small-range correction
        zeros = regs.count(0)
        if zeros:
            return int(round(m * math.log(m / zeros)))
    two32 = 2 ** 32
    if raw > two32 / 30.0:                          # large-range correction
        return int(round(-two32 * math.log(1 - raw / two32)))
    return int(round(raw))


def merge(tenant: Optional[str], dest: str, other: str) -> dict:
    with _reg.mutate() as data:
        a = data.get(_key(tenant, dest))
        b = data.get(_key(tenant, other))
        if not a or not b:
            return {"ok": False, "reason": "unknown-sketch"}
        if a["p"] != b["p"]:
            return {"ok": False, "reason": "precision-mismatch"}
        a["registers"] = [max(x, y) for x, y in zip(a["registers"], b["registers"])]
    return {"ok": True}
