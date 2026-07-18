"""JSON patch: diff/apply roundtrip, nested, lists, invert, immutability."""

from __future__ import annotations

from face_service import jsonpatch as jp


def test_replace_scalar():
    a = {"threshold": 0.6}
    b = {"threshold": 0.8}
    ops = jp.diff(a, b)
    assert ops == [{"op": "replace", "path": "/threshold", "value": 0.8}]
    assert jp.apply(a, ops) == b


def test_add_and_remove_keys():
    a = {"x": 1, "y": 2}
    b = {"x": 1, "z": 3}
    assert jp.apply(a, jp.diff(a, b)) == b


def test_nested():
    a = {"policy": {"liveness": False, "scopes": {"lobby": 0.6}}}
    b = {"policy": {"liveness": True, "scopes": {"lobby": 0.7}}}
    assert jp.apply(a, jp.diff(a, b)) == b


def test_lists():
    a = {"tags": ["a", "b", "c"]}
    b = {"tags": ["a", "x"]}          # replace b->x, remove c
    assert jp.apply(a, jp.diff(a, b)) == b


def test_list_growth():
    a = {"t": [1]}
    b = {"t": [1, 2, 3]}
    assert jp.apply(a, jp.diff(a, b)) == b


def test_path_escaping():
    a = {"a/b": 1}
    b = {"a/b": 2}
    ops = jp.diff(a, b)
    assert ops[0]["path"] == "/a~1b"
    assert jp.apply(a, ops) == b


def test_immutability():
    a = {"x": {"y": 1}}
    b = {"x": {"y": 2}}
    ops = jp.diff(a, b)
    jp.apply(a, ops)
    assert a == {"x": {"y": 1}}       # original untouched


def test_invert_rollback():
    a = {"threshold": 0.6, "liveness": False}
    b = {"threshold": 0.8, "liveness": True}
    ops = jp.diff(a, b)
    inverse = jp.invert(a, ops)
    assert jp.apply(b, inverse) == a


def test_no_change_empty_diff():
    a = {"x": 1}
    assert jp.diff(a, {"x": 1}) == []
