"""Hash chain - a tamper-evident, append-only ledger for critical events.

The audit log records what happened, but a plain log can be edited after the
fact: delete the line that shows the door opened at 3am and nobody knows. This
subsystem lets any surface append events into a per-tenant chain where each entry
carries the SHA-256 of the previous entry plus its own payload. Change, remove,
or reorder any past entry and every following hash stops matching - so tampering
is *detectable* even if the file is writable.

  * ``append``  add an event; returns its sequence number and hash.
  * ``verify``  walk the chain and confirm every link; returns the first break
                (or None if intact).
  * ``head``    the current tip hash - publish/anchor it elsewhere (a webhook,
                a second store) and even wholesale file replacement is caught.

This is a genuine ledger, not a cache: entries are never rewritten, only
appended. Payloads should be small facts (event type, subject, timestamp), never
biometric data.

Registry: ``hashchain.json`` (env ``FACE_HASHCHAIN_FILE``).
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_HASHCHAIN_FILE", "hashchain.json")

GENESIS = "0" * 64


def _hash(prev: str, seq: int, ts: int, event: str, payload: dict) -> str:
    body = json.dumps({"prev": prev, "seq": seq, "ts": ts, "event": event,
                       "payload": payload}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def append(tenant: Optional[str], event: str, payload: Optional[dict] = None,
           ts: Optional[int] = None) -> dict:
    event = (event or "").strip() or "event"
    payload = payload or {}
    ts = int(ts if ts is not None else time.time())
    t = _reg.norm(tenant)
    with _reg.mutate() as data:
        chain = data.setdefault(t, [])
        prev = chain[-1]["hash"] if chain else GENESIS
        seq = len(chain)
        entry = {"seq": seq, "ts": ts, "event": event, "payload": payload,
                 "prev": prev, "hash": _hash(prev, seq, ts, event, payload)}
        chain.append(entry)
    return {"seq": entry["seq"], "hash": entry["hash"]}


def entries(tenant: Optional[str]) -> List[dict]:
    return list(_reg.load().get(_reg.norm(tenant)) or [])


def head(tenant: Optional[str]) -> str:
    chain = _reg.load().get(_reg.norm(tenant)) or []
    return chain[-1]["hash"] if chain else GENESIS


def verify(tenant: Optional[str]) -> Optional[dict]:
    """Walk the chain. Returns None if intact, else a dict describing the first
    broken link (its seq and why)."""
    prev = GENESIS
    for i, e in enumerate(_reg.load().get(_reg.norm(tenant)) or []):
        if e.get("seq") != i:
            return {"seq": i, "reason": "sequence_out_of_order"}
        if e.get("prev") != prev:
            return {"seq": i, "reason": "prev_hash_mismatch"}
        expect = _hash(prev, e["seq"], e["ts"], e["event"], e.get("payload") or {})
        if e.get("hash") != expect:
            return {"seq": i, "reason": "hash_mismatch"}
        prev = e["hash"]
    return None


def is_intact(tenant: Optional[str]) -> bool:
    return verify(tenant) is None
