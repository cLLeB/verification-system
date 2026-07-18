"""Change freeze: window scoping, categories, exemptions, gate, lift."""

from __future__ import annotations

import os

import pytest

from face_service import changefreeze as cf

T = "t_changefreeze_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_CHANGEFREEZE_FILE"] = str(tmp_path / "cf.json")
    yield


def test_category_frozen_in_window():
    cf.declare(T, start=0, end=100, categories=["thresholds"], reason="peak event")
    assert cf.check(T, "thresholds", now=50)["frozen"]
    assert not cf.check(T, "enrolment", now=50)["frozen"]   # other category ok


def test_outside_window_not_frozen():
    cf.declare(T, start=0, end=100, categories=["thresholds"], reason="x")
    assert not cf.check(T, "thresholds", now=200)["frozen"]


def test_wildcard_freezes_everything():
    cf.declare(T, start=0, end=100, categories=["*"], reason="lockdown")
    assert cf.check(T, "anything", now=50)["frozen"]


def test_exempt_principal_passes():
    cf.declare(T, start=0, end=100, categories=["*"], reason="x",
               exempt=["breakglass-admin"])
    assert not cf.check(T, "thresholds", "breakglass-admin", now=50)["frozen"]
    assert cf.check(T, "thresholds", "normal-admin", now=50)["frozen"]


def test_gate_blocks():
    cf.declare(T, start=0, end=100, categories=["config"], reason="audit")
    res = cf.gate(T, {"success": True}, "config", now=50)
    assert not res["success"] and res["code"] == "CHANGE_FROZEN"


def test_lift_ends_freeze():
    fz = cf.declare(T, start=0, end=100, categories=["*"], reason="x")
    assert cf.lift(T, fz["id"])
    assert not cf.check(T, "config", now=50)["frozen"]
    assert not cf.lift(T, fz["id"])


def test_active_listing():
    cf.declare(T, start=0, end=100, categories=["a"], reason="one")
    cf.declare(T, start=0, end=200, categories=["b"], reason="two")
    act = cf.active(T, now=50)
    assert len(act) == 2


def test_validation():
    with pytest.raises(ValueError):
        cf.declare(T, 100, 0, ["a"], "x")
    with pytest.raises(ValueError):
        cf.declare(T, 0, 100, ["a"], "")
