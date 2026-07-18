"""On-call rotations: round-robin resolution, overrides, and upcoming shifts."""

from __future__ import annotations

import os

import pytest

from face_service import oncall

T = "t_oncall_test"
DAY = 86400


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_ONCALL_FILE"] = str(tmp_path / "oncall.json")
    yield


def _rot():
    return oncall.define(T, "ops", ["ama", "kofi", "esi"],
                         shift_seconds=DAY, anchor=0)


def test_round_robin_by_shift():
    r = _rot()
    assert oncall.whoisoncall(T, r["id"], when=0)["member"] == "ama"
    assert oncall.whoisoncall(T, r["id"], when=DAY + 1)["member"] == "kofi"
    assert oncall.whoisoncall(T, r["id"], when=2 * DAY)["member"] == "esi"
    assert oncall.whoisoncall(T, r["id"], when=3 * DAY)["member"] == "ama"


def test_override_takes_precedence():
    r = _rot()
    oncall.override(T, r["id"], "zara", start=0, end=DAY)
    out = oncall.whoisoncall(T, r["id"], when=100)
    assert out["member"] == "zara" and out["source"] == "override"
    # outside the window, back to rotation
    assert oncall.whoisoncall(T, r["id"], when=DAY + 100)["member"] == "kofi"


def test_upcoming_shifts():
    r = _rot()
    up = oncall.upcoming(T, r["id"], count=3, when=0)
    assert [s["member"] for s in up] == ["ama", "kofi", "esi"]
    assert up[1]["start"] == DAY


def test_upcoming_from_mid_rotation():
    r = _rot()
    up = oncall.upcoming(T, r["id"], count=2, when=int(2.5 * DAY))
    assert up[0]["member"] == "esi" and up[0]["start"] == 2 * DAY


def test_unknown_rotation():
    assert not oncall.whoisoncall(T, "nope")["exists"]
    assert oncall.upcoming(T, "nope") == []


def test_validation():
    with pytest.raises(ValueError):
        oncall.define(T, "", ["a"])
    with pytest.raises(ValueError):
        oncall.define(T, "x", [])
    with pytest.raises(ValueError):
        oncall.define(T, "x", ["a"], shift_seconds=0)
    r = _rot()
    with pytest.raises(ValueError):
        oncall.override(T, r["id"], "z", start=10, end=10)
