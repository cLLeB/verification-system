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


# --- issuance (shared by /v1, admin console, tenant portal) --------------------
QR_MAX_CHARS = 2600               # ~QR version 33 at ECC-M alphanumeric
CRED_MAX_DIM = 512                # per-modality template budget (512 int8 bytes)


class IssueError(ValueError):
    """Credential could not be issued. ``code`` is the typed reason."""

    def __init__(self, code: str, message: str, detail=None) -> None:
        super().__init__(message)
        self.code = code
        self.detail = detail


def qr_png_b64(text: str) -> str:
    import cv2
    import numpy as np
    import qrcode
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=4)
    qr.add_data(text)
    qr.make(fit=True)
    mat = np.array(qr.get_matrix(), dtype=np.uint8)
    img = np.kron((1 - mat) * 255, np.ones((8, 8), np.uint8))
    ok, png = cv2.imencode(".png", img)
    if not ok:
        raise IssueError("qr_failed", "QR PNG encoding failed")
    return base64.b64encode(png.tobytes()).decode("ascii")


def issue(tenant: Optional[str], face_cfg, palm_enabled: bool, user_id: str,
          modalities=None, expiry_days: int = 365,
          name: Optional[str] = None, attrs: Optional[dict] = None) -> dict:
    """Issue a signed credential for an enrolled user: one protected+quantized
    centroid per available modality, signed with the tenant's issuer key,
    recorded in the registry. Raises ``IssueError`` (typed) on failure."""
    import numpy as np
    from biometric.core import credential as _cred
    from . import issuer_keys, modality as _modality

    if not 1 <= int(expiry_days) <= 3650:
        raise IssueError("bad_request", "'expiry_days' must be between 1 and 3650.")
    requested = list(modalities or ["face", "palm"])
    cid = _cred.new_cid()
    templates, mods, skipped = [], [], []
    for m in ("face", "palm") if palm_enabled else ("face",):
        if m not in requested:
            continue
        store, _idx, _thr = _modality.store_and_index(face_cfg, m)
        tmpl = store.load_raw(user_id)
        if tmpl is None or not tmpl.anchors:
            skipped.append({"modality": m, "reason": "not_enrolled"})
            continue
        vec = np.mean(np.stack(tmpl.anchors).astype(np.float32), axis=0)
        n = float(np.linalg.norm(vec))
        if n <= 0:
            skipped.append({"modality": m, "reason": "not_enrolled"})
            continue
        vec /= n
        if vec.shape[0] > CRED_MAX_DIM:
            # spec 6.4: a modality whose vector exceeds the QR budget stays
            # store-based; the credential ships without it.
            skipped.append({"modality": m, "reason": "template_too_large"})
            continue
        templates.append(_cred.template_envelope(cid, m, vec))
        mods.append(m)
    if not templates:
        raise IssueError("not_enrolled",
                         f"'{user_id}' has no enrolment a credential can carry.",
                         detail=skipped)

    t = _norm(tenant)
    kid = issuer_keys.get_or_create(t)["kid"]
    payload = _cred.build(cid, t, kid, user_id, templates, mods,
                          expiry_days=int(expiry_days),
                          name=(name or "").strip() or None, attrs=attrs or None)
    _, sig = issuer_keys.sign_for(t, _cred.signing_bytes(payload))
    text = _cred.encode(payload, sig)
    if len(text) > QR_MAX_CHARS:
        raise IssueError("too_large", "Credential payload too large for a QR.")

    record(t, cid.hex(), user_id, mods, payload["iat"], payload["exp"],
           name=payload.get("name"))
    return {"credential_id": cid.hex(), "payload_b45": text,
            "qr_png_b64": qr_png_b64(text), "modalities": mods, "skipped": skipped,
            "issued_at": payload["iat"], "expires": payload["exp"]}


# --- revocation list for offline verifiers ------------------------------------
def _bloom_params(n: int, fpr: float = _BLOOM_FPR) -> tuple:
    m = max(64, int(-n * math.log(fpr) / (math.log(2) ** 2)))
    k = max(1, round(m / n * math.log(2)))
    return m, k


def _bloom_positions(cid_hex: str, m: int, k: int):
    # Double hashing in EXPLICIT 64-bit wrapped arithmetic, so verifiers on any
    # platform (Kotlin ULong, uint64_t, ...) reproduce identical positions.
    mask = (1 << 64) - 1
    digest = hashlib.sha256(bytes.fromhex(cid_hex)).digest()
    h1 = int.from_bytes(digest[:8], "big")
    h2 = int.from_bytes(digest[8:16], "big") | 1
    for i in range(k):
        yield ((h1 + i * h2) & mask) % m


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
