"""Firmware registry: version compare, baseline, vulns, fleet rollup."""

from __future__ import annotations

import os

import pytest

from face_service import firmware as fw

T = "t_firmware_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_FIRMWARE_FILE"] = str(tmp_path / "fw.json")
    yield


def test_numeric_version_compare_not_lexical():
    fw.set_baseline(T, "readerX", "1.9.9")
    fw.report_version(T, "d1", "1.10.0", model="readerX")
    # 1.10.0 > 1.9.9 numerically even though lexically "1.10" < "1.9"
    assert not fw.check(T, "d1")["below_baseline"]


def test_below_baseline_flagged():
    fw.set_baseline(T, "readerX", "2.0.0")
    fw.report_version(T, "d1", "1.5.0", model="readerX")
    c = fw.check(T, "d1")
    assert c["below_baseline"] and not c["compliant"]


def test_vulnerable_version():
    fw.report_version(T, "d1", "1.2.3", model="readerX")
    fw.flag_vulnerable(T, "1.2.3", note="CVE-2026-0001", model="readerX")
    c = fw.check(T, "d1")
    assert c["vulnerable"] and c["vuln_note"] == "CVE-2026-0001"
    assert not c["compliant"]


def test_model_agnostic_vuln():
    fw.report_version(T, "d1", "0.9.0", model="readerY")
    fw.flag_vulnerable(T, "0.9.0")   # no model -> matches any model
    assert fw.check(T, "d1")["vulnerable"]


def test_no_baseline_is_compliant_on_that_axis():
    fw.report_version(T, "d1", "1.0.0", model="unknownmodel")
    assert fw.check(T, "d1")["compliant"]


def test_fleet_rollup():
    fw.set_baseline(T, "readerX", "2.0.0")
    fw.report_version(T, "good", "2.1.0", model="readerX")
    fw.report_version(T, "old", "1.0.0", model="readerX")
    fw.report_version(T, "vuln", "2.5.0", model="readerX")
    fw.flag_vulnerable(T, "2.5.0", model="readerX")
    fl = fw.fleet(T)
    assert fl["total"] == 3 and fl["non_compliant"] == 2
    assert {d["device"] for d in fl["devices"]} == {"old", "vuln"}


def test_unknown_device():
    assert not fw.check(T, "ghost")["exists"]


def test_validation():
    with pytest.raises(ValueError):
        fw.report_version(T, "", "1.0")
    with pytest.raises(ValueError):
        fw.set_baseline(T, "m", "")
    with pytest.raises(ValueError):
        fw.flag_vulnerable(T, "")
