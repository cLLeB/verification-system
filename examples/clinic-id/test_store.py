"""Clinic record logic (pure SQLite). python -m pytest examples/clinic-id/test_store.py -q"""

import importlib
import os


def _fresh(tmp_path):
    os.environ["CLINIC_DB"] = str(tmp_path / "c.db")
    import store
    importlib.reload(store)
    return store


def test_register_patient_unique(tmp_path):
    s = _fresh(tmp_path)
    assert s.register_patient("MRN001", "Ama", 1_000) is True
    assert s.register_patient("MRN001", "Ama", 1_100) is False
    assert s.patient("MRN001")["name"] == "Ama"
    assert s.patient("MRN999") is None


def test_history_is_most_recent_first(tmp_path):
    s = _fresh(tmp_path)
    s.register_patient("MRN001", "Ama", 1_000)
    s.add_visit("MRN001", "malaria", 1_100)
    s.add_visit("MRN001", "follow-up", 1_200)
    hist = s.history("MRN001")
    assert [h["note"] for h in hist] == ["follow-up", "malaria"]     # newest first


def test_history_is_per_patient(tmp_path):
    s = _fresh(tmp_path)
    s.register_patient("A", "Ama", 1_000)
    s.register_patient("B", "Kofi", 1_000)
    s.add_visit("A", "x", 1_100)
    assert len(s.history("A")) == 1 and s.history("B") == []
