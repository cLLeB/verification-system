"""Temporary liveness/PAD data-collection tool: fully OFF without the secret,
token-gated when on, and a save -> export round-trip works."""
import base64
import io
import zipfile

import cv2
import numpy as np


def _img_b64():
    a = (np.random.rand(64, 64, 3) * 255).astype("uint8")
    ok, buf = cv2.imencode(".jpg", a)
    return "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode()


def test_off_without_secret(client, monkeypatch):
    import app
    monkeypatch.setattr(app, "ANALYTICS_TOKEN", "")
    assert client.get("/collect").status_code == 404
    assert client.post("/api/collect", json={"label": "live", "image": _img_b64()}).status_code == 404
    assert client.get("/api/analytics/collect").status_code == 404


def test_gated_save_and_export(client, monkeypatch, tmp_path):
    import app
    monkeypatch.setattr(app, "ANALYTICS_TOKEN", "sekret")
    monkeypatch.setattr(app, "_COLLECT_DIR", str(tmp_path / "collect"))
    assert client.get("/collect?token=sekret").status_code == 200
    # wrong token rejected
    assert client.post("/api/collect",
                       json={"token": "nope", "label": "live", "image": _img_b64()}).status_code == 403
    # bad label rejected
    assert client.post("/api/collect",
                       json={"token": "sekret", "label": "junk", "image": _img_b64()}).status_code == 400
    # genuine saves
    r = client.post("/api/collect",
                    json={"token": "sekret", "label": "live", "image": _img_b64()}).get_json()
    assert r["success"] and r["count"] == 1
    client.post("/api/collect", json={"token": "sekret", "label": "spoof", "image": _img_b64()})
    client.post("/api/collect", json={"token": "sekret", "label": "palm_live", "image": _img_b64()})
    # export (header-gated) returns a zip with the labeled folders
    d = client.get("/api/analytics/collect", headers={"X-Analytics-Token": "sekret"}).get_json()
    assert d["counts"]["live"] == 1 and d["counts"]["spoof"] == 1 and d["counts"]["palm_live"] == 1
    z = zipfile.ZipFile(io.BytesIO(base64.b64decode(d["zip_b64"])))
    names = z.namelist()
    assert any(n.startswith("live/") for n in names) and any(n.startswith("spoof/") for n in names)
    # export without the header is forbidden
    assert client.get("/api/analytics/collect").status_code == 403
