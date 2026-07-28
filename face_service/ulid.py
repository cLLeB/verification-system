"""ULID - time-sortable unique identifiers.

UUIDs are unique but random, so rows keyed by them scatter across an index and can't be
range-scanned by creation time. A ULID packs a 48-bit millisecond timestamp followed by 80
random bits into a 26-character Crockford-base32 string, so ULIDs are globally unique *and*
lexicographically sortable by creation time - ideal for event ids, audit keys and anything
you want ordered without a separate timestamp column. This subsystem generates and parses
them, including a monotonic factory that guarantees strictly increasing ids within the same
millisecond. Pure and stateless (the monotonic factory holds its own small state).

  * ``new``            a ULID for a timestamp (defaults to now).
  * ``timestamp_ms`` / ``timestamp`` - extract the embedded time from a ULID.
  * ``is_ulid``        validate a string is a well-formed ULID.
  * ``monotonic``      a factory returning ever-increasing ULIDs (ties broken by
                       incrementing the random component).

Crockford base32 excludes I, L, O, U to avoid transcription errors. The timestamp occupies
the first 10 characters, the randomness the last 16.
"""

from __future__ import annotations

import os
import time as _time
from typing import Callable, Optional

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_DECODE = {c: i for i, c in enumerate(_CROCKFORD)}
_TIME_LEN = 10
_RAND_LEN = 16


def _encode(value: int, length: int) -> str:
    chars = []
    for _ in range(length):
        chars.append(_CROCKFORD[value & 0x1F])
        value >>= 5
    return "".join(reversed(chars))


def new(timestamp_ms: Optional[int] = None, randomness: Optional[int] = None) -> str:
    ts = int(timestamp_ms if timestamp_ms is not None else _time.time() * 1000)
    if ts < 0 or ts >= (1 << 48):
        raise ValueError("timestamp out of 48-bit range.")
    rand = randomness if randomness is not None else int.from_bytes(os.urandom(10), "big")
    rand &= (1 << 80) - 1
    return _encode(ts, _TIME_LEN) + _encode(rand, _RAND_LEN)


def is_ulid(value: str) -> bool:
    s = (value or "").strip().upper()
    if len(s) != _TIME_LEN + _RAND_LEN:
        return False
    return all(c in _DECODE for c in s)


def _decode(s: str) -> int:
    value = 0
    for c in s:
        value = (value << 5) | _DECODE[c]
    return value


def timestamp_ms(value: str) -> Optional[int]:
    s = (value or "").strip().upper()
    if not is_ulid(s):
        return None
    return _decode(s[:_TIME_LEN])


def timestamp(value: str) -> Optional[float]:
    ms = timestamp_ms(value)
    return None if ms is None else ms / 1000.0


def monotonic() -> Callable[[Optional[int]], str]:
    """Return a generator function producing strictly increasing ULIDs."""
    state = {"ms": -1, "rand": 0}

    def _gen(timestamp_ms_arg: Optional[int] = None) -> str:
        ts = int(timestamp_ms_arg if timestamp_ms_arg is not None else _time.time() * 1000)
        if ts == state["ms"]:
            state["rand"] += 1                       # same ms -> increment randomness
        else:
            state["ms"] = ts
            state["rand"] = int.from_bytes(os.urandom(10), "big") & ((1 << 80) - 1)
        return _encode(ts, _TIME_LEN) + _encode(state["rand"] & ((1 << 80) - 1), _RAND_LEN)

    return _gen
