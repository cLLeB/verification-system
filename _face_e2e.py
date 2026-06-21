"""End-to-end proof of the face engine: enroll / verify / identify.

Uses three distinct faces cropped from the bundled group photo as separate
identities. Pose gate is relaxed here so every crop yields an embedding and we
can see the real DENY similarity scores (production keeps the strict gate).
"""
from __future__ import annotations
import dataclasses, tempfile
import cv2, numpy as np
import insightface
from insightface.app import FaceAnalysis

import face.engine as fe
from face import api, CONFIG
from face.storage import FaceStore

cfg = dataclasses.replace(CONFIG, db_path=tempfile.mkdtemp(prefix="face_e2e_"),
                          max_yaw_deg=90.0, max_pitch_deg=90.0, min_face_px=40)

# Load the model ONCE and let the engine reuse it (avoids a second load).
app = FaceAnalysis(name=cfg.model_name, providers=list(cfg.providers))
app.prepare(ctx_id=cfg.ctx_id, det_size=(cfg.det_size, cfg.det_size))
fe._app = app

img = insightface.data.get_image("t1")
faces = sorted(app.get(img), key=lambda f: f.bbox[0])
h, w = img.shape[:2]

def crop(face):
    x1, y1, x2, y2 = [int(v) for v in face.bbox]
    mx, my = int((x2 - x1) * 0.6), int((y2 - y1) * 0.6)
    return img[max(0, y1-my):min(h, y2+my), max(0, x1-mx):min(w, x2+mx)].copy()

def vary(im, kind):
    if kind == "bright": return cv2.convertScaleAbs(im, alpha=1.0, beta=35)
    if kind == "dark":   return cv2.convertScaleAbs(im, alpha=0.75, beta=-15)
    if kind == "rot":
        M = cv2.getRotationMatrix2D((im.shape[1]/2, im.shape[0]/2), 7, 1.0)
        return cv2.warpAffine(im, M, (im.shape[1], im.shape[0]))
    return im

alice, bob, carol = crop(faces[0]), crop(faces[1]), crop(faces[2])

print("=== ENROLL alice (original, bright, dark) ===")
for k in ("orig", "bright", "dark"):
    r = api.enroll("alice", vary(alice, k), cfg, store=FaceStore(cfg))
    print(f"  {k:6} -> {r['success']} | {r['message']}")

print("=== ENROLL bob ===")
print("  ", api.enroll("bob", bob, cfg, store=FaceStore(cfg))["message"])

print(f"\nthreshold={cfg.match_threshold}")
def verify(who, im, label):
    r = api.verify(who, im, cfg, store=FaceStore(cfg))
    print(f"  verify {who} w/ {label:12}: granted={r['success']} score={r.get('score')} | {r['message']}")

verify("alice", vary(alice, "rot"), "alice(rot)")   # expect GRANT
verify("alice", bob,  "bob")                          # expect DENY
verify("alice", carol, "carol")                       # expect DENY

print("\n=== IDENTIFY (1:N) ===")
for label, im in [("alice(rot)", vary(alice, "rot")), ("bob", bob), ("carol=stranger", carol)]:
    r = api.identify(im, cfg, store=FaceStore(cfg))
    print(f"  {label:16} -> picked={r['user_id']} granted={r['success']} score={r.get('score')} margin={r.get('margin')}")

print("\n=== DUPLICATE guard: enroll bob's face as 'imposter' -> expect blocked ===")
print("  ", api.enroll("imposter", bob, cfg, store=FaceStore(cfg))["message"])
print("\nDONE")
