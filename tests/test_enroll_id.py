"""Enrollment routing: ID documents auto-branch and tag provenance; selfies take
the normal path; forcing source='live' on a card hits the normal multi-face gate."""
import os

import pytest

from face.config import FaceConfig
from face import api as _api
from face import engine
from face.storage import FaceStore

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _img(name):
    import cv2
    p = os.path.join(_ROOT, name)
    return cv2.imread(p) if os.path.exists(p) else None


def _needs(name):
    img = _img(name)
    if img is None:
        pytest.skip(f"fixture {name} not present")
    if not engine.available():
        pytest.skip("insightface not available")
    return img


def test_card_auto_branches_to_id(tmp_path):
    img = _needs("Image.jpeg")
    cfg = FaceConfig(db_path=str(tmp_path))          # isolated store AND index
    st = FaceStore(cfg)
    res = _api.enroll("carduser", img, cfg, store=st, source="auto")
    assert res.get("success") is True
    assert res.get("source") == "id_document"
    assert st.load("carduser").anchor_sources == ["id"]


def test_selfie_auto_takes_normal_path(tmp_path):
    img = _needs("image.png")
    cfg = FaceConfig(db_path=str(tmp_path))
    st = FaceStore(cfg)
    res = _api.enroll("selfieuser", img, cfg, store=st, source="auto")
    assert res.get("success") is True
    assert res.get("source") == "live"
    assert st.load("selfieuser").anchor_sources == ["live"]


def test_verify_with_id_is_rejected(tmp_path):
    """Security boundary: ID auto-branch is enrollment-only. Presenting a card at
    VERIFY must still be rejected by the live-only gates (anti-spoof), even though
    the same card would match this person's template on score alone."""
    cfg = FaceConfig(db_path=str(tmp_path))
    st = FaceStore(cfg)
    _api.enroll("victim", _needs("image.png"), cfg, store=st, source="auto")
    res = _api.verify("victim", _needs("Image.jpeg"), cfg, store=st)
    assert res.get("success") is False
    assert res.get("code") in ("multiple_faces", "pose", "liveness", "no_face")


def test_forcing_live_on_card_is_rejected(tmp_path):
    img = _needs("Image.jpeg")
    cfg = FaceConfig(db_path=str(tmp_path))
    st = FaceStore(cfg)
    res = _api.enroll("forced", img, cfg, store=st, source="live")
    # Normal path's single-face gate rejects the card (main + ghost portrait).
    assert res.get("success") is False
    assert res.get("code") in ("multiple_faces", "pose", "liveness", "no_face")
