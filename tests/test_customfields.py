"""Custom fields: typed validation, coercion, required/unknown handling."""

from __future__ import annotations

import os

import pytest

from face_service import customfields as cf

T = "t_customfields_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_CUSTOMFIELDS_FILE"] = str(tmp_path / "cf.json")
    yield


def test_string_and_int_coercion():
    cf.define_field(T, "emp_no", "int", min=1, max=9999)
    cf.define_field(T, "dept", "string", max_len=20)
    out = cf.set_values(T, "ama", {"emp_no": "42", "dept": "ops"})
    assert out["ok"] and out["values"] == {"emp_no": 42, "dept": "ops"}


def test_int_bounds_enforced():
    cf.define_field(T, "level", "int", min=1, max=5)
    assert not cf.set_values(T, "ama", {"level": 9})["ok"]
    assert "level" in cf.set_values(T, "ama", {"level": 9})["errors"]


def test_string_max_len():
    cf.define_field(T, "code", "string", max_len=3)
    assert not cf.set_values(T, "ama", {"code": "toolong"})["ok"]


def test_enum_choices():
    cf.define_field(T, "colour", "enum", choices=["red", "green"])
    assert cf.set_values(T, "ama", {"colour": "red"})["ok"]
    assert not cf.set_values(T, "ama", {"colour": "purple"})["ok"]


def test_bool_coercion():
    cf.define_field(T, "vip", "bool")
    assert cf.set_values(T, "ama", {"vip": "yes"})["values"]["vip"] is True
    assert cf.set_values(T, "ama", {"vip": "0"})["values"]["vip"] is False
    assert not cf.set_values(T, "ama", {"vip": "maybe"})["ok"]


def test_date_iso():
    cf.define_field(T, "start", "date")
    assert cf.set_values(T, "ama", {"start": "2026-01-15"})["ok"]
    assert not cf.set_values(T, "ama", {"start": "15/01/2026"})["ok"]


def test_required_missing_is_error():
    cf.define_field(T, "dept", "string", required=True)
    out = cf.set_values(T, "ama", {})
    assert not out["ok"] and out["errors"]["dept"] == "required"


def test_unknown_field_rejected():
    cf.define_field(T, "dept", "string")
    out = cf.set_values(T, "ama", {"ghost": "x"})
    assert not out["ok"] and out["errors"]["ghost"] == "unknown field"


def test_all_or_nothing_store():
    cf.define_field(T, "a", "int")
    cf.define_field(T, "b", "int")
    cf.set_values(T, "ama", {"a": 1, "b": 2})
    cf.set_values(T, "ama", {"a": 3, "b": "bad"})   # rejected wholesale
    assert cf.get_values(T, "ama") == {"a": 1, "b": 2}


def test_validation():
    with pytest.raises(ValueError):
        cf.define_field(T, "", "string")
    with pytest.raises(ValueError):
        cf.define_field(T, "x", "float")
    with pytest.raises(ValueError):
        cf.define_field(T, "x", "enum", choices=[])
    with pytest.raises(ValueError):
        cf.set_values(T, "", {})
