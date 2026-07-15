"""Break-glass: time-boxed emergency override with use counting."""

from __future__ import annotations

import os

import pytest

from face_service import breakglass as bg

T = "t_bg_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_BREAKGLASS_FILE"] = str(tmp_path / "bg.json")
    yield


def test_activate_and_check():
    rec = bg.activate(T, "ward", reason="cardiac arrest", by="medic1", ttl=100, now=1000)
    assert rec["token"].startswith("bg_")
    assert bg.active(T, "ward", now=1050)
    assert bg.check(T, "ward", now=1050)
    assert bg.report(T)[0]["uses"] == 1


def test_expiry():
    bg.activate(T, "ward", reason="x", by="m", ttl=50, now=1000)
    assert not bg.active(T, "ward", now=1100)
    assert not bg.check(T, "ward", now=1100)


def test_requires_reason_and_actor():
    with pytest.raises(ValueError):
        bg.activate(T, "ward", reason="", by="m")
    with pytest.raises(ValueError):
        bg.activate(T, "ward", reason="x", by="")


def test_close_early():
    bg.activate(T, "ward", reason="x", by="m", ttl=1000, now=1000)
    assert bg.close(T, "ward", now=1010)
    assert not bg.active(T, "ward", now=1010)
    assert not bg.close(T, "ward", now=1010)
