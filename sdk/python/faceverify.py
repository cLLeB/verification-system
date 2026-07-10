"""Biometric Verification Backbone — Python SDK (zero dependencies, stdlib only).

Face AND palm in one API: the server AUTO-DETECTS whether each image is a face or a
palm and routes it — you never declare the modality. A user can enrol either or both
under one ``user_id``; presenting either one verifies them ("a match is a match").

    from faceverify import FaceVerifyClient
    fv = FaceVerifyClient("https://your-host:5000", "fk_yourkey")

    # Managed — image can be a face or a palm; auto-detected.
    fv.enroll("alice", ["a1.jpg", "a2.jpg", "a3.jpg"])   # faces
    fv.enroll("alice", ["palm1.jpg"])                    # ...also her palm, same id
    r = fv.verify("alice", "probe.jpg")                  # face OR palm
    if r["success"]: ...                      # granted (r["modality"] tells you which)

    # Stateless (you keep the data)
    vec = fv.embed("face.jpg")["embedding"]   # store this 512-d vector yourself
    r = fv.compare("probe.jpg", references=[{"embedding": vec}])
    if r["match"] and fv.verify_signature(r): ...

Images may be a file path, raw bytes, or an already-base64 string.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import ssl
import urllib.parse
import urllib.request
from typing import List, Optional, Union

Image = Union[str, bytes]


def _to_b64(image: Image) -> str:
    if isinstance(image, bytes):
        return base64.b64encode(image).decode("ascii")
    if isinstance(image, str) and os.path.exists(image):
        with open(image, "rb") as fh:
            return base64.b64encode(fh.read()).decode("ascii")
    return image  # assume already base64 / data-URL


class FaceVerifyClient:
    def __init__(self, base_url: str, api_key: str, *, verify_tls: bool = True,
                 signing_secret: Optional[str] = None, timeout: int = 30, retries: int = 2):
        self.base = base_url.rstrip("/")
        self.api_key = api_key
        self.signing_secret = signing_secret
        self.timeout = timeout
        self.retries = retries
        self._ctx = None
        if not verify_tls:
            self._ctx = ssl.create_default_context()
            self._ctx.check_hostname = False
            self._ctx.verify_mode = ssl.CERT_NONE

    # --- transport ---------------------------------------------------------
    def _call(self, method: str, path: str, body: Optional[dict] = None) -> dict:
        import time
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            self.base + path, data=data, method=method,
            headers={"Content-Type": "application/json", "X-API-Key": self.api_key})
        last = None
        for attempt in range(self.retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout, context=self._ctx) as r:
                    return json.loads(r.read())
            except urllib.error.HTTPError as e:
                # Retry only transient server/rate-limit statuses; return others as-is.
                if e.code in (429, 502, 503, 504) and attempt < self.retries:
                    time.sleep(0.4 * (2 ** attempt)); continue
                try:
                    return json.loads(e.read())
                except Exception:
                    return {"success": False, "code": "http_error", "message": str(e)}
            except (urllib.error.URLError, TimeoutError) as e:   # network blip
                last = e
                if attempt < self.retries:
                    time.sleep(0.4 * (2 ** attempt)); continue
                return {"success": False, "code": "network_error", "message": str(last)}

    @staticmethod
    def _ref(item) -> dict:
        if isinstance(item, dict):
            return item
        return {"image": _to_b64(item)}

    # --- stateless ---------------------------------------------------------
    def embed(self, image: Image, modality: Optional[str] = None) -> dict:
        body = {"image": _to_b64(image)}
        if modality:
            body["modality"] = modality
        return self._call("POST", "/v1/embed", body)

    def compare(self, probe: Union[Image, dict], references: List, threshold: Optional[float] = None) -> dict:
        body = {"probe": self._ref(probe), "references": [self._ref(r) for r in references]}
        if threshold is not None:
            body["threshold"] = threshold
        return self._call("POST", "/v1/compare", body)

    # --- managed -----------------------------------------------------------
    def enroll(self, user_id: str, images: Union[Image, List[Image]],
               modality: Optional[str] = None, hand: Optional[str] = None) -> dict:
        """Enrol a user from one or more images. By default the server AUTO-DETECTS
        whether each image is a face or a palm and routes it accordingly — the same
        ``user_id`` can hold both. Pass ``modality='face'|'palm'`` only to pin it
        (e.g. enrolling a combined photo as just one modality).

        Palm supports BOTH hands under one id (present either to verify). Uploading
        several palm images at once auto-binds up to two hands with no round-trip —
        ideal for bulk/dataset upload. ``hand``:
          * ``'any'`` — bind a second hand automatically (default for multi-image);
          * ``'other'`` — confirm a second hand after a ``different_hand`` prompt;
          * ``'auto'`` — one-at-a-time UI: a non-matching hand returns
            ``code='different_hand'`` for you to confirm (default for a single image).
        A third distinct hand is refused (``code='hands_full'``)."""
        imgs = images if isinstance(images, list) else [images]
        body = {"user_id": user_id, "images": [_to_b64(i) for i in imgs]}
        if modality:
            body["modality"] = modality
        if hand:
            body["hand"] = hand
        return self._call("POST", "/v1/enroll", body)

    def enroll_bulk(self, people: List[dict]) -> dict:
        """Enrol many at once. Each entry: {"user_id", "images":[...]} or
        {"user_id", "embeddings":[[...]]}. Images are base64-encoded for you."""
        out = []
        for p in people:
            entry = {"user_id": p["user_id"]}
            if p.get("images"):
                entry["images"] = [_to_b64(i) for i in p["images"]]
            if p.get("embeddings"):
                entry["embeddings"] = p["embeddings"]
            out.append(entry)
        return self._call("POST", "/v1/enroll/bulk", {"people": out})

    def verify(self, user_id: str, image: Image, modality: Optional[str] = None) -> dict:
        """1:1 verify. The server auto-detects face vs palm in ``image``; whichever
        the user enrolled with (or either, if both) confirms them. ``modality`` pins it."""
        body = {"user_id": user_id, "image": _to_b64(image)}
        if modality:
            body["modality"] = modality
        return self._call("POST", "/v1/verify", body)

    def identify(self, image: Image, modality: Optional[str] = None) -> dict:
        """1:N identify. Auto-detects face vs palm; returns the matched ``user_id``
        and ``modality``. A match is a match either way."""
        body = {"image": _to_b64(image)}
        if modality:
            body["modality"] = modality
        return self._call("POST", "/v1/identify", body)

    def verify_live(self, frames: List[Image], token: str, user_id: str = "") -> dict:
        body = {"frames": [_to_b64(f) for f in frames], "token": token}
        if user_id:
            body["user_id"] = user_id
        return self._call("POST", "/v1/verify", body)

    def challenge(self) -> dict:
        return self._call("GET", "/v1/challenge")

    def users(self) -> dict:
        return self._call("GET", "/v1/users")

    def delete_user(self, user_id: Union[str, List[str]]) -> dict:
        if isinstance(user_id, list):
            return self._call("POST", "/v1/users/delete", {"user_ids": user_id})
        return self._call("POST", "/v1/users/delete", {"user_id": user_id})

    def export_user(self, user_id: str) -> dict:
        return self._call("POST", "/v1/users/export", {"user_id": user_id})

    def purge_tenant(self) -> dict:
        """Erase ALL users in this tenant (right-to-erasure). Irreversible."""
        return self._call("POST", "/v1/users/purge", {"confirm": True})

    def template_status(self, user_id: Optional[str] = None) -> dict:
        """Template-protection status per modality, or one user's detail.
        Stored templates live in a scrambled, revocable domain; this reports
        the scheme, protection epoch, and last reissue. Admin key required."""
        path = "/v1/templates/status"
        if user_id:
            path += "?user_id=" + urllib.parse.quote(user_id)
        return self._call("GET", path)

    def reissue_templates(self, user_id: Optional[str] = None) -> dict:
        """Re-protect stored templates in a NEW domain (cancelable biometrics):
        previously exported or stolen copies stop matching, enrolled people keep
        verifying with no recapture. Omit user_id to reissue the whole tenant.
        Admin key required."""
        body = {"confirm": True}
        if user_id:
            body["user_id"] = user_id
        return self._call("POST", "/v1/templates/reissue", body)

    # --- portable offline credentials (signed QR) ---------------------------
    def issue_credential(self, user_id: str, modalities=None, expiry_days: int = 365,
                         name: Optional[str] = None, attrs: Optional[dict] = None) -> dict:
        """Issue a signed QR credential for an enrolled user. Returns
        {credential_id, payload_b45, qr_png_b64, expires}. Admin key required."""
        body = {"user_id": user_id, "expiry_days": expiry_days}
        if modalities:
            body["modalities"] = list(modalities)
        if name:
            body["name"] = name
        if attrs:
            body["attrs"] = dict(attrs)
        return self._call("POST", "/v1/credentials", body)

    def list_credentials(self, user_id: Optional[str] = None) -> dict:
        path = "/v1/credentials"
        if user_id:
            path += "?user_id=" + urllib.parse.quote(user_id)
        return self._call("GET", path)

    def revoke_credential(self, credential_id: str) -> dict:
        """Revoke an issued credential; offline verifiers pick it up on their
        next trust-store refresh. Admin key required."""
        return self._call("DELETE", f"/v1/credentials/{credential_id}")

    def verify_credential(self, credential: str, image: Optional[Image] = None,
                          embedding=None) -> dict:
        """Hosted check of a scanned credential (the FV1: string) against a live
        capture: signature -> expiry -> revocation -> biometric match."""
        body = {"credential": credential}
        if image is not None:
            body["image"] = _to_b64(image)
        if embedding is not None:
            body["embedding"] = list(embedding)
        return self._call("POST", "/v1/credentials/verify", body)

    def glance_index(self, modality: str = "face") -> dict:
        """The on-device 1:N glance index (int8 protection-domain vectors +
        calibrated threshold). Admin key + allow_export entitlement required."""
        return self._call("GET", f"/v1/sync/index?modality={modality}")

    def export_glance_index(self, passphrase: str, modality: str = "face") -> dict:
        """The glance index as a passphrase-encrypted file payload for
        air-gapped devices."""
        return self._call("POST", "/v1/export/glance-index",
                          {"passphrase": passphrase, "modality": modality})

    def trust_store(self) -> dict:
        """The public signed bundle of issuer keys + revocation lists."""
        return self._call("GET", "/v1/trust-store")

    def trusted_issuers(self) -> dict:
        return self._call("GET", "/v1/trust")

    def trust_issuer(self, tenant: str) -> dict:
        """Accept credentials issued by another tenant (cross-org verification —
        no data import needed). Admin key required."""
        return self._call("POST", f"/v1/trust/{tenant}")

    def untrust_issuer(self, tenant: str) -> dict:
        return self._call("DELETE", f"/v1/trust/{tenant}")

    def tenant_keys(self) -> dict:
        """List this tenant's issuer signing keys (active first). Admin key required."""
        return self._call("GET", "/v1/tenant/keys")

    def rotate_tenant_keys(self) -> dict:
        """Rotate the issuer signing key. Previously signed items stay verifiable."""
        return self._call("POST", "/v1/tenant/keys/rotate", {"confirm": True})

    def usage(self) -> dict:
        return self._call("GET", "/v1/usage")

    def health(self) -> dict:
        return self._call("GET", "/v1/health")

    # --- trust the result --------------------------------------------------
    def verify_signature(self, payload: dict) -> bool:
        """Verify the HMAC signature on a verify/compare response (needs signing_secret)."""
        sig = payload.get("signature")
        if not sig or not self.signing_secret:
            return False
        body = json.dumps({k: payload.get(k) for k in ("success", "match", "user_id", "score", "best_score")},
                          sort_keys=True, separators=(",", ":"))
        msg = f"{sig['ts']}.{sig['nonce']}.{body}".encode()
        expect = hmac.new(self.signing_secret.encode(), msg, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expect, sig.get("hmac", ""))
