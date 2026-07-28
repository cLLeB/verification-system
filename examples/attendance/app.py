"""Attendance kiosk - a tiny product that clocks staff in/out by their face or palm,
using the Biometric Verification Backbone as its identity layer.

    Enrol:  operator adds a staff member (name + a capture) -> backbone stores it.
    Clock:  anyone steps up, the kiosk grabs a frame -> backbone 1:N identify ->
            we log an in/out punch for whoever it is. No cards, no PINs, no scanner.

Run:
    export BACKBONE_URL=... BACKBONE_API_KEY=...     # an admin key on the backbone
    python examples/attendance/app.py                # http://localhost:8000
"""

from __future__ import annotations

import time

from flask import Flask, jsonify, render_template, request

import backbone
import store

app = Flask(__name__)
fv = backbone.client()


@app.get("/")
def index():
    return render_template("index.html")


@app.post("/api/enroll")
def enroll():
    """Operator enrols a staff member: name + one capture (face or palm - the backbone
    auto-detects). In production gate this behind your own operator auth."""
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    image = data.get("image") or ""
    if not name or not image:
        return jsonify({"ok": False, "message": "Name and a capture are required."}), 400
    r = fv.enroll(name, [image])
    ok = bool(r.get("success"))
    return jsonify({"ok": ok, "message": r.get("message") or ("Enrolled " + name if ok
                                                              else "Enrolment failed."),
                    "detail": r})


@app.post("/api/clock")
def clock():
    """The kiosk action: identify whoever is in front of the camera, then punch them
    in/out. This is the entire integration - one identify call + our own log."""
    data = request.get_json(silent=True) or {}
    image = data.get("image") or ""
    if not image:
        return jsonify({"ok": False, "message": "No capture."}), 400
    r = fv.identify(image)                       # 1:N - "who is this?"
    if not r.get("success") or not r.get("user_id"):
        return jsonify({"ok": False, "code": r.get("code", "no_match"),
                        "message": "Not recognised - see the operator to enrol."})
    punch = store.record_punch(r["user_id"], time.time())
    verb = "clocked IN" if punch["direction"] == "in" else "clocked OUT"
    return jsonify({"ok": True, "user_id": r["user_id"], "direction": punch["direction"],
                    "modality": r.get("modality"), "score": r.get("score"),
                    "message": f"{r['user_id']} {verb}."})


@app.get("/api/report")
def report():
    return jsonify({"ok": True, "date": time.strftime("%Y-%m-%d"), "punches": store.today()})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
