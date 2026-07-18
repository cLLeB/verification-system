"""Risk engine: weighted scoring, capping, banding, assess."""

from __future__ import annotations

import os

import pytest

from face_service import riskengine as re

T = "t_riskengine_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_RISKENGINE_FILE"] = str(tmp_path / "re.json")
    yield


def _weights():
    re.set_weights(T, {
        "off-shift-access": 20,
        "access-while-on-leave": 30,
        "impossible-travel": 60,
        "new-device": 15,
    })


def test_score_sums_weights():
    _weights()
    out = re.score(T, ["off-shift-access", "new-device"])
    assert out["score"] == 35
    assert len(out["contributors"]) == 2


def test_score_capped_at_100():
    _weights()
    out = re.score(T, ["impossible-travel", "access-while-on-leave", "off-shift-access"])
    assert out["score"] == 100    # 60+30+20 = 110 -> capped


def test_unknown_signals_ignored():
    _weights()
    assert re.score(T, ["mystery-signal"])["score"] == 0.0


def test_classify_bands():
    re.set_weights(T, {"a": 50}, bands={"medium": 40, "high": 70})
    assert re.classify(T, 10) == "low"
    assert re.classify(T, 50) == "medium"
    assert re.classify(T, 80) == "high"


def test_assess_combines():
    _weights()
    a = re.assess(T, ["impossible-travel"])
    assert a["score"] == 60 and a["band"] == "medium"
    b = re.assess(T, ["impossible-travel", "access-while-on-leave"])
    assert b["score"] == 90 and b["band"] == "high"


def test_no_config_scores_zero():
    assert re.score(T, ["anything"])["score"] == 0.0
    assert re.classify(T, 100) == "high"   # default bands


def test_validation():
    with pytest.raises(ValueError):
        re.set_weights(T, {})
    with pytest.raises(ValueError):
        re.set_weights(T, {"a": -1})
    with pytest.raises(ValueError):
        re.set_weights(T, {"a": 10}, bands={"medium": 80, "high": 40})
