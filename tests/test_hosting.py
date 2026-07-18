"""Visitor hosting: sponsorship window, gate, co-presence, host visitors."""

from __future__ import annotations

import os

import pytest

from face_service import hosting

T = "t_hosting_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_HOSTING_FILE"] = str(tmp_path / "hosting.json")
    yield


def test_sponsored_within_window():
    hosting.sponsor(T, "host1", "vis1", start=0, end=100)
    assert hosting.is_sponsored(T, "vis1", now=50)["sponsored"]
    assert not hosting.is_sponsored(T, "vis1", now=200)["sponsored"]


def test_gate_requires_sponsorship():
    res = hosting.gate(T, {"success": True, "code": "GRANTED"}, "vis1", now=0)
    assert not res["success"] and res["code"] == "NO_SPONSOR"
    hosting.sponsor(T, "host1", "vis1", start=0, end=100)
    assert hosting.gate(T, {"success": True}, "vis1", now=50)["success"]


def test_co_presence_required():
    hosting.sponsor(T, "host1", "vis1", start=0, end=100)
    # host not present
    res = hosting.gate(T, {"success": True}, "vis1", require_present=True, now=50)
    assert not res["success"] and res["code"] == "HOST_ABSENT"
    hosting.set_present(T, "host1", True)
    assert hosting.gate(T, {"success": True}, "vis1", require_present=True, now=50)["success"]


def test_end_sponsorship():
    sp = hosting.sponsor(T, "host1", "vis1", start=0, end=100)
    assert hosting.end(T, sp["id"])
    assert not hosting.is_sponsored(T, "vis1", now=50)["sponsored"]
    assert not hosting.end(T, sp["id"])


def test_host_visitors():
    hosting.sponsor(T, "host1", "vis1", start=0, end=100)
    hosting.sponsor(T, "host1", "vis2", start=0, end=100)
    hosting.sponsor(T, "host2", "vis3", start=0, end=100)
    assert hosting.host_visitors(T, "host1", now=50) == ["vis1", "vis2"]


def test_validation():
    with pytest.raises(ValueError):
        hosting.sponsor(T, "", "vis1", 0, 100)
    with pytest.raises(ValueError):
        hosting.sponsor(T, "host1", "vis1", 100, 0)
