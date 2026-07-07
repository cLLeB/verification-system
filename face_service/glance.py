"""On-device 1:N "glance" index (trust platform Phase 3, spec 7).

One compact, protection-domain vector per enrolled person — int8-quantized, so
100k identities ≈ 50 MB — shipped to devices for sub-second offline
identification by brute-force dot product (measured well under 200 ms on a
mid-range phone; ANN only if real scale ever demands it).

Design points:
  * Rows are the per-user CENTROID of the protected (store-domain) anchors —
    the projection is linear, so this equals projecting the raw centroid.
  * Individually reissued users live in their own domain and are EXCLUDED
    (the store-domain probe cannot score them); a reissue-all folds them back.
  * The 1:N operating point is calibrated SEPARATELY from 1:1 (lesson from the
    palm threshold incidents): the impostor distribution over the index rows
    sets the threshold at a target FAR, clamped to [floor, floor+band] both
    here and on the device — and a margin gate (top vs runner-up) rides along.
    Mirror any change to GLANCE_* into Android Config.kt.
"""

from __future__ import annotations

import base64
import time
from typing import Optional

import numpy as np

from biometric import calibrate as _calibrate

PAYLOAD_FORMAT = "faceverify-glance-index"
VERSION = 1

# 1:N floors per modality — ABOVE the 1:1 thresholds (0.40 face / 0.65 palm):
# at 100k identities the impostor MAX creeps up, and glance has no liveness.
# Mirrored in Android Config.kt (GLANCE_*) — keep in sync.
GLANCE_FLOORS = {"face": 0.45, "palm": 0.68}
GLANCE_CLAMP_BAND = 0.12          # calibration may only tighten: [floor, floor+band]
GLANCE_MARGIN = 0.08              # top must beat the runner-up by this
GLANCE_TARGET_FAR = 0.005


def build_payload(tenant: str, store, modality: str) -> dict:
    """The glance-index payload for one tenant store: per-user int8 centroids in
    the store's protection domain + the calibrated 1:N operating point."""
    users, rows = [], []
    off_domain = {u for u, _ in store.off_domain_users()}
    for t in store.iter_templates():                    # matching-domain vectors
        if not t.embeddings or t.user_id in off_domain:
            continue
        c = np.mean(np.stack(t.embeddings).astype(np.float32), axis=0)
        n = float(np.linalg.norm(c))
        if n <= 0:
            continue
        users.append(t.user_id)
        rows.append(c / n)

    floor = GLANCE_FLOORS.get(modality, 0.5)
    threshold = floor
    if rows:
        rec = _calibrate.recommend_threshold(
            ((u, [r]) for u, r in zip(users, rows)),
            target_far=GLANCE_TARGET_FAR, lo=floor, hi=floor + GLANCE_CLAMP_BAND)
        if rec is not None:
            threshold = float(rec["threshold"])

    dim = int(rows[0].shape[0]) if rows else 0
    scales = np.empty(len(rows), np.float32)
    data = np.empty((len(rows), dim), np.int8)
    for i, r in enumerate(rows):
        s = float(np.max(np.abs(r))) or 1.0
        scales[i] = s
        data[i] = np.clip(np.rint(r * (127.0 / s)), -127, 127).astype(np.int8)

    payload = {"format": PAYLOAD_FORMAT, "version": VERSION,
               "tenant": tenant or "default", "modality": modality,
               "dim": dim, "count": len(users), "users": users,
               "scales": base64.b64encode(scales.tobytes()).decode("ascii"),
               "data": base64.b64encode(data.tobytes()).decode("ascii"),
               "threshold": round(threshold, 4), "margin": GLANCE_MARGIN,
               "floor": floor, "clamp_band": GLANCE_CLAMP_BAND,
               "seq": store.current_seq(), "generated": int(time.time()),
               "skipped_off_domain": len(off_domain)}
    if store.protection_enabled:
        from biometric.core import protect as _protect
        ref, seed = store.domain_seed()
        payload["protection"] = {"scheme": _protect.SCHEME, "seedref": ref,
                                 "seed": base64.b64encode(seed).decode("ascii"),
                                 "epoch": store.store_epoch()}
    return payload


def search(payload: dict, probe: np.ndarray, top_k: int = 5) -> list:
    """Reference implementation of the device search (used by tests and the
    server-side parity path): int8 rows x f32 probe, per-user scores."""
    count, dim = int(payload["count"]), int(payload["dim"])
    if not count:
        return []
    scales = np.frombuffer(base64.b64decode(payload["scales"]), np.float32)
    rows = np.frombuffer(base64.b64decode(payload["data"]), np.int8).reshape(count, dim)
    sims = (rows.astype(np.float32) @ np.asarray(probe, np.float32)) * (scales / 127.0)
    order = np.argsort(-sims)[:top_k]
    return [(payload["users"][i], float(sims[i])) for i in order]


def decide(hits: list, threshold: float, margin: float) -> Optional[tuple]:
    """Margin-checked 1:N decision: (user_id, score) or None."""
    if not hits:
        return None
    top_id, top = hits[0]
    second = hits[1][1] if len(hits) > 1 else -1.0
    if top >= threshold and (len(hits) == 1 or top - second >= margin):
        return top_id, top
    return None
