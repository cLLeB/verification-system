"""Event log: filtering, time windows, cursor pagination, count."""

from __future__ import annotations

import os

import pytest

from face_service import eventlog as el

T = "t_eventlog_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_EVENTLOG_FILE"] = str(tmp_path / "el.json")
    yield


def test_filter_by_type_and_actor():
    el.append(T, "verify.ok", actor="ama", now=1)
    el.append(T, "verify.denied", actor="ama", now=2)
    el.append(T, "verify.ok", actor="kofi", now=3)
    assert el.query(T, event_type="verify.ok")["returned"] == 2
    assert el.query(T, actor="ama")["returned"] == 2
    assert el.query(T, event_type="verify.ok", actor="kofi")["returned"] == 1


def test_time_window():
    for t in range(1, 6):
        el.append(T, "e", now=t)
    # [2,4): events at 2 and 3
    assert el.count(T, since=2, until=4) == 2


def test_newest_first():
    el.append(T, "e", now=1)
    el.append(T, "e", now=2)
    page = el.query(T)["events"]
    assert [e["at"] for e in page] == [2, 1]


def test_cursor_pagination():
    for t in range(1, 6):
        el.append(T, "e", now=t)
    p1 = el.query(T, limit=2)
    assert [e["at"] for e in p1["events"]] == [5, 4]
    assert p1["next_cursor"] is not None
    p2 = el.query(T, limit=2, cursor=p1["next_cursor"])
    assert [e["at"] for e in p2["events"]] == [3, 2]
    p3 = el.query(T, limit=2, cursor=p2["next_cursor"])
    assert [e["at"] for e in p3["events"]] == [1]
    assert p3["next_cursor"] is None


def test_get_by_id():
    r = el.append(T, "e", actor="ama", now=1)
    got = el.get(T, r["id"])
    assert got["exists"] and got["actor"] == "ama"
    assert not el.get(T, "ghost")["exists"]


def test_validation():
    with pytest.raises(ValueError):
        el.append(T, "")
