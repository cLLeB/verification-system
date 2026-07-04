"""Versioned CBOR container for biometric template payloads.

One format for everything a template travels in — SQLite blobs, sync bundles,
and (Phase 2) signed credentials — so every consumer validates the same way.
A 3-byte magic prefix (``BE1``) distinguishes envelopes from the older FT1/FT2
binary blobs and Fernet ciphertext. Decoding is strict: unknown fields, wrong
enums, or a wrong version are rejected (never trust external bytes).
"""

from __future__ import annotations

from typing import Optional

import cbor2

MAGIC = b"BE1"
VERSION = 1

MODALITIES = ("face", "palm")
KINDS = ("raw", "protected", "quantized-protected")
DTYPES = ("f32", "i8")

_REQUIRED = ("v", "mod", "kind", "dim", "dtype", "data")
_ALLOWED = set(_REQUIRED) | {"seedref", "meta"}


class EnvelopeError(ValueError):
    """Bytes are not a valid template envelope."""


def is_envelope(blob) -> bool:
    return isinstance(blob, (bytes, bytearray)) and bytes(blob[:3]) == MAGIC


def encode(mod: str, kind: str, data: bytes, dim: int, dtype: str,
           seedref: Optional[str] = None, meta: Optional[dict] = None) -> bytes:
    env = {"v": VERSION, "mod": mod, "kind": kind, "dim": dim,
           "dtype": dtype, "data": bytes(data)}
    if seedref is not None:
        env["seedref"] = seedref
    if meta is not None:
        env["meta"] = dict(meta)
    _validate(env)
    return MAGIC + cbor2.dumps(env)


def decode(blob) -> dict:
    if not is_envelope(blob):
        raise EnvelopeError("not a template envelope (missing BE1 magic)")
    try:
        env = cbor2.loads(bytes(blob[3:]))
    except Exception as exc:
        raise EnvelopeError(f"undecodable CBOR payload: {exc}") from exc
    _validate(env)
    return env


def _validate(env) -> None:
    if not isinstance(env, dict):
        raise EnvelopeError("envelope must be a CBOR map")
    missing = [k for k in _REQUIRED if k not in env]
    if missing:
        raise EnvelopeError(f"missing required fields: {missing}")
    unknown = sorted(set(env) - _ALLOWED)
    if unknown:
        raise EnvelopeError(f"unknown fields: {unknown}")
    if env["v"] != VERSION:
        raise EnvelopeError(f"unsupported envelope version: {env['v']!r}")
    if env["mod"] not in MODALITIES:
        raise EnvelopeError(f"unknown modality: {env['mod']!r}")
    if env["kind"] not in KINDS:
        raise EnvelopeError(f"unknown template kind: {env['kind']!r}")
    if env["dtype"] not in DTYPES:
        raise EnvelopeError(f"unknown dtype: {env['dtype']!r}")
    if not isinstance(env["dim"], int) or isinstance(env["dim"], bool) or env["dim"] < 0:
        raise EnvelopeError(f"dim must be a non-negative integer, got {env['dim']!r}")
    if not isinstance(env["data"], bytes) or not env["data"]:
        raise EnvelopeError("data must be non-empty bytes")
    if "seedref" in env and not isinstance(env["seedref"], str):
        raise EnvelopeError("seedref must be a string")
    if "meta" in env and not isinstance(env["meta"], dict):
        raise EnvelopeError("meta must be a map")
