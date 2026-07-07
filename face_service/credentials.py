"""Issued-credential registry + revocation lists (trust platform Phase 2).

Tracks WHAT was issued (metadata only — the QR itself is regenerable from the
store's raw template and is never persisted) and which credentials are revoked.
Revocation ships to offline verifiers inside the trust store: an exact cid list
while small, a Bloom filter (no false negatives; verifiers fail closed on a
positive) once large. Reissuing ONE user's templates (spec 5.3) auto-revokes
their credentials — the leak that motivated the reissue taints them too.

Registry file lives in ``BIO_CREDENTIALS_DIR`` (default ``secrets/credentials``),
read at call time so tests and deploys can repoint it via the environment.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import threading
import time
from typing import List, Optional

_FILE = "credentials.json"
_lock = threading.Lock()

EXACT_LIMIT = 100                 # revocations shipped as an exact list up to here
_BLOOM_FPR = 0.005                # target false-positive rate for the bloom form


def _dir() -> str:
    return os.environ.get("BIO_CREDENTIALS_DIR", os.path.join("secrets", "credentials"))


def _norm(tenant: Optional[str]) -> str:
    return (tenant or "default").strip() or "default"


def _load() -> dict:
    path = os.path.join(_dir(), _FILE)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _save(data: dict) -> None:
    os.makedirs(_dir(), exist_ok=True)
    path = os.path.join(_dir(), _FILE)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


# --- registry -----------------------------------------------------------------
def record(tenant: Optional[str], cid_hex: str, user_id: str, modalities: List[str],
           iat: int, exp: int, name: Optional[str] = None) -> dict:
    t = _norm(tenant)
    meta = {"cid": cid_hex, "user_id": user_id, "modalities": list(modalities),
            "iat": int(iat), "exp": int(exp), "revoked": False}
    if name:
        meta["name"] = name
    with _lock:
        data = _load()
        data.setdefault(t, {})[cid_hex] = meta
        _save(data)
    return meta


def list_for(tenant: Optional[str], user_id: Optional[str] = None) -> List[dict]:
    recs = (_load().get(_norm(tenant)) or {}).values()
    out = [dict(r) for r in recs if user_id is None or r.get("user_id") == user_id]
    return sorted(out, key=lambda r: -r["iat"])


def get(tenant: Optional[str], cid_hex: str) -> Optional[dict]:
    rec = (_load().get(_norm(tenant)) or {}).get(cid_hex)
    return dict(rec) if rec else None


def revoke(tenant: Optional[str], cid_hex: str) -> bool:
    with _lock:
        data = _load()
        rec = (data.get(_norm(tenant)) or {}).get(cid_hex)
        if rec is None:
            return False
        already = rec.get("revoked", False)
        rec["revoked"] = True
        rec.setdefault("revoked_at", int(time.time()))
        _save(data)
        return not already


def revoke_for_user(tenant: Optional[str], user_id: str) -> int:
    """Auto-revocation hook for per-user template reissue (spec 5.3/6.5)."""
    n = 0
    with _lock:
        data = _load()
        for rec in (data.get(_norm(tenant)) or {}).values():
            if rec.get("user_id") == user_id and not rec.get("revoked"):
                rec["revoked"] = True
                rec["revoked_at"] = int(time.time())
                n += 1
        if n:
            _save(data)
    return n


def revoked_cids(tenant: Optional[str]) -> List[str]:
    return sorted(r["cid"] for r in (_load().get(_norm(tenant)) or {}).values()
                  if r.get("revoked"))


def is_revoked(tenant: Optional[str], cid_hex: str) -> bool:
    rec = (_load().get(_norm(tenant)) or {}).get(cid_hex)
    return bool(rec and rec.get("revoked"))


def remove_tenant(tenant: Optional[str]) -> bool:
    """Offboarding: drop the tenant's credential registry."""
    t = _norm(tenant)
    with _lock:
        data = _load()
        if t not in data:
            return False
        del data[t]
        _save(data)
        return True


# --- revocation list for offline verifiers ------------------------------------
def _bloom_params(n: int, fpr: float = _BLOOM_FPR) -> tuple:
    m = max(64, int(-n * math.log(fpr) / (math.log(2) ** 2)))
    k = max(1, round(m / n * math.log(2)))
    return m, k


def _bloom_positions(cid_hex: str, m: int, k: int):
    digest = hashlib.sha256(bytes.fromhex(cid_hex)).digest()
    h1 = int.from_bytes(digest[:8], "big")
    h2 = int.from_bytes(digest[8:16], "big") | 1
    for i in range(k):
        yield (h1 + i * h2) % m


def build_revocation_list(tenant: Optional[str]) -> dict:
    """The compact, versioned revocation object shipped inside the trust store.
    ``{version, count, exact:[cid...]}`` while small; ``{version, count, bloom:
    {m, k, bits}}`` once large (no false negatives — a bloom hit means REVOKED,
    fail closed)."""
    cids = revoked_cids(tenant)
    out = {"version": int(time.time()), "count": len(cids)}
    if len(cids) <= EXACT_LIMIT:
        out["exact"] = cids
        return out
    m, k = _bloom_params(len(cids))
    bits = bytearray((m + 7) // 8)
    for cid in cids:
        for pos in _bloom_positions(cid, m, k):
            bits[pos // 8] |= 1 << (pos % 8)
    out["bloom"] = {"m": m, "k": k,
                    "bits": base64.b64encode(bytes(bits)).decode("ascii")}
    return out


def check_revoked(rev: dict, cid_hex: str) -> bool:
    """Verifier-side check against a revocation object (exact or bloom form)."""
    if not rev:
        return False
    if "exact" in rev:
        return cid_hex in rev["exact"]
    bloom = rev.get("bloom") or {}
    try:
        m, k = int(bloom["m"]), int(bloom["k"])
        bits = base64.b64decode(bloom["bits"])
    except (KeyError, ValueError, TypeError):
        return True                                   # malformed data: fail closed
    return all(bits[pos // 8] & (1 << (pos % 8))
               for pos in _bloom_positions(cid_hex, m, k))
