"""ID-document detection: ghost-vs-two-people logic (model-free) + real fixtures."""
import os
from types import SimpleNamespace

import numpy as np
import pytest

from face.config import CONFIG
from face import id_document as idd

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _face(bbox, emb):
    e = np.asarray(emb, np.float32)
    e = e / (np.linalg.norm(e) or 1.0)
    px = int(min(bbox[2] - bbox[0], bbox[3] - bbox[1]))
    return SimpleNamespace(bbox=bbox, embedding=e, face_px=px, det_score=0.9)


def _unit(seed, dim=512):
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(dim).astype(np.float32)
    return v / np.linalg.norm(v)


def test_ghost_portrait_classified_as_id():
    # Large primary + much smaller SAME-identity second face = a card's ghost.
    img = np.zeros((600, 400, 3), np.uint8)
    same = _unit(1)
    faces = [_face((20, 20, 240, 260), same),          # primary
             _face((300, 20, 360, 80), same * 0.98)]   # tiny, same person
    a = idd.assess(img, faces, CONFIG, live_score=None)
    assert a.is_id is True
    assert a.signals.ghost_portrait >= 0.5


def test_two_real_people_not_id():
    # Two comparably-sized DIFFERENT identities = genuine multi-person, NOT an ID.
    img = np.zeros((400, 600, 3), np.uint8)
    faces = [_face((20, 20, 220, 220), _unit(2)),
             _face((320, 20, 520, 220), _unit(99))]
    a = idd.assess(img, faces, CONFIG, live_score=None)
    assert a.is_id is False
    assert a.signals.ghost_portrait == 0.0


def test_single_clean_face_not_id():
    img = np.zeros((300, 300, 3), np.uint8)
    faces = [_face((30, 30, 270, 270), _unit(3))]      # fills the frame
    a = idd.assess(img, faces, CONFIG, live_score=None)
    assert a.is_id is False


def test_empty_faces_not_id():
    a = idd.assess(np.zeros((100, 100, 3), np.uint8), [], CONFIG)
    assert a.is_id is False and a.primary_face_index == -1


# --- integration on real fixtures (skipped if the photos aren't present) ------

def _img(name):
    import cv2
    p = os.path.join(_ROOT, name)
    if not os.path.exists(p):
        return None
    return cv2.imread(p)


@pytest.mark.parametrize("name,expect_id", [
    ("Image.jpeg", True),     # official ID card (main + ghost portrait)
    ("image.png", False),     # recent passport-style selfie
    ("me.jpeg", False),       # year-old selfie
])
def test_real_fixtures(name, expect_id):
    from face import engine
    img = _img(name)
    if img is None:
        pytest.skip(f"fixture {name} not present")
    if not engine.available():
        pytest.skip("insightface not available")
    faces = engine.detect_all(img, CONFIG)
    a = idd.assess(img, faces, CONFIG, live_score=None)
    assert a.is_id is expect_id, f"{name}: conf={a.confidence:.3f} {a.signals.as_dict()}"
