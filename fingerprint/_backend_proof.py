"""Backend-only proof: does enroll/verify/identify actually work on REAL
fingerprint images, with the phone entirely out of the loop?

Enrolls one finger from clean sensor images, then checks:
  - same finger  -> should be GRANTED
  - other finger -> should be DENIED
Also feeds a real phone capture in to show how little ridge data it carries.
"""
from __future__ import annotations
import dataclasses
import tempfile
import cv2

from fingerprint import api as engine
from fingerprint.config import CONFIG
from fingerprint.storage import TemplateStore

# Fresh throwaway DB so we never touch real enrolments.
tmp = tempfile.mkdtemp(prefix="fp_proof_")
cfg = dataclasses.replace(CONFIG, db_path=tmp)
store = TemplateStore(cfg)

def load(p):
    img = cv2.imread(p, cv2.IMREAD_COLOR)
    if img is None:
        raise SystemExit(f"could not read {p}")
    return img

S = "samples/100__M_Left_{}_finger_{}.BMP"
print("\n=== 1. ENROLL index finger (3 impressions) ===")
for variant in ("CR", "Obl", "Zcut"):
    r = engine.enroll("index_user", load(S.format("index", variant)), cfg, store)
    print(f"  {variant:5} -> success={r['success']} | {r['message']}")

print("\n=== 2. VERIFY: SAME finger (index) -> expect GRANTED ===")
for variant in ("CR", "Obl", "Zcut"):
    r = engine.verify("index_user", load(S.format("index", variant)), cfg, store)
    print(f"  {variant:5} -> GRANTED={r['success']} | score={r.get('score')} | {r['message']}")

print("\n=== 3. VERIFY: DIFFERENT fingers -> expect DENIED ===")
for finger in ("middle", "ring", "little"):
    for variant in ("CR", "Obl"):
        try:
            img = load(S.format(finger, variant))
        except SystemExit:
            continue
        r = engine.verify("index_user", img, cfg, store)
        print(f"  {finger:6} {variant:4} -> GRANTED={r['success']} | score={r.get('score')} | {r['message']}")

print("\n=== 4. IDENTIFY (1:N) same finger -> expect picks index_user ===")
r = engine.identify(load(S.format("index", "Obl")), cfg, store)
print(f"  picked={r.get('user_id')} granted={r['success']} score={r.get('score')} minutiae={r.get('minutiae')}")

print("\n=== 5. A REAL PHONE CAPTURE through the same pipeline ===")
import glob, os
caps = sorted(glob.glob("debug/cap-verify_*.png"), key=os.path.getsize, reverse=True)
if caps:
    r = engine.identify(load(caps[0]), cfg, store)
    print(f"  file={os.path.basename(caps[0])}")
    print(f"  granted={r['success']} minutiae={r.get('minutiae')} | {r['message']}")
else:
    print("  (no phone captures found)")
print()
