"""Exam check-in logic (pure SQLite). python -m pytest examples/exam-identity/test_store.py -q"""

import importlib
import os


def _fresh(tmp_path):
    os.environ["EXAM_DB"] = str(tmp_path / "e.db")
    import store
    importlib.reload(store)
    return store


def test_register_candidate_unique(tmp_path):
    s = _fresh(tmp_path)
    assert s.register_candidate("12345", "Ama", "MATH") is True
    assert s.register_candidate("12345", "Ama", "MATH") is False


def test_failed_verify_is_flagged(tmp_path):
    s = _fresh(tmp_path)
    s.register_candidate("12345", "Ama", "MATH")
    s.record_checkin("12345", "MATH", verified=True, score=0.8, now=1_000)
    s.record_checkin("12345", "MATH", verified=False, score=0.1, now=1_100)   # impersonation
    summ = s.summary("MATH")
    assert summ == {"verified": 1, "flagged": 1}
    assert [f["index_no"] for f in s.flagged("MATH")] == ["12345"]


def test_report_is_per_exam(tmp_path):
    s = _fresh(tmp_path)
    s.record_checkin("1", "MATH", False, 0.1, 1_000)
    s.record_checkin("2", "ENG", False, 0.1, 1_001)
    assert s.summary("MATH") == {"verified": 0, "flagged": 1}
    assert len(s.flagged("ENG")) == 1 and len(s.flagged("MATH")) == 1
    assert len(s.flagged()) == 2                       # no filter -> all
