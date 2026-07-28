"""Field-data recorder: records real attempts, exports them incrementally, and the
export stays token-gated. Also covers the pilot's open-enrolment switch."""

import base64
import io
import json
import os
import zipfile

import cv2
import numpy as np
import pytest


def _img_b64():
    a = (np.random.rand(96, 96, 3) * 255).astype("uint8")
    ok, buf = cv2.imencode(".jpg", a)
    return "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode()


def _frame():
    return (np.random.rand(96, 96, 3) * 255).astype("uint8")


@pytest.fixture
def field(tmp_path, monkeypatch):
    """A recorder pointed at a throwaway directory."""
    from face_service import fielddata as fd
    monkeypatch.setattr(fd, "DIR", str(tmp_path / "fielddata"))
    monkeypatch.setattr(fd, "IMAGES", str(tmp_path / "fielddata" / "images"))
    monkeypatch.setattr(fd, "ENABLED", True)
    monkeypatch.setattr(fd, "_bytes", 0.0)
    return fd


def test_records_capture_and_decision(field):
    result = {"success": True, "modality": "palm", "matched_modality": "palm",
              "user_id": "edwina", "score": 0.71, "code": "match",
              "results": {"palm": {"success": True, "score": 0.71, "margin": 0.02,
                                   "threshold": 0.65,
                                   "candidates": [{"user_id": "edwina", "score": 0.71},
                                                  {"user_id": "caleb", "score": 0.69}]}}}
    field.record("verify", _frame(), result, claimed_user_id="caleb", actor="kiosk")

    evs = field.events(0)
    assert len(evs) == 1
    rec = evs[0]
    assert rec["event"] == "verify" and rec["matched_user_id"] == "edwina"
    assert rec["claimed_user_id"] == "caleb" and rec["score"] == 0.71
    # the runner-up list is what makes the confusion diagnosable
    assert rec["detail"]["palm"]["candidates"][1]["user_id"] == "caleb"
    assert rec["detail"]["palm"]["margin"] == 0.02
    # the frame itself is on disk where the event says it is
    assert os.path.isfile(os.path.join(field.DIR, rec["images"][0]))


def test_burst_keeps_only_the_decided_frame(field):
    burst = [_frame() for _ in range(5)]
    field.record("verify", burst + [burst[2]], {"success": False}, actor="kiosk")
    rec = field.events(0)[0]
    assert len(rec["images"]) == 1               # decided frame only, no duplicate
    assert rec["n_frames"] == 5


def test_disabled_records_nothing(field, monkeypatch):
    monkeypatch.setattr(field, "ENABLED", False)
    field.record("verify", _frame(), {"success": True})
    assert field.events(0) == []


def test_size_budget_stops_writing(field, monkeypatch):
    monkeypatch.setattr(field, "MAX_MB", 0.0)
    field.record("verify", _frame(), {"success": True})
    assert field.events(0) == []                 # over budget -> nothing recorded


def test_stamps_are_strictly_increasing(field):
    """Two captures in the same millisecond must not share a stamp: the export
    cursor pages with `ts > since`, so a duplicate straddling a batch boundary
    would be skipped for ever."""
    for _ in range(25):
        field.record("verify", _frame(), {"success": True})
    stamps = [e["ts"] for e in field.events(0)]
    assert len(set(stamps)) == len(stamps)
    assert stamps == sorted(stamps)


def test_archive_never_skips_on_a_batch_boundary(field):
    """Page through one event at a time; every event must come back exactly once."""
    for _ in range(6):
        field.record("verify", _frame(), {"success": True})
    seen, cursor = [], 0
    while True:
        _, meta = field.archive(cursor, limit=1)
        if meta["count"] == 0:
            break
        seen.append(meta["cursor"])
        cursor = meta["cursor"]
    assert len(seen) == 6 and len(set(seen)) == 6


def test_archive_is_incremental(field):
    for _ in range(4):
        field.record("verify", _frame(), {"success": True, "user_id": "u"})
    blob, meta = field.archive(0, limit=2)
    assert meta["count"] == 2 and meta["remaining"] == 2
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        rows = [json.loads(l) for l in z.read("events.jsonl").decode().splitlines()]
        assert len(rows) == 2
        assert any(n.startswith("images/") for n in z.namelist())
    # resuming from the cursor returns exactly the rest, no overlap
    _, meta2 = field.archive(meta["cursor"], limit=100)
    assert meta2["count"] == 2 and meta2["remaining"] == 0


def test_stats_and_wipe(field):
    field.record("enroll", _frame(), {"success": True, "modality": "face"})
    s = field.stats()
    assert s["events"] == 1 and s["images"] == 1 and s["by_event"]["enroll"] == 1
    field.wipe()
    assert field.events(0) == []


# --- HTTP surface -----------------------------------------------------------
def test_export_endpoints_are_token_gated(client, monkeypatch):
    import app
    monkeypatch.setattr(app, "ANALYTICS_TOKEN", "")
    assert client.get("/api/analytics/field/manifest").status_code == 404
    assert client.get("/api/analytics/field.zip").status_code == 404
    monkeypatch.setattr(app, "ANALYTICS_TOKEN", "sekret")
    assert client.get("/api/analytics/field/manifest").status_code == 403
    assert client.get("/api/analytics/field.zip",
                      headers={"X-Analytics-Token": "nope"}).status_code == 403
    r = client.get("/api/analytics/field/manifest", headers={"X-Analytics-Token": "sekret"})
    assert r.status_code == 200 and r.get_json()["field"]["enabled"] in (True, False)


def test_field_zip_carries_cursor_headers(client, monkeypatch, field):
    import app
    monkeypatch.setattr(app, "ANALYTICS_TOKEN", "sekret")
    monkeypatch.setattr(app.fielddata, "DIR", field.DIR)
    monkeypatch.setattr(app.fielddata, "IMAGES", field.IMAGES)
    field.record("verify", _frame(), {"success": True, "user_id": "u"})
    r = client.get("/api/analytics/field.zip", headers={"X-Analytics-Token": "sekret"})
    assert r.status_code == 200 and r.headers["Content-Type"] == "application/zip"
    assert int(r.headers["X-Field-Count"]) == 1
    assert int(r.headers["X-Field-Cursor"]) > 0
    assert int(r.headers["X-Field-Remaining"]) == 0


def test_open_enrollment_needs_no_password(client, monkeypatch):
    """The pilot switch: enrolling works with no admin session at all."""
    import app
    monkeypatch.setattr(app, "OPEN_ENROLL", True)
    client.post("/admin/logout")
    r = client.post("/api/enroll", json={"user_id": "walkup", "image": _img_b64()})
    assert r.status_code == 200                  # not 401 - no login demanded
    assert client.get("/admin/session").get_json()["open_enroll"] is True


def test_closed_enrollment_still_demands_admin(client, monkeypatch):
    import app
    monkeypatch.setattr(app, "OPEN_ENROLL", False)
    client.post("/admin/logout")
    r = client.post("/api/enroll", json={"user_id": "walkup", "image": _img_b64()})
    assert r.status_code == 401 and r.get_json()["code"] == "admin_required"


def test_admin_console_stays_protected(client, monkeypatch):
    """Open enrolment must not open the operator console."""
    import app
    monkeypatch.setattr(app, "OPEN_ENROLL", True)
    client.post("/admin/logout")
    assert client.get("/api/users").status_code == 401
    assert client.get("/admin/api/overview").status_code == 401
