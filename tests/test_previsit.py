"""Visitor pre-registration: expect, check in/out, desk views."""

from __future__ import annotations

import os

import pytest

from face_service import previsit

T = "t_previsit_test"
D = "2026-07-15"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_PREVISIT_FILE"] = str(tmp_path / "previsit.json")
    yield


def test_register_and_checkin():
    rec = previsit.register(T, "Jane Doe", host="ama", date=D, company="Acme")
    assert rec["ref"].startswith("pv_") and rec["status"] == "expected"
    ci = previsit.check_in(T, rec["ref"], now=1000)
    assert ci["status"] == "on_site" and ci["checked_in"] == 1000


def test_unknown_ref_flagged():
    out = previsit.check_in(T, "pv_nope")
    assert out["flagged"] is True


def test_checkout():
    rec = previsit.register(T, "Jane", host="ama", date=D)
    previsit.check_in(T, rec["ref"], now=1000)
    assert previsit.check_out(T, rec["ref"], now=2000)
    assert not previsit.check_out(T, rec["ref"])


def test_desk_views():
    r1 = previsit.register(T, "A", host="h", date=D)
    previsit.register(T, "B", host="h", date=D)
    previsit.check_in(T, r1["ref"], now=1000)
    assert len(previsit.on_site(T, D)) == 1
    assert len(previsit.no_shows(T, D)) == 1


def test_validation():
    with pytest.raises(ValueError):
        previsit.register(T, "", host="h", date=D)
