"""Exam candidate identity — stops impersonation at the seat using the backbone's
1:1 verify against a pre-enrolled candidate roster.

    Register:  the board enrols each candidate (index number + capture).
    Check-in:  candidate claims their index number and shows their face/palm ->
               fv.verify(index_no, image). Match -> seated. No match -> FLAGGED as a
               possible impersonation (someone sitting for another candidate).

Run:
    export BACKBONE_URL=... BACKBONE_API_KEY=...
    python examples/exam-identity/app.py           # http://localhost:8002
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
    index_no = (data.get("index_no") or "").strip()
    name = (data.get("name") or "").strip()
    exam = (data.get("exam") or "").strip()
    image = data.get("image") or ""
    if not index_no or not image:
        return jsonify({"ok": False, "message": "Index number and a capture are required."}), 400
    if not store.register_candidate(index_no, name, exam):
        return jsonify({"ok": False, "message": f"Candidate {index_no} already registered."}), 409
    r = fv.enroll(index_no, [image])          # enrol keyed by the index number
    if not r.get("success"):
        return jsonify({"ok": False, "message": r.get("message") or "Enrolment failed."}), 400
    return jsonify({"ok": True, "message": f"Registered {index_no}."})


@app.post("/api/checkin")
def checkin():
    """The candidate CLAIMS an index number; we 1:1 verify their biometric against it.
    A mismatch is the impersonation signal — logged as flagged."""
    data = request.get_json(silent=True) or {}
    index_no = (data.get("index_no") or "").strip()
    exam = (data.get("exam") or "").strip()
    image = data.get("image") or ""
    if not index_no or not image:
        return jsonify({"ok": False, "message": "Index number and a capture are required."}), 400
    r = fv.verify(index_no, image)            # 1:1 — "is this really 12345?"
    verified = bool(r.get("success"))
    store.record_checkin(index_no, exam, verified, r.get("score"), time.time())
    if verified:
        return jsonify({"ok": True, "index_no": index_no, "message": f"{index_no} verified — seated."})
    return jsonify({"ok": False, "code": "identity_mismatch", "flagged": True,
                    "message": f"Face/palm does NOT match {index_no} — flagged for the invigilator."})


@app.get("/api/report")
def report():
    exam = request.args.get("exam")
    return jsonify({"ok": True, "summary": store.summary(exam), "flagged": store.flagged(exam)})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8002)
