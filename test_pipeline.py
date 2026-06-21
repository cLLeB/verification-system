"""End-to-end engine self-test on real fingerprint images.

Unlike the old version (which matched two synthetic noise images), this enrols a
real fingerprint and checks that:
  * the SAME finger (a different impression) is ACCEPTED, and
  * a DIFFERENT finger is REJECTED.

It looks for sample images under _calib/fake (staged from the reference dataset).
If they are absent it explains how to provide your own and exits gracefully.

Run:  python test_pipeline.py
"""

from __future__ import annotations

import glob
import os
import sys
import tempfile

import cv2

from fingerprint.config import Config
from fingerprint import api as engine
from fingerprint.storage import TemplateStore


def _base(path):
    b = os.path.basename(path)
    for s in ("_CR", "_Obl", "_Zcut"):
        b = b.replace(s, "")
    return os.path.splitext(b)[0]


def main():
    files = sorted(glob.glob("samples/*.BMP")) or sorted(glob.glob("_calib/fake/*.BMP"))
    if len(files) < 4:
        print("No staged sample images found under samples/.")
        print("This self-test needs a few fingerprint images with multiple")
        print("impressions per finger. See SETUP_GUIDE.md (calibration section).")
        return 0

    # Group by finger; find one finger with >=2 impressions + a different finger.
    groups = {}
    for f in files:
        groups.setdefault(_base(f), []).append(f)
    multi = {k: v for k, v in groups.items() if len(v) >= 2}
    if not multi:
        print("Could not find a finger with two impressions to test with.")
        return 0

    finger = sorted(multi)[0]
    enroll_img, probe_same = multi[finger][0], multi[finger][1]
    other_finger = next(k for k in sorted(groups) if k != finger)
    probe_other = groups[other_finger][0]

    with tempfile.TemporaryDirectory() as tmp:
        cfg = Config(db_path=tmp)
        store = TemplateStore(cfg)

        print(f"[1] Enrolling finger '{finger}' from {os.path.basename(enroll_img)} ...")
        res = engine.enroll("test_user", cv2.imread(enroll_img), cfg, store)
        print("    ->", res["message"])
        if not res["success"]:
            print("[FAIL] enrolment failed.")
            return 1

        print(f"[2] Verifying SAME finger ({os.path.basename(probe_same)}) ...")
        same = engine.identify(cv2.imread(probe_same), cfg, store)
        print(f"    -> {same['message']}  (score={same.get('score')})")

        print(f"[3] Verifying DIFFERENT finger ({os.path.basename(probe_other)}) ...")
        other = engine.identify(cv2.imread(probe_other), cfg, store)
        print(f"    -> {other['message']}  (score={other.get('score')})")

        ok = same["success"] and not other["success"]
        print()
        print("[SUCCESS] Engine accepts the right finger and rejects the wrong one."
              if ok else
              "[CHECK] Unexpected result — see scores above (thresholds may need calibration).")
        return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
