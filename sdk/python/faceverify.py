"""Face Verification Backbone — Python SDK (zero dependencies, stdlib only).

    from faceverify import FaceVerifyClient
    fv = FaceVerifyClient("https://your-host:5000", "fk_yourkey")

    # Managed
    fv.enroll("alice", ["a1.jpg", "a2.jpg", "a3.jpg"])
    r = fv.verify("alice", "probe.jpg")
    if r["success"]: ...                      # granted

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
    def embed(self, image: Image) -> dict:
        return self._call("POST", "/v1/embed", {"image": _to_b64(image)})

    def compare(self, probe: Union[Image, dict], references: List, threshold: Optional[float] = None) -> dict:
        body = {"probe": self._ref(probe), "references": [self._ref(r) for r in references]}
        if threshold is not None:
            body["threshold"] = threshold
        return self._call("POST", "/v1/compare", body)

    # --- managed -----------------------------------------------------------
    def enroll(self, user_id: str, images: Union[Image, List[Image]]) -> dict:
        imgs = images if isinstance(images, list) else [images]
        return self._call("POST", "/v1/enroll", {"user_id": user_id, "images": [_to_b64(i) for i in imgs]})

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

    def verify(self, user_id: str, image: Image) -> dict:
        return self._call("POST", "/v1/verify", {"user_id": user_id, "image": _to_b64(image)})

    def identify(self, image: Image) -> dict:
        return self._call("POST", "/v1/identify", {"image": _to_b64(image)})

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
