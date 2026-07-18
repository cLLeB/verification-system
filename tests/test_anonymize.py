"""Anonymize: deterministic pseudonyms, tenant isolation, scrub/redact."""

from __future__ import annotations

import os

import pytest

from face_service import anonymize as an

T = "t_anon_test"
T2 = "t_anon_other"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_ANONYMIZE_FILE"] = str(tmp_path / "anon.json")
    yield


def test_pseudonym_is_deterministic():
    an.register_secret(T, secret="k1")
    a = an.pseudonym(T, "ama")
    b = an.pseudonym(T, "ama")
    assert a == b and a.startswith("anon_")


def test_different_values_differ():
    an.register_secret(T, secret="k1")
    assert an.pseudonym(T, "ama") != an.pseudonym(T, "kofi")


def test_tenants_never_collide():
    an.register_secret(T, secret="k1")
    an.register_secret(T2, secret="k2")
    assert an.pseudonym(T, "ama") != an.pseudonym(T2, "ama")


def test_scrub_pseudonymizes_and_redacts():
    an.register_secret(T, secret="k1")
    rec = {"subject": "ama", "email": "a@x.com", "score": 0.9, "scope": "door"}
    out = an.scrub(T, rec, id_fields=["subject"], redact_fields=["email"])
    assert out["subject"] == an.pseudonym(T, "ama")
    assert out["email"] == "***"
    assert out["score"] == 0.9 and out["scope"] == "door"


def test_scrub_preserves_correlation():
    an.register_secret(T, secret="k1")
    recs = [{"subject": "ama"}, {"subject": "ama"}, {"subject": "kofi"}]
    out = an.scrub_many(T, recs, id_fields=["subject"])
    assert out[0]["subject"] == out[1]["subject"] != out[2]["subject"]


def test_empty_id_passes_through():
    an.register_secret(T, secret="k1")
    out = an.scrub(T, {"subject": ""}, id_fields=["subject"])
    assert out["subject"] == ""


def test_lazy_secret_when_unregistered():
    # no register_secret call; pseudonym still stable within the run
    a = an.pseudonym(T, "ama")
    b = an.pseudonym(T, "ama")
    assert a == b
