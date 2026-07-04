# Trust Platform — Phase 0: Crypto & Identity Foundations — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the cryptographic plumbing for the Trust Platform (spec: `docs/superpowers/specs/2026-07-04-trust-platform-design.md` §4): per-tenant Ed25519 issuer keys, a versioned CBOR template envelope, and KEK-wrapped per-store data keys — exposed on admin console, tenant portal, REST API, and SDKs.

**Architecture:** Three new pure modules (`biometric/core/envelope.py`, `biometric/core/signing.py`, `face_service/issuer_keys.py`) plus surgical extensions to `biometric/core/crypto.py` (key wrapping, rotation, crypto-erase) and `biometric/core/store.py` (envelope-wrapped blobs, fully backward compatible). Service layer adds two `/v1` endpoints, two admin endpoints, two portal endpoints, and UI panels.

**Tech Stack:** Python/Flask, `cryptography` (Ed25519, Fernet — already a dep), `cbor2` (new dep), SQLite stores, pytest with existing `client`/`make_key` fixtures.

## Global Constraints

- Envelope magic is `b"BE1"`, version `1`; fields exactly per spec §4.2 (`v, mod, kind, dim, dtype, seedref?, data, meta?`); kinds `raw | protected | quantized-protected`; dtypes `f32 | i8`; modalities `face | palm`.
- Key ID (`kid`) = first 16 hex chars of SHA-256 of the raw 32-byte Ed25519 public key.
- All decode paths fail closed: malformed input → typed error / `None`, never an exception escaping to a route, never a silent pass.
- Backward compatibility is non-negotiable: existing FT1/FT2 blobs, legacy JSON templates, passphrase-derived ciphers, and plaintext `.key` files must all still read.
- Every new endpoint: audit event via `audit.log(tenant, action, actor=..., success=..., detail=...)`, scope-gated, documented in `openapi.yaml`.
- UI copy is plain language (a semi-technical reader must understand it without knowing what Ed25519 is).
- New files follow house style: module docstring explaining *why*, `from __future__ import annotations`, small focused functions.
- Run commands from the repo root. Full suite must stay green: `python -m pytest tests/ -x -q` (some tests skip without the face model pack — skips are fine, failures are not).
- Commit after every task with the repo's conventional-commit style (`feat:`, `fix:`, `docs:`, `test:`). No attribution footer (disabled globally).

---

### Task 1: Template envelope module

**Files:**
- Modify: `requirements.txt` (add `cbor2`)
- Create: `biometric/core/envelope.py`
- Test: `tests/test_envelope.py`

**Interfaces:**
- Produces: `envelope.MAGIC: bytes`, `envelope.VERSION: int`,
  `envelope.encode(mod: str, kind: str, data: bytes, dim: int, dtype: str, seedref: str | None = None, meta: dict | None = None) -> bytes`,
  `envelope.decode(blob: bytes) -> dict`, `envelope.is_envelope(blob) -> bool`,
  `envelope.EnvelopeError(ValueError)`.
  Tasks 5+ and all later phases consume these exact names.

- [x] **Step 1: Add the dependency**

In `requirements.txt`, under the `# --- Shared ---` section, after the `cryptography>=42.0` line, add:

```
cbor2>=5.6               # CBOR template envelopes + signed credentials (trust platform)
```

Run: `pip install cbor2`
Expected: installs cleanly.

- [x] **Step 2: Write the failing tests**

Create `tests/test_envelope.py`:

```python
"""Template envelope: versioned CBOR container round-trip + strict validation."""
import pytest

from biometric.core import envelope
from biometric.core.envelope import EnvelopeError


def test_round_trip_minimal():
    blob = envelope.encode(mod="face", kind="raw", data=b"\x01\x02\x03", dim=3, dtype="f32")
    assert envelope.is_envelope(blob)
    env = envelope.decode(blob)
    assert env["v"] == envelope.VERSION
    assert env["mod"] == "face" and env["kind"] == "raw"
    assert env["dim"] == 3 and env["dtype"] == "f32"
    assert env["data"] == b"\x01\x02\x03"
    assert "seedref" not in env and "meta" not in env


def test_round_trip_full():
    blob = envelope.encode(mod="palm", kind="quantized-protected", data=b"\x00" * 16,
                           dim=16, dtype="i8", seedref="cred:abc123",
                           meta={"engine_version": "1.0", "quality": 0.9})
    env = envelope.decode(blob)
    assert env["seedref"] == "cred:abc123"
    assert env["meta"]["quality"] == 0.9


def test_not_an_envelope():
    assert not envelope.is_envelope(b"FT2xxxx")
    assert not envelope.is_envelope(b"")
    assert not envelope.is_envelope(None)
    with pytest.raises(EnvelopeError):
        envelope.decode(b"FT2xxxx")


def test_rejects_garbage_cbor():
    with pytest.raises(EnvelopeError):
        envelope.decode(envelope.MAGIC + b"\xff\xff\xff")


def test_rejects_bad_fields():
    good = dict(mod="face", kind="raw", data=b"x", dim=1, dtype="f32")
    for bad in (dict(good, mod="iris"), dict(good, kind="plain"),
                dict(good, dtype="f64"), dict(good, dim=-1), dict(good, data=b"")):
        with pytest.raises(EnvelopeError):
            envelope.encode(**bad)


def test_rejects_unknown_field_and_wrong_version():
    import cbor2
    env = {"v": 99, "mod": "face", "kind": "raw", "dim": 1, "dtype": "f32", "data": b"x"}
    with pytest.raises(EnvelopeError):
        envelope.decode(envelope.MAGIC + cbor2.dumps(env))
    env = {"v": 1, "mod": "face", "kind": "raw", "dim": 1, "dtype": "f32",
           "data": b"x", "surprise": True}
    with pytest.raises(EnvelopeError):
        envelope.decode(envelope.MAGIC + cbor2.dumps(env))
```

- [x] **Step 3: Run tests to verify they fail**

Run: `python -m pytest tests/test_envelope.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'biometric.core.envelope'` (or ImportError).

- [x] **Step 4: Implement the module**

Create `biometric/core/envelope.py`:

```python
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
```

- [x] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_envelope.py -v`
Expected: all PASS.

- [x] **Step 6: Commit**

```bash
git add requirements.txt biometric/core/envelope.py tests/test_envelope.py
git commit -m "feat(trust): versioned CBOR template envelope (BE1) with strict validation"
```

---

### Task 2: Ed25519 signing helpers

**Files:**
- Create: `biometric/core/signing.py`
- Test: `tests/test_signing.py`

**Interfaces:**
- Produces: `signing.generate() -> tuple[bytes, bytes]` (32-byte private, 32-byte public),
  `signing.kid(pk: bytes) -> str` (16 hex chars),
  `signing.sign(sk: bytes, message: bytes) -> bytes` (64-byte signature),
  `signing.verify(pk: bytes, message: bytes, signature: bytes) -> bool` (never raises).
  Task 3 and Phase 2 consume these exact names.

- [x] **Step 1: Write the failing tests**

Create `tests/test_signing.py`:

```python
"""Ed25519 helpers: keygen, key ids, sign/verify (verify never raises)."""
from biometric.core import signing


def test_generate_shapes():
    sk, pk = signing.generate()
    assert len(sk) == 32 and len(pk) == 32 and sk != pk


def test_kid_is_stable_16_hex():
    _, pk = signing.generate()
    k = signing.kid(pk)
    assert len(k) == 16 and int(k, 16) >= 0
    assert signing.kid(pk) == k


def test_sign_verify_round_trip():
    sk, pk = signing.generate()
    sig = signing.sign(sk, b"hello")
    assert len(sig) == 64
    assert signing.verify(pk, b"hello", sig)


def test_verify_rejects_tamper_and_never_raises():
    sk, pk = signing.generate()
    sig = signing.sign(sk, b"hello")
    assert not signing.verify(pk, b"HELLO", sig)
    assert not signing.verify(pk, b"hello", sig[:-1] + bytes([sig[-1] ^ 1]))
    _, other_pk = signing.generate()
    assert not signing.verify(other_pk, b"hello", sig)
    assert not signing.verify(b"short", b"hello", sig)          # malformed key
    assert not signing.verify(pk, b"hello", b"not-a-signature")  # malformed sig
```

- [x] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_signing.py -v`
Expected: FAIL — ImportError.

- [x] **Step 3: Implement the module**

Create `biometric/core/signing.py`:

```python
"""Ed25519 signing primitives for the trust platform.

Raw-bytes API (32-byte keys, 64-byte signatures) so callers never touch
``cryptography`` objects. ``verify`` returns False on ANY failure — malformed
key, malformed signature, or mismatch — so verification code can never be
crashed by attacker-controlled bytes.
"""

from __future__ import annotations

import hashlib

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


def generate() -> tuple:
    """New keypair -> (private_bytes, public_bytes), 32 bytes each."""
    sk = Ed25519PrivateKey.generate()
    return sk.private_bytes_raw(), sk.public_key().public_bytes_raw()


def kid(pk: bytes) -> str:
    """Stable key id: first 16 hex chars of SHA-256 of the raw public key."""
    return hashlib.sha256(pk).hexdigest()[:16]


def sign(sk: bytes, message: bytes) -> bytes:
    return Ed25519PrivateKey.from_private_bytes(sk).sign(message)


def verify(pk: bytes, message: bytes, signature: bytes) -> bool:
    try:
        Ed25519PublicKey.from_public_bytes(pk).verify(signature, message)
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False
```

- [x] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_signing.py -v`
Expected: all PASS.

- [x] **Step 5: Commit**

```bash
git add biometric/core/signing.py tests/test_signing.py
git commit -m "feat(trust): Ed25519 signing helpers with fail-closed verify"
```

---

### Task 3: Per-tenant issuer key registry

**Files:**
- Create: `face_service/issuer_keys.py`
- Test: `tests/test_issuer_keys.py`

**Interfaces:**
- Consumes: `biometric.core.signing` (Task 2), `biometric.core.crypto.get_cipher` (existing).
- Produces: `issuer_keys.get_or_create(tenant) -> dict` (public view: `{kid, public_key, created, status}`),
  `issuer_keys.rotate(tenant) -> dict`, `issuer_keys.public_keys(tenant) -> list[dict]` (active first),
  `issuer_keys.sign_for(tenant, message: bytes) -> tuple[str, bytes]` (`(kid, signature)`),
  `issuer_keys.verify_for(tenant, kid, message, signature) -> bool`,
  `issuer_keys.remove(tenant) -> bool`, `issuer_keys.key_dir() -> str`.
  Tenant `None`/`""` normalizes to `"default"`. Tasks 6–8 and Phase 2 consume these.

- [x] **Step 1: Write the failing tests**

Create `tests/test_issuer_keys.py`:

```python
"""Per-tenant issuer keys: create-on-demand, rotate keeps verify-only history,
private keys encrypted at rest, offboard removal."""
import base64
import json
import os

import pytest

from face_service import issuer_keys


@pytest.fixture(autouse=True)
def isolated_keydir(tmp_path, monkeypatch):
    monkeypatch.setenv("BIO_ISSUER_KEY_DIR", str(tmp_path / "issuer"))


def test_get_or_create_is_idempotent():
    a = issuer_keys.get_or_create("acme")
    b = issuer_keys.get_or_create("acme")
    assert a["kid"] == b["kid"] and a["status"] == "active"
    assert len(a["kid"]) == 16 and a["public_key"]


def test_tenant_normalization():
    assert issuer_keys.get_or_create(None)["kid"] == issuer_keys.get_or_create("")["kid"]


def test_rotate_retires_old_key():
    old = issuer_keys.get_or_create("acme")
    new = issuer_keys.rotate("acme")
    assert new["kid"] != old["kid"]
    keys = issuer_keys.public_keys("acme")
    assert keys[0]["kid"] == new["kid"] and keys[0]["status"] == "active"
    retired = [k for k in keys if k["status"] == "retired"]
    assert [k["kid"] for k in retired] == [old["kid"]]
    assert "retired_at" in retired[0]


def test_sign_and_verify_across_rotation():
    kid1, sig1 = issuer_keys.sign_for("acme", b"payload-1")
    issuer_keys.rotate("acme")
    kid2, sig2 = issuer_keys.sign_for("acme", b"payload-2")
    assert kid1 != kid2
    assert issuer_keys.verify_for("acme", kid1, b"payload-1", sig1)   # retired key still verifies
    assert issuer_keys.verify_for("acme", kid2, b"payload-2", sig2)
    assert not issuer_keys.verify_for("acme", kid2, b"payload-1", sig1)
    assert not issuer_keys.verify_for("acme", "0" * 16, b"payload-1", sig1)


def test_private_key_encrypted_and_dropped_on_retire():
    issuer_keys.rotate("acme")   # creates then retires nothing; ensures active exists
    issuer_keys.rotate("acme")   # now there is a retired entry
    path = os.path.join(issuer_keys.key_dir(), "issuer_keys.json")
    data = json.load(open(path, encoding="utf-8"))
    active = data["acme"]["active"]
    # stored sk must NOT be a raw 32-byte ed25519 key (it's Fernet ciphertext)
    assert len(base64.b64decode(active["sk"])) != 32
    for retired in data["acme"]["retired"]:
        assert "sk" not in retired


def test_remove_for_offboarding():
    issuer_keys.get_or_create("gone")
    assert issuer_keys.remove("gone") is True
    assert issuer_keys.remove("gone") is False
```

- [x] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_issuer_keys.py -v`
Expected: FAIL — ImportError.

- [x] **Step 3: Implement the module**

Create `face_service/issuer_keys.py`:

```python
"""Per-tenant Ed25519 issuer keypairs (the tenant's signing identity).

Anything the platform issues on a tenant's behalf — portable credentials
(Phase 2), signed bundles, the trust store — is signed with the tenant's
active key. Rotation retires the old key: its PRIVATE half is dropped (it can
never sign again) but the public half is retained so previously issued
signatures keep verifying until their artifacts expire.

Private keys are encrypted at rest with the key-directory cipher from
``biometric.core.crypto`` (key file by default; BIO_DB_KEY passphrase if set).
The registry lives in ``BIO_ISSUER_KEY_DIR`` (default: ``secrets/issuer``),
read at call time so tests and deploys can repoint it via the environment.
"""

from __future__ import annotations

import base64
import json
import os
import threading
import time
from typing import List, Optional, Tuple

from biometric.core import signing
from biometric.core.crypto import get_cipher

_FILE = "issuer_keys.json"
_lock = threading.Lock()


def key_dir() -> str:
    return os.environ.get("BIO_ISSUER_KEY_DIR", os.path.join("secrets", "issuer"))


def _norm(tenant: Optional[str]) -> str:
    return (tenant or "default").strip() or "default"


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def _load() -> dict:
    path = os.path.join(key_dir(), _FILE)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _save(data: dict) -> None:
    os.makedirs(key_dir(), exist_ok=True)
    path = os.path.join(key_dir(), _FILE)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _new_key() -> dict:
    sk, pk = signing.generate()
    cipher = get_cipher(key_dir())
    stored = cipher.encrypt(sk) if cipher is not None else sk
    return {"kid": signing.kid(pk), "pk": _b64(pk), "sk": _b64(stored),
            "created": int(time.time())}


def _public(k: dict, status: str) -> dict:
    out = {"kid": k["kid"], "public_key": k["pk"],
           "created": k["created"], "status": status}
    if "retired_at" in k:
        out["retired_at"] = k["retired_at"]
    return out


def get_or_create(tenant: Optional[str]) -> dict:
    t = _norm(tenant)
    with _lock:
        data = _load()
        rec = data.get(t)
        if not rec or not rec.get("active"):
            rec = {"active": _new_key(), "retired": (rec or {}).get("retired", [])}
            data[t] = rec
            _save(data)
        return _public(rec["active"], "active")


def rotate(tenant: Optional[str]) -> dict:
    t = _norm(tenant)
    with _lock:
        data = _load()
        rec = data.setdefault(t, {"active": None, "retired": []})
        old = rec.get("active")
        if old:
            retired = {k: v for k, v in old.items() if k != "sk"}  # drop private half
            retired["retired_at"] = int(time.time())
            rec["retired"].append(retired)
        rec["active"] = _new_key()
        _save(data)
        return _public(rec["active"], "active")


def public_keys(tenant: Optional[str]) -> List[dict]:
    """Active key first (created on demand), then retired verify-only keys,
    newest first."""
    active = get_or_create(tenant)
    rec = _load().get(_norm(tenant)) or {}
    return [active] + [_public(k, "retired") for k in reversed(rec.get("retired", []))]


def sign_for(tenant: Optional[str], message: bytes) -> Tuple[str, bytes]:
    """Sign with the tenant's ACTIVE key (created on first use) -> (kid, sig)."""
    get_or_create(tenant)
    rec = _load()[_norm(tenant)]["active"]
    sk = base64.b64decode(rec["sk"])
    cipher = get_cipher(key_dir())
    if cipher is not None:
        sk = cipher.decrypt(sk)
    return rec["kid"], signing.sign(sk, message)


def verify_for(tenant: Optional[str], kid: str, message: bytes, signature: bytes) -> bool:
    """Verify against the tenant's key with this kid (active or retired)."""
    for k in public_keys(tenant):
        if k["kid"] == kid:
            return signing.verify(base64.b64decode(k["public_key"]), message, signature)
    return False


def remove(tenant: Optional[str]) -> bool:
    """Offboarding: drop the tenant's signing identity entirely."""
    t = _norm(tenant)
    with _lock:
        data = _load()
        if t not in data:
            return False
        del data[t]
        _save(data)
        return True
```

- [x] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_issuer_keys.py -v`
Expected: all PASS.

Note: `test_private_key_encrypted_and_dropped_on_retire` relies on `get_cipher` creating a
`.key` file in the issuer dir (encryption on by default) — that is existing behavior of
`biometric/core/crypto.py`.

- [x] **Step 5: Commit**

```bash
git add face_service/issuer_keys.py tests/test_issuer_keys.py
git commit -m "feat(trust): per-tenant Ed25519 issuer key registry with rotation history"
```

---

### Task 4: KEK-wrapped data keys, master rotation, crypto-erase

**Files:**
- Modify: `biometric/core/crypto.py`
- Test: `tests/test_crypto_kek.py`

**Interfaces:**
- Consumes: existing `get_cipher(db_path, passphrase=None)` behavior (must stay compatible).
- Produces: `crypto.get_cipher(...)` (same signature, new wrapped-key behavior for NEW stores),
  `crypto.rotate_master(db_path: str, old_passphrase: str, new_passphrase: str) -> bool`,
  `crypto.erase_keys(db_path: str) -> int`.
  Task 5's CLI and later offboarding docs consume `erase_keys`/`rotate_master`.

**Design (from spec §4.3):** with a master passphrase set, a *new* store gets a random
data key stored only as `.key.wrapped` (Fernet-encrypted by a KEK derived from the
passphrase + per-store salt). Rotating the master passphrase re-wraps the data key —
no data re-encryption. Back-compat: existing passphrase-derived stores keep deriving
the same key; existing plaintext `.key` files are wrapped on first open when a
passphrase is present.

- [x] **Step 1: Write the failing tests**

Create `tests/test_crypto_kek.py`:

```python
"""KEK-wrapped store data keys: new stores, legacy migration, master rotation,
crypto-erase."""
import os

import pytest

from biometric.core import crypto


def _files(d):
    return sorted(os.listdir(d)) if os.path.isdir(d) else []


def test_fresh_store_with_passphrase_gets_wrapped_key(tmp_path):
    d = str(tmp_path / "s1")
    c = crypto.get_cipher(d, passphrase="master-pw")
    token = c.encrypt(b"secret template")
    assert ".key.wrapped" in _files(d) and ".salt" in _files(d)
    assert ".key" not in _files(d)
    # reopening yields the same data key
    c2 = crypto.get_cipher(d, passphrase="master-pw")
    assert c2.decrypt(token) == b"secret template"


def test_wrong_passphrase_fails_fast_on_wrapped_store(tmp_path):
    d = str(tmp_path / "s2")
    crypto.get_cipher(d, passphrase="right")
    with pytest.raises(Exception):
        crypto.get_cipher(d, passphrase="wrong")


def test_plaintext_keyfile_store_migrates_to_wrapped(tmp_path):
    d = str(tmp_path / "s3")
    c = crypto.get_cipher(d)                      # no passphrase -> plaintext .key
    token = c.encrypt(b"old data")
    assert ".key" in _files(d)
    c2 = crypto.get_cipher(d, passphrase="master-pw")   # passphrase introduced
    assert c2.decrypt(token) == b"old data"       # same data key, now wrapped
    assert ".key.wrapped" in _files(d) and ".key" not in _files(d)


def test_legacy_derived_store_still_reads(tmp_path):
    """Stores encrypted by the OLD code path (salt + passphrase-derived key,
    no key files) must keep decrypting unchanged."""
    d = str(tmp_path / "s4")
    old = crypto.get_cipher(d, passphrase="pw")   # fresh -> wrapped in new code
    # simulate a legacy store: remove the wrapped key, keep only the salt;
    # legacy data was encrypted with the KDF-derived key directly
    os.remove(os.path.join(d, ".key.wrapped"))
    legacy_cipher = crypto.get_cipher(d, passphrase="pw")
    token = legacy_cipher.encrypt(b"legacy blob")
    assert crypto.get_cipher(d, passphrase="pw").decrypt(token) == b"legacy blob"


def test_rotate_master_rewraps_without_reencrypting(tmp_path):
    d = str(tmp_path / "s5")
    token = crypto.get_cipher(d, passphrase="old-pw").encrypt(b"data")
    assert crypto.rotate_master(d, "old-pw", "new-pw") is True
    assert crypto.get_cipher(d, passphrase="new-pw").decrypt(token) == b"data"
    with pytest.raises(Exception):
        crypto.get_cipher(d, passphrase="old-pw")


def test_rotate_master_refuses_legacy_and_keyfile_stores(tmp_path):
    d = str(tmp_path / "s6")
    crypto.get_cipher(d)                                   # key-file store
    assert crypto.rotate_master(d, "a", "b") is False


def test_erase_keys_makes_data_unrecoverable(tmp_path):
    d = str(tmp_path / "s7")
    crypto.get_cipher(d, passphrase="pw").encrypt(b"data")
    removed = crypto.erase_keys(d)
    assert removed >= 2                                    # .salt + .key.wrapped
    assert not any(f in _files(d) for f in (".salt", ".key", ".key.wrapped"))
```

- [x] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_crypto_kek.py -v`
Expected: FAIL — `.key.wrapped` never created, `rotate_master`/`erase_keys` missing.

- [x] **Step 3: Implement**

In `biometric/core/crypto.py`:

Add after the `_KEY_FILE = ".key"` line:

```python
_WRAPPED_KEY_FILE = ".key.wrapped"
```

Add a KEK-derivation helper (below `_load_or_create`):

```python
def _derive_kek(db_path: str, passphrase: str) -> bytes:
    """Fernet key derived from the master passphrase + this store's salt."""
    salt = _load_or_create(db_path, _SALT_FILE, lambda: os.urandom(16))
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt,
                     iterations=_PBKDF2_ITERATIONS)
    return base64.urlsafe_b64encode(kdf.derive(passphrase.encode("utf-8")))
```

Replace the body of `get_cipher` with:

```python
def get_cipher(db_path: str, passphrase: Optional[str] = None):
    if not _AVAILABLE:
        return None
    if passphrase is None:
        passphrase = next((os.environ[v] for v in _KEY_ENV_VARS if os.environ.get(v)), "")
    if passphrase:
        fresh = not os.path.exists(os.path.join(db_path, _SALT_FILE))
        kek = Fernet(_derive_kek(db_path, passphrase))
        wrapped = os.path.join(db_path, _WRAPPED_KEY_FILE)
        plain = os.path.join(db_path, _KEY_FILE)
        if os.path.exists(wrapped):
            with open(wrapped, "rb") as fh:
                return Fernet(kek.decrypt(fh.read()))   # wrong passphrase -> InvalidToken
        if os.path.exists(plain):
            # key-file store gaining a master passphrase: wrap the existing data
            # key so the plaintext copy can be removed (same key, no re-encryption)
            with open(plain, "rb") as fh:
                dk = fh.read()
            with open(wrapped, "wb") as fh:
                fh.write(kek.encrypt(dk))
            _restrict(wrapped)
            os.remove(plain)
            return Fernet(dk)
        if fresh:
            # brand-new store: random data key, stored only in wrapped form
            dk = Fernet.generate_key()
            with open(wrapped, "wb") as fh:
                fh.write(kek.encrypt(dk))
            _restrict(wrapped)
            return Fernet(dk)
        # pre-KEK store (salt exists, no key files): the derived key IS the data
        # key — behavior identical to the old code so existing DBs decrypt
        return Fernet(_derive_kek(db_path, passphrase))
    key = _load_or_create(db_path, _KEY_FILE, Fernet.generate_key)
    return Fernet(key)
```

Add at the end of the file:

```python
def rotate_master(db_path: str, old_passphrase: str, new_passphrase: str) -> bool:
    """Re-wrap a store's data key under a new master passphrase. The data key —
    and therefore every encrypted blob — is untouched. Returns False for stores
    that have no wrapped key (legacy derived / plain key-file stores)."""
    wrapped = os.path.join(db_path, _WRAPPED_KEY_FILE)
    if not (_AVAILABLE and os.path.exists(wrapped)):
        return False
    old_kek = Fernet(_derive_kek(db_path, old_passphrase))
    with open(wrapped, "rb") as fh:
        dk = old_kek.decrypt(fh.read())        # wrong old passphrase -> InvalidToken
    os.remove(os.path.join(db_path, _SALT_FILE))   # fresh salt for the new KEK
    new_kek = Fernet(_derive_kek(db_path, new_passphrase))
    with open(wrapped, "wb") as fh:
        fh.write(new_kek.encrypt(dk))
    _restrict(wrapped)
    return True


def erase_keys(db_path: str) -> int:
    """Crypto-erase: delete the store's key material. Any encrypted blobs left
    behind (including SQLite pages and backups) become permanently unreadable.
    Returns the number of key files removed."""
    removed = 0
    for name in (_SALT_FILE, _KEY_FILE, _WRAPPED_KEY_FILE):
        path = os.path.join(db_path, name)
        if os.path.exists(path):
            os.remove(path)
            removed += 1
    return removed
```

- [x] **Step 4: Run new tests, then the full suite (regression)**

Run: `python -m pytest tests/test_crypto_kek.py -v`
Expected: all PASS.

Run: `python -m pytest tests/test_storage.py tests/test_biometric_core.py -q`
Expected: PASS (stores still open and read).

- [x] **Step 5: Commit**

```bash
git add biometric/core/crypto.py tests/test_crypto_kek.py
git commit -m "feat(trust): KEK-wrapped store data keys + master rotation + crypto-erase"
```

---

### Task 5: Envelope-wrapped store blobs + maintenance CLI

**Files:**
- Modify: `biometric/core/store.py` (constructor + `_serialize`/`_deserialize`)
- Modify: `biometric/profile.py:45-52` (`make_store` passes modality)
- Modify: `face/storage.py:38-47` (`FaceStore.__init__` passes modality)
- Create: `manage_templates.py` (repo root, alongside `manage_keys.py`)
- Test: `tests/test_store_envelope.py`

**Interfaces:**
- Consumes: `envelope.encode/decode/is_envelope` (Task 1), `crypto.erase_keys` (Task 4).
- Produces: `TemplateStore(db_path, ..., modality: str = "face")` — new keyword;
  all stored blobs are now `BE1(kind="raw", data=<FT2 bytes>)`; reads accept
  envelope, FT2, FT1, and legacy JSON. CLI: `python manage_templates.py wrap|erase-keys`.

- [x] **Step 1: Write the failing tests**

Create `tests/test_store_envelope.py`:

```python
"""Store blobs are envelope-wrapped on write; every legacy format still reads."""
import numpy as np

from biometric.core import envelope
from biometric.core.store import BioTemplate, TemplateStore, _pack


def _raw_blob(store, user_id):
    with store._connect() as conn:
        blob = conn.execute("SELECT data FROM templates WHERE user_id=?",
                            (user_id,)).fetchone()[0]
    return store._cipher.decrypt(blob) if store._cipher is not None else blob


def test_write_produces_envelope(tmp_path):
    store = TemplateStore(str(tmp_path), modality="palm")
    store.add_embedding("u1", np.random.rand(8).astype(np.float32))
    raw = _raw_blob(store, "u1")
    assert envelope.is_envelope(raw)
    env = envelope.decode(raw)
    assert env["mod"] == "palm" and env["kind"] == "raw"
    assert env["dim"] == 8 and env["dtype"] == "f32"
    t = store.load("u1")
    assert t is not None and len(t.anchors) == 1


def test_legacy_ft2_blob_still_reads(tmp_path):
    store = TemplateStore(str(tmp_path))
    blob = _pack(BioTemplate(user_id="legacy", anchors=[np.ones(4, np.float32)]))
    if store._cipher is not None:
        blob = store._cipher.encrypt(blob)
    with store._write_lock, store._connect() as conn:
        seq = store._next_seq(conn)
        conn.execute("INSERT INTO templates(user_id, data, seq, deleted) VALUES (?,?,?,0)",
                     ("legacy", blob, seq))
    t = store.load("legacy")
    assert t is not None and t.anchors[0].shape[0] == 4


def test_corrupt_envelope_returns_none(tmp_path):
    store = TemplateStore(str(tmp_path))
    bad = envelope.MAGIC + b"\xff\xff"
    if store._cipher is not None:
        bad = store._cipher.encrypt(bad)
    with store._write_lock, store._connect() as conn:
        seq = store._next_seq(conn)
        conn.execute("INSERT INTO templates(user_id, data, seq, deleted) VALUES (?,?,?,0)",
                     ("bad", bad, seq))
    assert store.load("bad") is None


def test_wrap_command_rewrites_legacy_rows(tmp_path):
    import manage_templates
    store = TemplateStore(str(tmp_path))
    blob = _pack(BioTemplate(user_id="legacy", anchors=[np.ones(4, np.float32)]))
    if store._cipher is not None:
        blob = store._cipher.encrypt(blob)
    with store._write_lock, store._connect() as conn:
        seq = store._next_seq(conn)
        conn.execute("INSERT INTO templates(user_id, data, seq, deleted) VALUES (?,?,?,0)",
                     ("legacy", blob, seq))
    n = manage_templates.wrap_store(str(tmp_path), modality="face", dry_run=False)
    assert n == 1
    assert envelope.is_envelope(_raw_blob(TemplateStore(str(tmp_path)), "legacy"))
```

- [x] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_store_envelope.py -v`
Expected: FAIL — `TemplateStore` has no `modality` kwarg / blobs are bare FT2.

- [x] **Step 3: Modify the store**

In `biometric/core/store.py`:

a) Import the envelope module (after `from .crypto import get_cipher`):

```python
from . import envelope
```

b) Constructor (`__init__`, currently lines 127-139): add the keyword and attribute:

```python
    def __init__(self, db_path: str, samples_per_user: int = 3,
                 adaptive_novelty: float = 0.92, adaptive_max_samples: int = 8,
                 db_file: str = _DEFAULT_DB_FILE, modality: str = "face") -> None:
        self.db_path = db_path
        self.modality = modality
```

(rest of the body unchanged.)

c) `_serialize` (currently lines 172-174) becomes:

```python
    def _serialize(self, tmpl: BioTemplate) -> bytes:
        rows = tmpl.embeddings
        dim = int(rows[0].shape[0]) if rows else 0
        blob = envelope.encode(mod=self.modality, kind="raw", data=_pack(tmpl),
                               dim=dim, dtype="f32")
        return self._cipher.encrypt(blob) if self._cipher is not None else blob
```

d) In `_deserialize`, after the decrypt block and BEFORE the FT2/FT1 magic check, insert:

```python
        if envelope.is_envelope(raw):
            try:
                raw = envelope.decode(raw)["data"]
            except envelope.EnvelopeError:
                return None
```

e) In `biometric/profile.py`, `make_store` (lines 45-52): add `modality=self.name,` to the
`TemplateStore(...)` call.

f) In `face/storage.py`, `FaceStore.__init__` (lines 38-47): add `modality="face",` to the
`super().__init__(...)` call.

- [x] **Step 4: Create the maintenance CLI**

Create `manage_templates.py` (repo root):

```python
"""Template-store maintenance CLI.

  python manage_templates.py wrap --path face_db [--modality face] [--dry-run]
      Re-write any pre-envelope (FT1/FT2/JSON) rows in envelope form. Reads
      already work without this; wrapping is for uniformity before Phase 1.
      Tenant stores live under <db_path>/tenants/<tenant>[/palm].

  python manage_templates.py erase-keys --path face_db/tenants/acme --yes
      CRYPTO-ERASE a store's key material. The encrypted templates left behind
      become permanently unreadable. Run only for offboarded tenants.
"""

from __future__ import annotations

import argparse
import sys

from biometric.core import crypto, envelope
from biometric.core.store import TemplateStore


def wrap_store(path: str, modality: str = "face", dry_run: bool = False,
               db_file: str = "faces.db") -> int:
    store = TemplateStore(path, db_file=db_file, modality=modality)
    with store._connect() as conn:
        rows = conn.execute(
            "SELECT user_id, data FROM templates WHERE deleted=0").fetchall()
    wrapped = 0
    for user_id, blob in rows:
        raw = blob
        if store._cipher is not None:
            try:
                raw = store._cipher.decrypt(raw)
            except Exception:
                print(f"  SKIP {user_id}: cannot decrypt (wrong key?)")
                continue
        if envelope.is_envelope(raw):
            continue
        tmpl = store._deserialize(blob)
        if tmpl is None:
            print(f"  SKIP {user_id}: unreadable blob")
            continue
        if not dry_run:
            store._write(tmpl)
        wrapped += 1
    print(f"{'would wrap' if dry_run else 'wrapped'} {wrapped} of {len(rows)} templates")
    return wrapped


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    w = sub.add_parser("wrap", help="envelope-wrap legacy template rows")
    w.add_argument("--path", required=True, help="store directory (holds the .db)")
    w.add_argument("--modality", default="face", choices=("face", "palm"))
    w.add_argument("--db-file", default="faces.db")
    w.add_argument("--dry-run", action="store_true")

    e = sub.add_parser("erase-keys", help="crypto-erase a store's key material")
    e.add_argument("--path", required=True)
    e.add_argument("--yes", action="store_true",
                   help="confirm: data becomes permanently unreadable")

    args = ap.parse_args(argv)
    if args.cmd == "wrap":
        wrap_store(args.path, modality=args.modality, dry_run=args.dry_run,
                   db_file=args.db_file)
        return 0
    if args.cmd == "erase-keys":
        if not args.yes:
            print("Refusing without --yes (this permanently destroys access to the data).")
            return 2
        removed = crypto.erase_keys(args.path)
        print(f"removed {removed} key file(s); remaining blobs are unrecoverable")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
```

- [x] **Step 5: Run new tests, then regression on storage/sync/index**

Run: `python -m pytest tests/test_store_envelope.py -v`
Expected: all PASS.

Run: `python -m pytest tests/test_storage.py tests/test_index.py tests/test_sync.py tests/test_biometric_core.py tests/test_adaptive_drift.py -q`
Expected: PASS (envelope wrapping is invisible to existing readers).

- [x] **Step 6: Commit**

```bash
git add biometric/core/store.py biometric/profile.py face/storage.py manage_templates.py tests/test_store_envelope.py
git commit -m "feat(trust): envelope-wrap stored templates (back-compat reads) + manage_templates CLI"
```

---

### Task 6: `/v1/tenant/keys` endpoints, offboard hook, OpenAPI

**Files:**
- Modify: `face_service/v1.py` (new endpoints; import `issuer_keys`)
- Modify: `app.py:443-466` (`admin_tenant_offboard` drops issuer keys too)
- Modify: `openapi.yaml` (two new paths)
- Test: `tests/test_issuer_keys_api.py`

**Interfaces:**
- Consumes: `issuer_keys` (Task 3); existing `require_scope`, `audit.log`, `_err`, `g.tenant`, `g.key_name` in `v1.py`.
- Produces: `GET /v1/tenant/keys` (manage scope) → `{success, tenant, keys:[{kid, public_key, created, status, retired_at?}]}`;
  `POST /v1/tenant/keys/rotate` (manage scope, body `{"confirm": true}`) → `{success, active}`.
  Tasks 7–8 (UI, SDK) consume these routes.

- [x] **Step 1: Write the failing tests**

Create `tests/test_issuer_keys_api.py`:

```python
"""/v1/tenant/keys: manage-scope gating, listing, confirmed rotation."""
import pytest


def _h(key):
    return {"X-API-Key": key}


@pytest.fixture(autouse=True)
def isolated_keydir(tmp_path, monkeypatch):
    monkeypatch.setenv("BIO_ISSUER_KEY_DIR", str(tmp_path / "issuer"))


def test_requires_manage_scope(client, make_key):
    vk = make_key("verify", "ik_v")
    assert client.get("/v1/tenant/keys", headers=_h(vk)).status_code == 403
    assert client.post("/v1/tenant/keys/rotate", headers=_h(vk),
                       json={"confirm": True}).status_code == 403
    assert client.get("/v1/tenant/keys").status_code == 401


def test_list_creates_active_key(client, make_key):
    ak = make_key("admin", "ik_a")
    r = client.get("/v1/tenant/keys", headers=_h(ak)).get_json()
    assert r["success"] and r["keys"][0]["status"] == "active"
    assert len(r["keys"][0]["kid"]) == 16


def test_rotate_requires_confirm_and_retires_old(client, make_key):
    ak = make_key("admin", "ik_b")
    kid0 = client.get("/v1/tenant/keys", headers=_h(ak)).get_json()["keys"][0]["kid"]
    assert client.post("/v1/tenant/keys/rotate", headers=_h(ak),
                       json={}).status_code == 400
    rot = client.post("/v1/tenant/keys/rotate", headers=_h(ak),
                      json={"confirm": True}).get_json()
    assert rot["success"] and rot["active"]["kid"] != kid0
    keys = client.get("/v1/tenant/keys", headers=_h(ak)).get_json()["keys"]
    assert keys[0]["kid"] == rot["active"]["kid"]
    assert any(k["kid"] == kid0 and k["status"] == "retired" for k in keys)
```

- [x] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_issuer_keys_api.py -v`
Expected: FAIL — 404 on the new routes.

- [x] **Step 3: Add the endpoints**

In `face_service/v1.py`, extend the package import line that already pulls in `audit`
(near the other `from . import ...` lines) to also import `issuer_keys`, then add
(a sensible location is right before the `purge_tenant` handler):

```python
@bp.get("/tenant/keys")
@require_scope("manage")
def tenant_keys():
    """This tenant's issuer signing keys — the keys the platform signs
    credentials/bundles with on the tenant's behalf. Active key first."""
    return jsonify({"success": True, "tenant": g.tenant or "default",
                    "keys": issuer_keys.public_keys(g.tenant)})


@bp.post("/tenant/keys/rotate")
@require_scope("manage")
def tenant_keys_rotate():
    """Rotate the issuer key. The old public key is retained so previously
    issued signatures keep verifying; only NEW items use the new key."""
    data = request.get_json(silent=True) or {}
    if data.get("confirm") is not True:
        return _err("Set 'confirm': true to rotate the issuer key. Existing "
                    "signatures keep verifying via the retired key.")
    rec = issuer_keys.rotate(g.tenant)
    audit.log(g.tenant, "issuer_key_rotate", actor=g.key_name, success=True,
              detail=f"new kid {rec['kid']}")
    return jsonify({"success": True, "active": rec})
```

- [x] **Step 4: Drop issuer keys on offboarding**

In `app.py`, inside `admin_tenant_offboard` (line 443ff), after the
`tenants.remove(tenant)` line add:

```python
    issuer_removed = issuer_keys.remove(tenant)
```

and extend the audit detail f-string to include `; issuer key removed={issuer_removed}`.
Add `issuer_keys` to the existing `from face_service import ...` import block at the
top of `app.py`.

- [x] **Step 5: Document in OpenAPI**

In `openapi.yaml`, add under `paths:` (match the file's existing indentation and
security-scheme names — copy the style of the `/v1/users` entries):

```yaml
  /v1/tenant/keys:
    get:
      summary: List the tenant's issuer signing keys
      description: >
        Ed25519 keys the platform uses to sign anything issued on this tenant's
        behalf (credentials, bundles). Active key first; retired keys remain
        listed so previously issued signatures can still be verified.
        Requires an admin (manage-scope) API key.
      responses:
        "200":
          description: Key list. Each item has kid, public_key (base64),
            created (unix), status (active|retired), retired_at (unix, retired only).
  /v1/tenant/keys/rotate:
    post:
      summary: Rotate the tenant's issuer signing key
      description: >
        Creates a fresh keypair; the old public key is retained for
        verification only. Requires body {"confirm": true} and an admin
        (manage-scope) API key.
      responses:
        "200":
          description: The new active key (kid, public_key, created, status).
        "400":
          description: Missing confirm flag.
```

- [x] **Step 6: Run tests to verify they pass**

Run: `python -m pytest tests/test_issuer_keys_api.py tests/test_v1_api.py -q`
Expected: PASS (new tests green, existing v1 suite untouched).

- [x] **Step 7: Commit**

```bash
git add face_service/v1.py app.py openapi.yaml tests/test_issuer_keys_api.py
git commit -m "feat(trust): /v1/tenant/keys list+rotate endpoints, offboard drops issuer keys"
```

---

### Task 7: Admin console + tenant portal "Security" panels

**Files:**
- Modify: `app.py` (two admin endpoints, near the other `/admin/api/*` routes)
- Modify: `face_service/portal.py` (two portal endpoints, after the keys section)
- Modify: `templates/admin.html` (new tab + panel; tabs nav is at lines 28-37, panels follow)
- Modify: `templates/portal.html` (new card section inside `#console`)
- Modify: `static/admin.js`, `static/portal.js`

**Interfaces:**
- Consumes: `issuer_keys` (Task 3), `admin.require_admin`, portal `require_tenant`
  (sets `g.portal_tenant`), portal JS `api(path, opts)` helper, `_enabled_or_402()` in portal.py.
- Produces: `GET/POST /admin/api/issuer-keys[/rotate]`, `GET/POST /portal/api/issuer-keys[/rotate]`.

- [x] **Step 1: Admin endpoints**

In `app.py`, near the other `/admin/api/tenants/*` routes, add:

```python
@app.route("/admin/api/issuer-keys", methods=["GET"])
@admin.require_admin
def admin_issuer_keys():
    tenant = (request.args.get("tenant") or "default").strip() or "default"
    return jsonify({"success": True, "tenant": tenant,
                    "keys": issuer_keys.public_keys(tenant)})


@app.route("/admin/api/issuer-keys/rotate", methods=["POST"])
@admin.require_admin
def admin_issuer_keys_rotate():
    data = request.get_json(silent=True) or {}
    tenant = (data.get("tenant") or "default").strip() or "default"
    rec = issuer_keys.rotate(tenant)
    audit.log(tenant, "issuer_key_rotate", actor=g.get("admin_user", "admin"),
              success=True, detail=f"new kid {rec['kid']}")
    return jsonify({"success": True, "tenant": tenant, "active": rec})
```

- [x] **Step 2: Portal endpoints**

In `face_service/portal.py`, after the keys section (`portal_keys_revoke`, line ~168),
add (also add `issuer_keys` and `audit` to the module's `from . import ...` imports if
not present):

```python
# --- issuer signing keys (this tenant's signing identity) -------------------
@portal_bp.get("/portal/api/issuer-keys")
@require_tenant
def portal_issuer_keys():
    return jsonify({"success": True,
                    "keys": issuer_keys.public_keys(g.portal_tenant)})


@portal_bp.post("/portal/api/issuer-keys/rotate")
@require_tenant
def portal_issuer_keys_rotate():
    gate = _enabled_or_402()
    if gate:
        return gate
    rec = issuer_keys.rotate(g.portal_tenant)
    audit.log(g.portal_tenant, "issuer_key_rotate", actor="portal", success=True,
              detail=f"new kid {rec['kid']}")
    return jsonify({"success": True, "active": rec})
```

Note: `_enabled_or_402` is defined at `face_service/portal.py:213` but the issuer-keys
section sits earlier in the file — either place the new routes after it or reference it
lazily; simplest is to add the new section after the invites section at the end.

- [x] **Step 3: Admin UI**

In `templates/admin.html`:

a) In the tabs nav (lines 28-37), after the `data-tab="keys"` button, add:

```html
                <button class="tab" data-tab="security">Security</button>
```

b) After the `#tab-keys` panel div closes, add a sibling panel. **Copy the exact
table/button/input class names used by the adjacent `#tab-keys` panel** so styling
matches; the structure to produce is:

```html
        <div class="panel hidden" id="tab-security">
            <h2>Security &mdash; signing keys</h2>
            <p class="hint">Each tenant has a signing key. Anything the platform issues
            for that tenant (credentials, export bundles) is signed with it, so other
            devices can prove it's genuine. Rotating creates a fresh key; things signed
            with the old key remain verifiable.</p>
            <div class="row">
                <input id="sec-tenant" placeholder="tenant (blank = default)">
                <button id="sec-load" type="button">Load keys</button>
                <button id="sec-rotate" type="button" class="danger">Rotate key&hellip;</button>
            </div>
            <table id="sec-keys">
                <thead><tr><th>Status</th><th>Key ID</th><th>Public key</th><th>Created</th></tr></thead>
                <tbody></tbody>
            </table>
        </div>
```

c) In `static/admin.js`, following the file's existing patterns for fetch + tab wiring,
add:

```javascript
async function secLoadKeys() {
    const tenant = (document.getElementById('sec-tenant').value || '').trim() || 'default';
    const r = await fetch(`/admin/api/issuer-keys?tenant=${encodeURIComponent(tenant)}`);
    const d = await r.json();
    const tb = document.querySelector('#sec-keys tbody');
    tb.innerHTML = '';
    for (const k of (d.keys || [])) {
        const tr = document.createElement('tr');
        tr.innerHTML =
            `<td>${k.status}</td><td><code>${k.kid}</code></td>` +
            `<td><code>${k.public_key}</code></td>` +
            `<td>${new Date(k.created * 1000).toLocaleString()}</td>`;
        tb.appendChild(tr);
    }
}

document.getElementById('sec-load')?.addEventListener('click', secLoadKeys);
document.getElementById('sec-rotate')?.addEventListener('click', async () => {
    const tenant = (document.getElementById('sec-tenant').value || '').trim() || 'default';
    if (!confirm(`Rotate the signing key for "${tenant}"?\n\nExisting signed items stay `
                 + `valid; new items are signed with the new key.`)) return;
    await fetch('/admin/api/issuer-keys/rotate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tenant }),
    });
    secLoadKeys();
});
```

If admin.js loads panel data on tab switch, also call `secLoadKeys()` when the
security tab activates (follow how the keys tab does it).

- [x] **Step 4: Portal UI**

`templates/portal.html` is a single-column card layout inside `<section id="console">`
(no tabs). After the card that lists API keys, add a new card, **copying the adjacent
card's class names**:

```html
        <div class="card" id="portal-security">
            <h2>Security &mdash; your signing key</h2>
            <p class="hint">This is the key used to sign everything issued for your
            organisation. Rotate it if you believe it may have been exposed; anything
            already issued stays verifiable.</p>
            <table id="psec-keys">
                <thead><tr><th>Status</th><th>Key ID</th><th>Created</th></tr></thead>
                <tbody></tbody>
            </table>
            <button id="psec-rotate" type="button" class="danger">Rotate signing key&hellip;</button>
        </div>
```

In `static/portal.js`, using the existing `api(path, opts)` helper (line 4), add and
call `loadIssuerKeys()` from wherever the page loads its other panels
(e.g. next to `loadKeys()`, line 105):

```javascript
async function loadIssuerKeys() {
    const d = await api('/portal/api/issuer-keys');
    const tb = document.querySelector('#psec-keys tbody');
    if (!tb || !d.keys) return;
    tb.innerHTML = '';
    for (const k of d.keys) {
        const tr = document.createElement('tr');
        tr.innerHTML = `<td>${k.status}</td><td><code>${k.kid}</code></td>`
                     + `<td>${new Date(k.created * 1000).toLocaleDateString()}</td>`;
        tb.appendChild(tr);
    }
}

document.getElementById('psec-rotate')?.addEventListener('click', async () => {
    if (!confirm('Rotate your signing key?\n\nEverything already issued stays valid; '
                 + 'new items are signed with the new key.')) return;
    await api('/portal/api/issuer-keys/rotate', { method: 'POST', body: '{}' });
    loadIssuerKeys();
});
```

- [x] **Step 5: Verify by launching the server**

Run: `python serve.py` (or the project's usual launch command), open
`http://localhost:5000/admin` → Security tab: load keys for `default`, rotate, see the
retired row appear. Open `/portal`, log in as a tenant, confirm the signing-key card
renders and rotation works. (Per project preference: launch the server for manual
UI checks — no headless screenshot loops.)

Also run: `python -m pytest tests/test_portal.py tests/test_admin_gate.py -q`
Expected: PASS.

- [x] **Step 6: Commit**

```bash
git add app.py face_service/portal.py templates/admin.html templates/portal.html static/admin.js static/portal.js
git commit -m "feat(trust): Security panels (issuer keys) in admin console and tenant portal"
```

---

### Task 8: SDK methods, docs page, changelog

**Files:**
- Modify: `sdk/python/faceverify.py` (two methods on the client class)
- Modify: `sdk/js/faceverify.js` (two methods on `FaceVerifyClient`)
- Create: `docs/security-keys.md`
- Modify: `README.md` (link the new doc), `CHANGELOG.md` (entry)
- Test: `tests/test_sdk_issuer_keys.py`

**Interfaces:**
- Consumes: `/v1/tenant/keys` routes (Task 6); Python SDK `self._call(method, path, body=None)`;
  JS SDK `this._call(method, path, body)`.
- Produces: Python `client.tenant_keys() -> dict`, `client.rotate_tenant_keys() -> dict`;
  JS `fv.tenantKeys()`, `fv.rotateTenantKeys()`.

- [ ] **Step 1: Write the failing SDK test**

Create `tests/test_sdk_issuer_keys.py`:

```python
"""Python SDK issuer-key methods hit the right endpoints with the right bodies."""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "sdk", "python"))
from faceverify import FaceVerify  # noqa: E402


def test_sdk_methods_call_expected_paths(monkeypatch):
    calls = []
    client = FaceVerify("https://example.test", api_key="fk_x")

    def fake_call(method, path, body=None):
        calls.append((method, path, body))
        return {"success": True}

    monkeypatch.setattr(client, "_call", fake_call)
    client.tenant_keys()
    client.rotate_tenant_keys()
    assert calls[0] == ("GET", "/v1/tenant/keys", None)
    assert calls[1] == ("POST", "/v1/tenant/keys/rotate", {"confirm": True})
```

Note: check the actual client class name at the top of `sdk/python/faceverify.py`
(`FaceVerify` per its README usage) and the `_call` signature at line 62 — adjust the
test import/signature to match exactly what the file defines.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_sdk_issuer_keys.py -v`
Expected: FAIL — `AttributeError: tenant_keys`.

- [ ] **Step 3: Add the SDK methods**

In `sdk/python/faceverify.py`, after `purge_tenant` (line ~168):

```python
    def tenant_keys(self) -> dict:
        """List this tenant's issuer signing keys (active first). Admin key required."""
        return self._call("GET", "/v1/tenant/keys")

    def rotate_tenant_keys(self) -> dict:
        """Rotate the issuer signing key. Previously signed items stay verifiable."""
        return self._call("POST", "/v1/tenant/keys/rotate", {"confirm": True})
```

In `sdk/js/faceverify.js`, inside `FaceVerifyClient`:

```javascript
  /** List this tenant's issuer signing keys (active first). Admin key required. */
  tenantKeys() {
    return this._call("GET", "/v1/tenant/keys");
  }

  /** Rotate the issuer signing key. Previously signed items stay verifiable. */
  rotateTenantKeys() {
    return this._call("POST", "/v1/tenant/keys/rotate", { confirm: true });
  }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_sdk_issuer_keys.py -v`
Expected: PASS.

- [ ] **Step 5: Write the docs page**

Create `docs/security-keys.md`:

```markdown
# Security foundations: signing keys, template envelopes, encryption

Plain-language guide to the security plumbing added in Trust Platform Phase 0.
Audience: a semi-technical admin or integrator.

## What's protecting your data

1. **Encryption at rest.** Every stored template is encrypted. Each tenant's
   store has its own data key; if you set a master passphrase (`BIO_DB_KEY`),
   data keys are stored *wrapped* (encrypted by a key derived from the
   passphrase), never in plain text.
2. **Signing keys.** Each tenant has an Ed25519 signing keypair. Everything the
   platform issues for that tenant (credentials, export bundles) is signed, so
   any device can check it is genuine and untampered.
3. **Template envelopes.** Every template travels in a versioned, validated
   container, so a corrupted or tampered payload is rejected instead of parsed.

## Everyday operations

### See or rotate a signing key
- **Admin console** -> Security tab -> enter tenant -> Load keys / Rotate.
- **Tenant portal** -> "Security - your signing key" card -> Rotate.
- **API**: `GET /v1/tenant/keys`, `POST /v1/tenant/keys/rotate` (admin key):

      curl -H "X-API-Key: $ADMIN_KEY" https://your-host/v1/tenant/keys
      curl -X POST -H "X-API-Key: $ADMIN_KEY" -H "Content-Type: application/json" \
           -d '{"confirm": true}' https://your-host/v1/tenant/keys/rotate

- **SDK (Python)**: `client.tenant_keys()`, `client.rotate_tenant_keys()`
- **SDK (JS)**: `fv.tenantKeys()`, `fv.rotateTenantKeys()`

Rotation is safe: items signed with the old key remain verifiable; only new
items use the new key. Rotate immediately if you suspect exposure.

### Rotate the master passphrase (per store)

    python -c "from biometric.core import crypto; print(crypto.rotate_master('face_db/tenants/acme', 'OLD', 'NEW'))"

Only the wrapped key is re-encrypted — templates are untouched. Stores created
before wrapped keys existed return False (they derive keys directly; migrate by
re-enrolling or leave as-is).

### Crypto-erase (offboarding)
Offboarding a tenant from the admin console already deletes its store directory
including key material. For a store outside that flow:

    python manage_templates.py erase-keys --path face_db/tenants/acme --yes

After this the encrypted data (and any backups of it) is permanently unreadable.

### Wrap legacy templates into envelopes

    python manage_templates.py wrap --path face_db --dry-run
    python manage_templates.py wrap --path face_db

Optional (reads work either way); recommended before Phase 1.

## What this does NOT yet do
Protected (cancelable) templates, portable credentials, and the public trust
page arrive in Phases 1, 2, and 4 of the trust platform
(`docs/superpowers/specs/2026-07-04-trust-platform-design.md`).
```

- [ ] **Step 6: Link it**

In `README.md`, where other docs are linked, add a line:

```markdown
- [Security foundations: signing keys & encryption](docs/security-keys.md)
```

In `CHANGELOG.md`, add under a new unreleased/dated heading (match the file's style):

```markdown
- Trust Platform Phase 0: per-tenant Ed25519 issuer keys (list/rotate on admin
  console, portal, API, SDKs), versioned template envelopes, KEK-wrapped store
  data keys with master rotation and crypto-erase, `manage_templates.py` CLI.
```

- [ ] **Step 7: Full suite + final verification**

Run: `python -m pytest tests/ -q`
Expected: everything passes (model-dependent tests may skip).

M0 demo check (from spec §13): admin rotates a tenant issuer key in the console;
`GET /v1/tenant/keys` shows active + retired; a fresh tenant store contains
`.key.wrapped`; `manage_templates.py wrap --dry-run` reports zero unwrapped rows on a
new store.

- [ ] **Step 8: Commit**

```bash
git add sdk/python/faceverify.py sdk/js/faceverify.js docs/security-keys.md README.md CHANGELOG.md tests/test_sdk_issuer_keys.py
git commit -m "feat(trust): SDK issuer-key methods + security foundations docs"
```

---

## After Phase 0

Phase 1 (protected templates) gets its own plan once M0 is demoed — its projection-seed
design consumes `issuer_keys`, `envelope`, and the KEK machinery exactly as named in the
Interfaces blocks above. Do not start Phase 1 tasks from this document.
