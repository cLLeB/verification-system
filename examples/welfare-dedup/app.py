"""Welfare de-dup + payout — registers beneficiaries WITHOUT duplicates and verifies
them at payout, using the backbone.

    Register:  before enrolling, ask the backbone "is this face already someone?"
               (1:N identify). If yes -> block the ghost/duplicate. If no -> enrol.
    Payout:    identify whoever steps up -> pay the matched beneficiary. No cards,
               and worn fingerprints don't exclude anyone (face or palm).

Run:
    export BACKBONE_URL=... BACKBONE_API_KEY=...
    python examples/welfare-dedup/app.py           # http://localhost:8001
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
    """Register a beneficiary with a biometric de-dup gate: the SAME face/palm cannot
    be registered twice under different names (the ghost-beneficiary killer)."""
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    program = (data.get("program") or "cash-transfer").strip()
    image = data.get("image") or ""
    if not name or not image:
        return jsonify({"ok": False, "message": "Name and a capture are required."}), 400

    # 1) Biometric duplicate check — is this person already enrolled (any name)?
    hit = fv.identify(image)
    if hit.get("success") and hit.get("user_id") and hit["user_id"] != name:
        return jsonify({"ok": False, "code": "duplicate_beneficiary",
                        "message": f"This person is already registered as '{hit['user_id']}'.",
                        "existing": hit["user_id"]}), 409
    # 2) Name-level check + enrol on the backbone
    if not store.register(name, program):
        return jsonify({"ok": False, "message": f"'{name}' is already registered."}), 409
    r = fv.enroll(name, [image])
    if not r.get("success"):
        return jsonify({"ok": False, "message": r.get("message") or "Enrolment failed."}), 400
    return jsonify({"ok": True, "message": f"Registered {name}."})


@app.post("/api/payout")
def payout():
    """Verify a beneficiary at payout by identify, then log the disbursement."""
    data = request.get_json(silent=True) or {}
    amount = data.get("amount", 0)
    image = data.get("image") or ""
    if not image:
        return jsonify({"ok": False, "message": "No capture."}), 400
    hit = fv.identify(image)
    if not hit.get("success") or not hit.get("user_id"):
        return jsonify({"ok": False, "code": "not_a_beneficiary",
                        "message": "Not recognised as a registered beneficiary."})
    if not store.is_registered(hit["user_id"]):
        return jsonify({"ok": False, "message": "Recognised, but not on the payout roll."})
    p = store.record_payout(hit["user_id"], amount, time.time())
    return jsonify({"ok": True, "beneficiary": p["name"], "amount": p["amount"],
                    "message": f"Paid {p['amount']} to {p['name']}."})


@app.get("/api/summary")
def summary():
    return jsonify({"ok": True, **store.summary()})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8001)
