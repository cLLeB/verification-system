"""Clinic patient identification - cardless patient matching across visits, with a
duplicate-record guard, using the backbone.

    Register:  new patient (MRN + capture). A biometric de-dup check first prevents
               creating a second record for someone already registered.
    Check-in:  identify whoever presents (no card) -> pull their record + visit
               history -> log today's visit. Continuity even if the card is lost.

Run:
    export BACKBONE_URL=... BACKBONE_API_KEY=...
    python examples/clinic-id/app.py               # http://localhost:8003
"""

from __future__ import annotations

import time

from flask import Flask, jsonify, request

import backbone
import store

app = Flask(__name__)
fv = backbone.client()


@app.post("/api/register")
def register():
    data = request.get_json(silent=True) or {}
    mrn = (data.get("mrn") or "").strip()
    name = (data.get("name") or "").strip()
    image = data.get("image") or ""
    if not mrn or not image:
        return jsonify({"ok": False, "message": "MRN and a capture are required."}), 400
    # Duplicate-record guard: is this person already a patient under another MRN?
    hit = fv.identify(image)
    if hit.get("success") and hit.get("user_id") and hit["user_id"] != mrn:
        return jsonify({"ok": False, "code": "duplicate_record",
                        "message": f"This patient already has a record ({hit['user_id']}).",
                        "existing": hit["user_id"]}), 409
    if not store.register_patient(mrn, name):
        return jsonify({"ok": False, "message": f"MRN {mrn} already exists."}), 409
    r = fv.enroll(mrn, [image])
    if not r.get("success"):
        return jsonify({"ok": False, "message": r.get("message") or "Enrolment failed."}), 400
    return jsonify({"ok": True, "message": f"Registered patient {mrn}."})


@app.post("/api/checkin")
def checkin():
    """Cardless check-in: identify the patient, return their record + history, log a visit."""
    data = request.get_json(silent=True) or {}
    image = data.get("image") or ""
    note = (data.get("note") or "").strip()
    if not image:
        return jsonify({"ok": False, "message": "No capture."}), 400
    hit = fv.identify(image)
    if not hit.get("success") or not hit.get("user_id"):
        return jsonify({"ok": False, "code": "no_record",
                        "message": "No matching patient - register them first."})
    mrn = hit["user_id"]
    rec = store.patient(mrn)
    if not rec:
        return jsonify({"ok": False, "message": "Recognised, but no clinic record on file."})
    store.add_visit(mrn, note, time.time())
    return jsonify({"ok": True, "patient": rec, "history": store.history(mrn),
                    "message": f"Checked in {rec['name'] or mrn}."})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8003)
