"""Surveys: rating validation, aggregation, NPS, comments."""

from __future__ import annotations

import os

import pytest

from face_service import surveys

T = "t_surveys_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_SURVEYS_FILE"] = str(tmp_path / "surveys.json")
    yield


def test_aggregate_average_and_distribution():
    surveys.create(T, "csat", "How was enrolment?", scale=5)
    surveys.respond(T, "csat", "ama", 5)
    surveys.respond(T, "csat", "kofi", 3)
    surveys.respond(T, "csat", "esi", 4)
    s = surveys.summary(T, "csat")
    assert s["responses"] == 3 and s["average"] == 4.0
    assert s["distribution"] == {3: 1, 4: 1, 5: 1}


def test_one_response_per_subject():
    surveys.create(T, "csat", "q", scale=5)
    surveys.respond(T, "csat", "ama", 2)
    surveys.respond(T, "csat", "ama", 5)   # overwrites
    s = surveys.summary(T, "csat")
    assert s["responses"] == 1 and s["average"] == 5.0


def test_nps_scale():
    surveys.create(T, "nps", "Recommend us?", scale=10)
    for subj, r in [("a", 10), ("b", 9), ("c", 7), ("d", 3)]:
        surveys.respond(T, "nps", subj, r)
    # promoters: a,b (2); detractors: d (1); 4 total -> (2-1)/4*100 = 25
    assert surveys.nps(T, "nps") == 25.0


def test_nps_none_for_non_10_scale():
    surveys.create(T, "csat", "q", scale=5)
    surveys.respond(T, "csat", "a", 5)
    assert surveys.nps(T, "csat") is None


def test_comments():
    surveys.create(T, "csat", "q", scale=5)
    surveys.respond(T, "csat", "ama", 5, comment="great")
    surveys.respond(T, "csat", "kofi", 2)   # no comment
    c = surveys.comments(T, "csat")
    assert len(c) == 1 and c[0]["comment"] == "great"


def test_rating_out_of_range():
    surveys.create(T, "csat", "q", scale=5)
    with pytest.raises(ValueError):
        surveys.respond(T, "csat", "ama", 6)


def test_validation():
    with pytest.raises(ValueError):
        surveys.create(T, "", "q")
    with pytest.raises(ValueError):
        surveys.create(T, "x", "q", scale=1)
    assert not surveys.respond(T, "ghost", "ama", 5)["ok"]
