"""Punch-toggle logic for the attendance vertical (pure SQLite - no backbone/models).

    python -m pytest examples/attendance/test_store.py -q
"""

import importlib
import os


def _fresh_store(tmp_path):
    os.environ["ATTENDANCE_DB"] = str(tmp_path / "att.db")
    import store
    importlib.reload(store)          # rebind _DB to the temp path
    return store


def test_first_punch_is_in_then_toggles(tmp_path):
    store = _fresh_store(tmp_path)
    base = 1_000_000_000            # a fixed day
    assert store.record_punch("Kofi", base)["direction"] == "in"
    assert store.record_punch("Kofi", base + 3600)["direction"] == "out"
    assert store.record_punch("Kofi", base + 7200)["direction"] == "in"


def test_toggle_is_per_person(tmp_path):
    store = _fresh_store(tmp_path)
    base = 1_000_000_000
    store.record_punch("Kofi", base)
    assert store.record_punch("Ama", base + 60)["direction"] == "in"    # Ama's first
    assert store.record_punch("Kofi", base + 120)["direction"] == "out"  # Kofi's second


def test_new_day_resets_to_in(tmp_path):
    store = _fresh_store(tmp_path)
    day1 = 1_000_000_000
    store.record_punch("Kofi", day1)                                    # in
    store.record_punch("Kofi", day1 + 3600)                             # out
    assert store.record_punch("Kofi", day1 + 86_400)["direction"] == "in"  # next day -> in


def test_today_lists_only_todays_punches(tmp_path):
    store = _fresh_store(tmp_path)
    day1 = 1_000_000_000
    store.record_punch("Kofi", day1)
    store.record_punch("Kofi", day1 + 86_400)          # next day
    assert len(store.today(day1 + 86_400)) == 1        # only the new day's punch
