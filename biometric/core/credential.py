"""Portable offline biometric credential - the signed QR payload (trust platform
Phase 2, spec 6.1).

A credential carries a person's PROTECTED, int8-quantized template in its own
matching domain, signed by the issuing tenant's Ed25519 key, base45-encoded for
a QR. Any phone that trusts the issuer's public key can verify the holder with
no network and no shared database:

    scan -> decode -> check signature/expiry/revocation -> live capture ->
    project the live embedding with the credential's seed -> match on-device.

Domain seed: derived PUBLICLY from the credential id (SHA-256 of a fixed label
+ cid) - an offline cross-org verifier can never hold the issuer's secret. The
privacy properties do not depend on seed secrecy: a stolen/photographed QR is
(a) unmatchable against any store or other credential (its own domain),
(b) useless without the live person, (c) revocable, (d) expiring.

Wire format:  "FV1:" + base45( CBOR [payload_bytes, sig64] )
where ``payload_bytes`` is the CBOR-encoded payload map carried as a byte
string and ``sig`` = Ed25519 over exactly those bytes (COSE-style detached
payload). Verifiers check the signature over the received bytes and only then
parse them - no CBOR canonicalization to re-implement on any platform.
Payload map (spec 6.1): v, cid(16B), iss, kid, sub, mod[], tpl[BE1 envelopes],
iat, exp, and optional name / attrs. Decoding fails closed with typed error
codes (spec 6.6).
"""

from __future__ import annotations

import hashlib
import os
import time
from typing import Callable, List, Optional, Tuple

import cbor2
import numpy as np

from . import base45, envelope, protect, signing

VERSION = 1
PREFIX = "FV1:"
CID_LEN = 16
DEFAULT_TTL_DAYS = 365
MAX_TPL = 4                              # envelopes per credential (1 per modality is typical)
_SEED_LABEL = b"faceverify-cred-v1:"

_REQUIRED = ("v", "cid", "iss", "kid", "sub", "mod", "tpl", "iat", "exp")
_ALLOWED = set(_REQUIRED) | {"name", "attrs"}

# Typed failure codes (spec 6.6) - surfaced verbatim across API, web, Android.
UNSUPPORTED_VERSION = "unsupported_version"
MALFORMED = "malformed_credential"
BAD_SIGNATURE = "bad_signature"
EXPIRED = "credential_expired"
NOT_YET_VALID = "credential_not_yet_valid"
UNKNOWN_ISSUER = "unknown_issuer"
REVOKED = "credential_revoked"


class CredentialError(ValueError):
    """A credential failed to decode or verify. ``code`` is the typed reason."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def new_cid() -> bytes:
    return os.urandom(CID_LEN)


def cred_seed(cid: bytes) -> bytes:
    """The credential's protection-domain seed - a PUBLIC function of the cid,
    so any verifier can project a live capture into the credential's domain."""
    return hashlib.sha256(_SEED_LABEL + cid).digest()


# --- int8 quantization (kind="quantized-protected") --------------------------
def quantize(vec: np.ndarray) -> bytes:
    """f32 vector -> 4-byte LE scale + int8 payload (symmetric, per-vector scale)."""
    v = np.asarray(vec, dtype=np.float32)
    scale = float(np.max(np.abs(v))) or 1.0
    q = np.clip(np.rint(v * (127.0 / scale)), -127, 127).astype(np.int8)
    return np.float32(scale).tobytes() + q.tobytes()


def dequantize(data: bytes) -> np.ndarray:
    if len(data) < 5:
        raise CredentialError(MALFORMED, "quantized template too short")
    scale = float(np.frombuffer(data[:4], np.float32)[0])
    q = np.frombuffer(data[4:], np.int8).astype(np.float32)
    v = q * (scale / 127.0)
    n = float(np.linalg.norm(v))
    return v / n if n > 0 else v


def template_envelope(cid: bytes, modality: str, raw_embedding: np.ndarray) -> bytes:
    """Project a RAW embedding into the credential's own domain and pack it as a
    quantized-protected BE1 envelope."""
    proj = protect.transform(cred_seed(cid), np.asarray(raw_embedding, np.float32))[0]
    return envelope.encode(mod=modality, kind="quantized-protected",
                           data=quantize(proj), dim=int(proj.shape[0]), dtype="i8",
                           seedref=protect.cred_ref(cid.hex()))


# --- build / encode -----------------------------------------------------------
def build(cid: bytes, issuer: str, kid: str, subject: str,
          templates: List[bytes], modalities: List[str],
          expiry_days: int = DEFAULT_TTL_DAYS, name: Optional[str] = None,
          attrs: Optional[dict] = None, now: Optional[int] = None) -> dict:
    now = int(time.time()) if now is None else int(now)
    payload = {"v": VERSION, "cid": bytes(cid), "iss": issuer, "kid": kid,
               "sub": subject, "mod": list(modalities), "tpl": list(templates),
               "iat": now, "exp": now + int(expiry_days) * 86400}
    if name:
        payload["name"] = name
    if attrs:
        payload["attrs"] = dict(attrs)
    _validate(payload)
    return payload


def signing_bytes(payload: dict) -> bytes:
    return cbor2.dumps(payload, canonical=True)


def encode(payload: dict, sig: bytes) -> str:
    return PREFIX + base45.encode(
        cbor2.dumps([signing_bytes(payload), bytes(sig)]))


# --- decode / verify (fail closed) --------------------------------------------
def decode(text: str) -> Tuple[dict, bytes, bytes]:
    """Wire string -> (payload, payload_bytes, sig). Schema-validated; the
    signature is NOT yet checked (verify() checks it over payload_bytes)."""
    text = (text or "").strip()
    if not text.startswith(PREFIX):
        raise CredentialError(MALFORMED, "not a FaceVerify credential (missing FV1 prefix)")
    try:
        blob = base45.decode(text[len(PREFIX):])
        outer = cbor2.loads(blob)
    except (base45.Base45Error, Exception) as exc:
        raise CredentialError(MALFORMED, f"undecodable credential: {exc}") from exc
    if (not isinstance(outer, list) or len(outer) != 2
            or not isinstance(outer[0], bytes)
            or not isinstance(outer[1], bytes) or len(outer[1]) != 64):
        raise CredentialError(MALFORMED,
                              "credential must be [payload bytes, 64-byte signature]")
    payload_bytes, sig = outer
    try:
        payload = cbor2.loads(payload_bytes)
    except Exception as exc:
        raise CredentialError(MALFORMED, f"undecodable payload: {exc}") from exc
    _validate(payload)
    return payload, payload_bytes, sig


def verify(text: str, resolve_key: Callable[[str, str], Optional[bytes]],
           now: Optional[int] = None) -> dict:
    """Decode + verify signature and validity window. ``resolve_key(iss, kid)``
    returns the issuer's 32-byte public key, or None for an untrusted/unknown
    issuer. Returns the payload; raises ``CredentialError`` on ANY failure."""
    payload, payload_bytes, sig = decode(text)
    pk = resolve_key(payload["iss"], payload["kid"])
    if pk is None:
        raise CredentialError(UNKNOWN_ISSUER,
                              f"issuer '{payload['iss']}' is not trusted here")
    if not signing.verify(pk, payload_bytes, sig):
        raise CredentialError(BAD_SIGNATURE, "signature check failed - tampered or forged")
    now = int(time.time()) if now is None else int(now)
    if now > payload["exp"]:
        raise CredentialError(EXPIRED, "this credential has expired")
    if now + 300 < payload["iat"]:                    # 5 min clock skew allowance
        raise CredentialError(NOT_YET_VALID, "this credential is not yet valid")
    return payload


def match(payload: dict, modality: str, live_raw_embedding: np.ndarray) -> float:
    """Best cosine between the live capture (projected into the credential's
    domain) and the credential's stored template(s) for ``modality``."""
    probe = protect.transform(cred_seed(payload["cid"]),
                              np.asarray(live_raw_embedding, np.float32))[0]
    best = -1.0
    for blob in payload["tpl"]:
        env = envelope.decode(blob)
        if env["mod"] != modality or env["kind"] != "quantized-protected":
            continue
        ref = dequantize(env["data"])
        if ref.shape[0] != probe.shape[0]:
            continue
        best = max(best, float(probe @ ref))
    return best


def _validate(payload) -> None:
    if not isinstance(payload, dict):
        raise CredentialError(MALFORMED, "payload must be a CBOR map")
    missing = [k for k in _REQUIRED if k not in payload]
    if missing:
        raise CredentialError(MALFORMED, f"missing fields: {missing}")
    unknown = sorted(set(payload) - _ALLOWED)
    if unknown:
        raise CredentialError(MALFORMED, f"unknown fields: {unknown}")
    if payload["v"] != VERSION:
        raise CredentialError(UNSUPPORTED_VERSION,
                              f"unsupported credential version {payload['v']!r}")
    if not isinstance(payload["cid"], bytes) or len(payload["cid"]) != CID_LEN:
        raise CredentialError(MALFORMED, "cid must be 16 bytes")
    for k in ("iss", "kid", "sub"):
        if not isinstance(payload[k], str) or not payload[k]:
            raise CredentialError(MALFORMED, f"{k} must be a non-empty string")
    if (not isinstance(payload["mod"], list) or not payload["mod"]
            or not all(isinstance(m, str) for m in payload["mod"])):
        raise CredentialError(MALFORMED, "mod must be a non-empty list of strings")
    tpl = payload["tpl"]
    if (not isinstance(tpl, list) or not tpl or len(tpl) > MAX_TPL
            or not all(isinstance(t, bytes) for t in tpl)):
        raise CredentialError(MALFORMED, f"tpl must be 1..{MAX_TPL} envelope blobs")
    for blob in tpl:
        try:
            env = envelope.decode(blob)
        except envelope.EnvelopeError as exc:
            raise CredentialError(MALFORMED, f"bad template envelope: {exc}") from exc
        if env["kind"] != "quantized-protected" or env["dtype"] != "i8":
            raise CredentialError(MALFORMED, "templates must be quantized-protected int8")
    for k in ("iat", "exp"):
        if not isinstance(payload[k], int) or isinstance(payload[k], bool) or payload[k] <= 0:
            raise CredentialError(MALFORMED, f"{k} must be a positive integer")
    if payload["exp"] <= payload["iat"]:
        raise CredentialError(MALFORMED, "exp must be after iat")
    if "name" in payload and not isinstance(payload["name"], str):
        raise CredentialError(MALFORMED, "name must be a string")
    if "attrs" in payload and not isinstance(payload["attrs"], dict):
        raise CredentialError(MALFORMED, "attrs must be a map")
