"""Notification preferences: defaults, channel/category toggles, opt-out."""

from __future__ import annotations

import os

import pytest

from face_service import notifyprefs as np

T = "t_notifyprefs_test"
S = "ama"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_NOTIFYPREFS_FILE"] = str(tmp_path / "np.json")
    yield


def test_default_allows_all_channels():
    assert np.channels_for(T, S, "alerts") == ["email", "sms", "push"]


def test_disable_channel():
    np.set_channel(T, S, "sms", enabled=False)
    assert not np.should_notify(T, S, "alerts", "sms")
    assert np.should_notify(T, S, "alerts", "email")


def test_category_override():
    np.set_category(T, S, "marketing", "email", enabled=False)
    assert not np.should_notify(T, S, "marketing", "email")
    assert np.should_notify(T, S, "alerts", "email")   # other category unaffected


def test_global_opt_out_wins():
    np.set_channel(T, S, "email", enabled=True)
    np.opt_out_all(T, S)
    assert np.channels_for(T, S, "alerts") == []


def test_opt_in_restores():
    np.opt_out_all(T, S)
    np.opt_in_all(T, S)
    assert np.should_notify(T, S, "alerts", "email")


def test_channel_disabled_beats_category_enabled():
    np.set_channel(T, S, "sms", enabled=False)
    np.set_category(T, S, "alerts", "sms", enabled=True)
    assert not np.should_notify(T, S, "alerts", "sms")


def test_invalid_channel():
    assert not np.should_notify(T, S, "alerts", "carrier-pigeon")


def test_validation():
    with pytest.raises(ValueError):
        np.set_channel(T, S, "fax", True)
    with pytest.raises(ValueError):
        np.set_category(T, S, "", "email", True)
