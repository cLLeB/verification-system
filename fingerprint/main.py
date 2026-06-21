"""Desktop client (laptop webcam) for the contactless fingerprint engine.

NOTE: most laptop webcams cannot resolve true friction ridges, so accuracy here
is limited by hardware. The mobile web app (app.py) using a phone back camera is
the recommended path. This client shares the exact same engine and decision logic.

Usage:
    python main.py                 # verify (1:N identify)
    python main.py --enroll        # enrol a new user (captures several impressions)
    python main.py --verify <id>   # 1:1 verification against a claimed user
"""

from __future__ import annotations

import sys
import time

import cv2

from fingerprint import api as engine
from fingerprint.config import CONFIG
from liveness import check_liveness

BOX_W, BOX_H = 200, 280


def _draw_overlay(image, x0, y0, x1, y1, lines):
    cv2.rectangle(image, (x0, y0), (x1, y1), (0, 255, 0), 2)
    cv2.putText(image, "Place fingertip in box", (x0 - 20, y0 - 12),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
    y = 40
    for text, color in lines:
        cv2.putText(image, text, (20, y), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
        y += 32


def run(mode: str, claimed_user: str = "") -> None:
    print(f"--- Fingerprint system: {mode.upper()} mode ---")

    user_id = ""
    needed = 1
    if mode == "enroll":
        user_id = input("Enter User ID to enroll: ").strip()
        if not user_id:
            print("Invalid User ID. Exiting.")
            return
        needed = CONFIG.samples_per_user
        print(f"Capture {needed} impressions of the SAME finger. Press 'c' for each, 'q' to quit.")

    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("ERROR: could not open camera.")
        return

    captured = 0
    while cap.isOpened():
        ok, frame = cap.read()
        if not ok:
            continue
        frame = cv2.flip(frame, 1)
        clean = frame.copy()
        h, w = frame.shape[:2]
        x0, y0 = (w - BOX_W) // 2, (h - BOX_H) // 2
        x1, y1 = x0 + BOX_W, y0 + BOX_H

        status = (f"ENROLL {user_id}: {captured}/{needed}" if mode == "enroll"
                  else mode.upper())
        _draw_overlay(frame, x0, y0, x1, y1,
                      [(status, (255, 200, 0)),
                       ("c=capture  q=quit", (200, 200, 200))])
        cv2.imshow("Fingerprint", frame)

        key = cv2.waitKey(5) & 0xFF
        if key == ord("q"):
            break
        if key != ord("c"):
            continue

        roi = clean[y0:y1, x0:x1]
        is_live, _s, msg = check_liveness(roi)
        if not is_live:
            print(f">> {msg}. Recapture.")
            time.sleep(1)
            continue

        print("Processing...")
        if mode == "enroll":
            res = engine.enroll(user_id, roi, CONFIG)
            print(">>", res["message"])
            if res["success"]:
                captured = res.get("samples", captured + 1)
                if captured >= needed:
                    print(f">>> Enrollment complete for {user_id}.")
                    break
            else:
                time.sleep(1)
        elif mode == "verify_user":
            res = engine.verify(claimed_user, roi, CONFIG)
            print(">>", res["message"], f"(score={res.get('score')})")
            time.sleep(1.5)
        else:  # identify
            res = engine.identify(roi, CONFIG)
            print(">>", res["message"], f"(score={res.get('score')}, margin={res.get('margin')})")
            if res["success"]:
                time.sleep(1.5)
                break
            time.sleep(1.5)

    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--enroll":
        run("enroll")
    elif len(sys.argv) > 2 and sys.argv[1] == "--verify":
        run("verify_user", sys.argv[2])
    else:
        run("identify")
