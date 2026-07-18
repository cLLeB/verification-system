"""Message templates: substitution, locale fallback, safe missing vars."""

from __future__ import annotations

import os

import pytest

from face_service import messagetemplates as mt

T = "t_msgtpl_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_MSGTEMPLATES_FILE"] = str(tmp_path / "mt.json")
    yield


def test_basic_substitution():
    mt.set_template(T, "welcome", "Hello {name}, access to {scope} granted.")
    out = mt.render(T, "welcome", {"name": "Ama", "scope": "Lab"})
    assert out["text"] == "Hello Ama, access to Lab granted."


def test_locale_specific_wins():
    mt.set_template(T, "greet", "Hello {name}", locale="en")
    mt.set_template(T, "greet", "Bonjour {name}", locale="fr")
    assert mt.render(T, "greet", {"name": "Ama"}, locale="fr")["text"] == "Bonjour Ama"


def test_region_falls_back_to_language():
    mt.set_template(T, "greet", "Bonjour {name}", locale="fr")
    out = mt.render(T, "greet", {"name": "Ama"}, locale="fr-CA")
    assert out["text"] == "Bonjour Ama" and out["locale"] == "fr"


def test_falls_back_to_default():
    mt.set_template(T, "greet", "Hi {name}")   # default locale
    out = mt.render(T, "greet", {"name": "Ama"}, locale="de")
    assert out["text"] == "Hi Ama" and out["locale"] == "default"


def test_missing_variable_left_literal():
    mt.set_template(T, "x", "Hello {name}, code {code}")
    out = mt.render(T, "x", {"name": "Ama"})
    assert out["text"] == "Hello Ama, code {code}"


def test_missing_vars_helper():
    mt.set_template(T, "x", "Hello {name}, code {code}")
    assert mt.missing_vars(T, "x", provided=["name"]) == ["code"]


def test_unknown_key():
    assert not mt.render(T, "ghost")["found"]


def test_locales_listing():
    mt.set_template(T, "g", "a", locale="en")
    mt.set_template(T, "g", "b", locale="fr")
    assert mt.locales(T, "g") == ["en", "fr"]


def test_no_code_injection_via_placeholder_syntax():
    # format-spec / attribute access must not be honoured
    mt.set_template(T, "x", "Value {name} end")
    out = mt.render(T, "x", {"name": "{other}"})
    # the injected braces are treated as literal data, not re-expanded
    assert out["text"] == "Value {other} end"


def test_validation():
    with pytest.raises(ValueError):
        mt.set_template(T, "", "body")
    with pytest.raises(ValueError):
        mt.set_template(T, "x", None)
