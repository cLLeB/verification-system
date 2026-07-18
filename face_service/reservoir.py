"""Reservoir sampling — keep a uniform random sample of an unbounded stream.

To review "a representative sample of today's verifications" or audit a fair slice of a
huge event stream, you can't keep everything. Reservoir sampling (Algorithm R) maintains a
fixed-size sample where every item seen so far is equally likely to be included, in one
pass and constant memory — without knowing the stream length in advance. This subsystem
provides that, with a seedable RNG so tests and reproducible audits are deterministic.

  * ``create``    a reservoir of a fixed ``size`` (optionally seeded).
  * ``offer`` / ``offer_many`` — present items; each is kept with the correct
                probability so the reservoir stays a uniform sample.
  * ``sample``    the current sample (up to ``size`` items).
  * ``seen``      total items offered so far.

While fewer than ``size`` items have been seen the reservoir holds them all; after that,
item ``n`` replaces a random existing slot with probability ``size/n`` — the standard
guarantee that yields an unbiased sample.

Registry: ``reservoir.json`` (env ``FACE_RESERVOIR_FILE``).
"""

from __future__ import annotations

import random
from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_RESERVOIR_FILE", "reservoir.json")


def _key(tenant: Optional[str], name: str) -> str:
    return f"{_reg.norm(tenant)}::{(name or '').strip()}"


def create(tenant: Optional[str], name: str, size: int,
           seed: Optional[int] = None) -> dict:
    if not (name or "").strip():
        raise ValueError("reservoir name is required.")
    if int(size) < 1:
        raise ValueError("size must be >= 1.")
    with _reg.mutate() as data:
        data[_key(tenant, name)] = {"size": int(size), "seen": 0, "items": [],
                                    "state": random.Random(seed).getrandbits(63)
                                    if seed is not None else None,
                                    "seed": seed}
    return {"name": (name or "").strip(), "size": int(size)}


def _rng(rec: dict) -> random.Random:
    r = random.Random()
    if rec.get("state") is not None:
        r.seed(rec["state"])
    return r


def _offer_one(rec: dict, rng: random.Random, item) -> None:
    rec["seen"] += 1
    if len(rec["items"]) < rec["size"]:
        rec["items"].append(item)
    else:
        j = rng.randint(1, rec["seen"])
        if j <= rec["size"]:
            rec["items"][j - 1] = item


def offer(tenant: Optional[str], name: str, item) -> dict:
    with _reg.mutate() as data:
        rec = data.get(_key(tenant, name))
        if not rec:
            return {"ok": False, "reason": "unknown-reservoir"}
        rng = _rng(rec)
        _offer_one(rec, rng, item)
        if rec.get("state") is not None:
            rec["state"] = rng.getrandbits(63)   # advance deterministic state
    return {"ok": True, "seen": rec["seen"]}


def offer_many(tenant: Optional[str], name: str, items) -> dict:
    with _reg.mutate() as data:
        rec = data.get(_key(tenant, name))
        if not rec:
            return {"ok": False, "reason": "unknown-reservoir"}
        rng = _rng(rec)
        for item in items or []:
            _offer_one(rec, rng, item)
        if rec.get("state") is not None:
            rec["state"] = rng.getrandbits(63)
    return {"ok": True, "seen": rec["seen"]}


def sample(tenant: Optional[str], name: str) -> List:
    rec = _reg.load().get(_key(tenant, name))
    return list(rec["items"]) if rec else []


def seen(tenant: Optional[str], name: str) -> int:
    rec = _reg.load().get(_key(tenant, name))
    return rec["seen"] if rec else 0
