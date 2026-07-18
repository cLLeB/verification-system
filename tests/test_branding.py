"""Branding: partial merge, defaults, validation, reset."""

from __future__ import annotations

import os

import pytest

from face_service import branding

T = "t_branding_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_BRANDING_FILE"] = str(tmp_path / "branding.json")
    yield


def test_defaults_when_unset():
    theme = branding.resolve(T)
    assert theme["product_name"] == "Contactless ID"
    assert theme["primary_color"] == "#2563EB"


def test_partial_override_merges():
    branding.set_branding(T, product_name="Acme Access")
    theme = branding.resolve(T)
    assert theme["product_name"] == "Acme Access"
    assert theme["primary_color"] == "#2563EB"    # default preserved


def test_multiple_tokens():
    branding.set_branding(T, primary_color="#ff0000", logo_url="https://x.com/l.png")
    theme = branding.resolve(T)
    assert theme["primary_color"] == "#FF0000"    # normalised upper
    assert theme["logo_url"] == "https://x.com/l.png"


def test_reset():
    branding.set_branding(T, product_name="Acme")
    assert branding.reset(T)
    assert branding.resolve(T)["product_name"] == "Contactless ID"


def test_invalid_color_rejected():
    with pytest.raises(ValueError):
        branding.set_branding(T, primary_color="red")


def test_invalid_url_and_email():
    with pytest.raises(ValueError):
        branding.set_branding(T, logo_url="notaurl")
    with pytest.raises(ValueError):
        branding.set_branding(T, support_email="nope")


def test_unknown_token_rejected():
    with pytest.raises(ValueError):
        branding.set_branding(T, backgroundColor="#fff")


def test_empty_update_rejected():
    with pytest.raises(ValueError):
        branding.set_branding(T)
