"""Access reviews: recertification campaign lifecycle."""

from __future__ import annotations

import os

import pytest

from face_service import accessreview as ar

T = "t_review_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_ACCESSREVIEW_FILE"] = str(tmp_path / "review.json")
    yield


def test_open_and_decide():
    r = ar.open_review(T, ["ama", "kofi", "esi"], reviewer="boss")
    assert ar.certify(T, r["id"], "ama")
    assert ar.flag_revoke(T, r["id"], "kofi")
    st = ar.status(T, r["id"])
    assert st["decided"] == 2 and st["pending"] == ["esi"]
    assert st["keep"] == ["ama"] and st["revoke"] == ["kofi"]


def test_close_returns_revoke_and_pending():
    r = ar.open_review(T, ["ama", "kofi"])
    ar.flag_revoke(T, r["id"], "kofi")
    rep = ar.close(T, r["id"], now=1000)
    assert rep["revoke"] == ["kofi"] and rep["pending"] == ["ama"]


def test_cannot_decide_after_close():
    r = ar.open_review(T, ["ama"])
    ar.close(T, r["id"])
    assert not ar.certify(T, r["id"], "ama")


def test_unknown_subject_rejected():
    r = ar.open_review(T, ["ama"])
    assert not ar.certify(T, r["id"], "stranger")


def test_validation():
    with pytest.raises(ValueError):
        ar.open_review(T, [])
