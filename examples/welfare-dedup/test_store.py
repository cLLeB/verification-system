"""Welfare ledger logic (pure SQLite). python -m pytest examples/welfare-dedup/test_store.py -q"""

import importlib
import os


def _fresh(tmp_path):
    os.environ["WELFARE_DB"] = str(tmp_path / "w.db")
    import store
    importlib.reload(store)
    return store


def test_register_is_unique_by_name(tmp_path):
    s = _fresh(tmp_path)
    assert s.register("Ama", "cash", 1_000) is True
    assert s.register("Ama", "cash", 1_100) is False        # already registered
    assert s.is_registered("Ama") and not s.is_registered("Kofi")


def test_payouts_accumulate(tmp_path):
    s = _fresh(tmp_path)
    s.register("Ama", "cash", 1_000)
    s.record_payout("Ama", 50.0, 1_100)
    s.record_payout("Ama", 50.0, 1_200)
    summ = s.summary()
    assert summ["beneficiaries"] == 1 and summ["payouts"] == 2 and summ["total_paid"] == 100.0


def test_beneficiaries_listed_in_registration_order(tmp_path):
    s = _fresh(tmp_path)
    s.register("Ama", "cash", 1_000)
    s.register("Kofi", "cash", 1_001)
    assert [b["name"] for b in s.beneficiaries()] == ["Ama", "Kofi"]
