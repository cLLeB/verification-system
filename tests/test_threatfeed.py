"""Threat feed: indicators, TTL expiry, multi-source, gate, purge."""

from __future__ import annotations

import os

import pytest

from face_service import threatfeed as tf

T = "t_threatfeed_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_THREATFEED_FILE"] = str(tmp_path / "tf.json")
    yield


def test_add_and_check():
    tf.add(T, "ip", "1.2.3.4", source="partnerA")
    out = tf.check(T, "ip", "1.2.3.4")
    assert out["listed"] and out["sources"] == ["partnerA"]


def test_unlisted():
    assert not tf.check(T, "ip", "9.9.9.9")["listed"]


def test_ttl_expiry():
    tf.add(T, "device_fp", "abc", ttl=100, now=0)
    assert tf.check(T, "device_fp", "abc", now=50)["listed"]
    assert not tf.check(T, "device_fp", "abc", now=200)["listed"]


def test_multi_source_stays_hot_until_last_expires():
    tf.add(T, "ip", "1.2.3.4", source="a", ttl=100, now=0)
    tf.add(T, "ip", "1.2.3.4", source="b", ttl=500, now=0)
    # source a expired, b still active
    out = tf.check(T, "ip", "1.2.3.4", now=200)
    assert out["listed"] and out["sources"] == ["b"]


def test_bulk_add_skips_invalid():
    out = tf.bulk_add(T, [
        {"type": "ip", "value": "1.1.1.1"},
        {"type": "bogus", "value": "x"},     # invalid type -> skipped
        {"type": "subject", "value": "ama"},
    ])
    assert out["added"] == 2


def test_gate_blocks_on_hit():
    tf.add(T, "device_fp", "badfp")
    res = tf.gate(T, {"success": True, "code": "GRANTED"}, device_fp="badfp")
    assert not res["success"] and res["code"] == "THREAT_BLOCKED"
    assert res["threat"]["type"] == "device_fp"


def test_gate_passes_clean_context():
    res = tf.gate(T, {"success": True}, device_fp="goodfp", ip="1.2.3.4")
    assert res["success"]


def test_purge_expired():
    tf.add(T, "ip", "1.1.1.1", ttl=100, now=0)
    tf.add(T, "ip", "2.2.2.2", now=0)          # no ttl
    assert tf.purge_expired(T, now=200)["removed"] == 1
    assert tf.count(T, now=200) == 1


def test_validation():
    with pytest.raises(ValueError):
        tf.add(T, "bogus", "x")
    with pytest.raises(ValueError):
        tf.add(T, "ip", "")
