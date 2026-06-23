"""/v1 palm routing via the Flask client: modality override, graceful palm
unavailability (no ONNX weights in CI), and the no-biometric path.

These run only when the face model + debug images are present (same gate as the
other API tests). Palm has no model in CI, so the palm path is exercised through
its graceful 'unavailable' behaviour rather than a real palm match.
"""
import base64

import numpy as np


def _h(key):
    return {"X-API-Key": key}


def _blank_b64():
    import cv2
    ok, buf = cv2.imencode(".jpg", np.zeros((200, 200, 3), np.uint8))
    return base64.b64encode(buf.tobytes()).decode()


def test_enroll_modality_palm_routes_to_palm_engine(client, make_key, enroll_images):
    """Pinning modality=palm routes to the palm engine — never to face. A face image
    has no palm, so it fails cleanly (no_hand where the detector runs, else
    palm_unavailable), and never silently enrols a face under the palm modality."""
    ak = make_key("admin", "palm_u")
    r = client.post("/v1/enroll", headers=_h(ak),
                    json={"user_id": "p1", "images": enroll_images[:1],
                          "modality": "palm"}).get_json()
    assert r["success"] is False
    palm = r["results"][0]
    assert palm["modality"] == "palm"


def test_blank_image_routes_to_no_biometric(client, make_key):
    """An image with neither a face nor a palm yields the router's no_biometric code."""
    ak = make_key("admin", "nobio")
    r = client.post("/v1/identify", headers=_h(ak), json={"image": _blank_b64()}).get_json()
    assert r["success"] is False and r["code"] == "no_biometric_detected"


def test_face_still_auto_routes(client, make_key, enroll_images, probe_image):
    """Default (no modality field): a face image auto-routes to face end-to-end."""
    ak = make_key("admin", "autoface")
    e = client.post("/v1/enroll", headers=_h(ak),
                    json={"user_id": "fa", "images": enroll_images}).get_json()
    assert e["success"] and e["enrolled"] >= 1
    idr = client.post("/v1/identify", headers=_h(ak), json={"image": probe_image}).get_json()
    assert idr["success"] and idr["user_id"] == "fa" and idr["modality"] == "face"
