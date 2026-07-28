"""Bloom filter - space-efficient "have we seen this?" at scale.

Some checks must be fast and memory-cheap over huge sets: "has this device fingerprint
appeared before", "is this template hash already enrolled", "seen this nonce". A Bloom
filter answers set membership with no false negatives and a tunable false-positive rate,
in a fraction of the memory of storing every item. This subsystem sizes a filter from a
target capacity and error rate and persists its bit array, so seen-checks survive restarts.

  * ``create``    a named filter sized for ``capacity`` items at ``error_rate``.
  * ``add``       insert an item (idempotent); returns whether it was probably new.
  * ``contains``  probable membership - ``False`` is definitive, ``True`` may be a rare
                  false positive.
  * ``stats``     configured/observed fill and the current estimated false-positive rate.

Bits are packed into a bytearray stored as hex; ``k`` independent positions come from
double-hashing two SHA-256-derived hashes, the standard Kirsch–Mitzenmacher technique.
Adding more than ``capacity`` items still works but the false-positive rate climbs, which
``stats`` surfaces.

Registry: ``bloomfilter.json`` (env ``FACE_BLOOMFILTER_FILE``).
"""

from __future__ import annotations

import hashlib
import math
from typing import Optional

from ._registry import Registry

_reg = Registry("FACE_BLOOMFILTER_FILE", "bloomfilter.json")


def _optimal(capacity: int, error_rate: float):
    m = math.ceil(-(capacity * math.log(error_rate)) / (math.log(2) ** 2))
    m = max(8, m)
    k = max(1, round((m / capacity) * math.log(2)))
    return int(m), int(k)


def _key(tenant: Optional[str], name: str) -> str:
    return _reg.scoped(tenant, (name or '').strip())


def create(tenant: Optional[str], name: str, capacity: int = 10000,
           error_rate: float = 0.01) -> dict:
    if not (name or "").strip():
        raise ValueError("filter name is required.")
    if int(capacity) < 1:
        raise ValueError("capacity must be >= 1.")
    if not 0 < float(error_rate) < 1:
        raise ValueError("error_rate must be in (0, 1).")
    m, k = _optimal(int(capacity), float(error_rate))
    nbytes = (m + 7) // 8
    with _reg.mutate() as data:
        data[_key(tenant, name)] = {"m": m, "k": k, "capacity": int(capacity),
                                    "error_rate": float(error_rate),
                                    "bits": bytes(nbytes).hex(), "count": 0}
    return {"name": (name or "").strip(), "bits": m, "hashes": k}


def _positions(item: str, m: int, k: int):
    data = str(item).encode("utf-8")
    h1 = int.from_bytes(hashlib.sha256(data).digest()[:8], "big")
    h2 = int.from_bytes(hashlib.sha256(data + b"\x01").digest()[:8], "big") | 1
    return [(h1 + i * h2) % m for i in range(k)]


def _get_set(ba: bytearray, pos: int) -> bool:
    return bool(ba[pos >> 3] & (1 << (pos & 7)))


def _set(ba: bytearray, pos: int) -> None:
    ba[pos >> 3] |= (1 << (pos & 7))


def add(tenant: Optional[str], name: str, item: str) -> dict:
    with _reg.mutate() as data:
        rec = data.get(_key(tenant, name))
        if not rec:
            return {"ok": False, "reason": "unknown-filter"}
        ba = bytearray(bytes.fromhex(rec["bits"]))
        was_new = False
        for p in _positions(item, rec["m"], rec["k"]):
            if not _get_set(ba, p):
                was_new = True
                _set(ba, p)
        if was_new:
            rec["count"] += 1
        rec["bits"] = bytes(ba).hex()
        return {"ok": True, "probably_new": was_new}


def contains(tenant: Optional[str], name: str, item: str) -> bool:
    rec = _reg.load().get(_key(tenant, name))
    if not rec:
        return False
    ba = bytes.fromhex(rec["bits"])
    return all((ba[p >> 3] & (1 << (p & 7))) for p in _positions(item, rec["m"], rec["k"]))


def stats(tenant: Optional[str], name: str) -> dict:
    rec = _reg.load().get(_key(tenant, name))
    if not rec:
        return {"exists": False}
    ba = bytes.fromhex(rec["bits"])
    set_bits = sum(bin(b).count("1") for b in ba)
    fill = set_bits / rec["m"]
    est_fp = fill ** rec["k"]
    return {"exists": True, "capacity": rec["capacity"], "count": rec["count"],
            "bits": rec["m"], "hashes": rec["k"], "fill_ratio": round(fill, 4),
            "estimated_fp_rate": round(est_fp, 6)}
