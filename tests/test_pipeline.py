"""Policy pipeline: ordered gate composition with short-circuit + advisory."""

from __future__ import annotations

import os

import pytest

from face_service import pipeline

T = "t_pipeline_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_PIPELINE_FILE"] = str(tmp_path / "pipeline.json")
    # reset process-local step registry between tests
    pipeline._STEPS.clear()
    yield


def _deny_step(code):
    def fn(tenant, result, context):
        result["success"] = False
        result["code"] = code
        return result
    return fn


def _pass_step(tag):
    def fn(tenant, result, context):
        result.setdefault("passed", []).append(tag)
        return result
    return fn


def test_runs_in_order():
    pipeline.register("a", _pass_step("a"))
    pipeline.register("b", _pass_step("b"))
    pipeline.set_pipeline(T, ["a", "b"])
    out = pipeline.apply(T, {"success": True})
    assert out["passed"] == ["a", "b"] and out["pipeline_trace"] == ["a", "b"]


def test_short_circuits_on_denial():
    pipeline.register("watch", _deny_step("watchlisted"))
    pipeline.register("after", _pass_step("after"))
    pipeline.set_pipeline(T, ["watch", "after"])
    out = pipeline.apply(T, {"success": True})
    assert out["success"] is False and out["denied_by"] == "watch"
    assert out["pipeline_trace"] == ["watch"]     # "after" never ran


def test_advisory_step_does_not_block():
    pipeline.register("risk", _deny_step("high_risk"))
    pipeline.register("after", _pass_step("after"))
    pipeline.set_pipeline(T, ["risk", "after"], advisory=["risk"])
    out = pipeline.apply(T, {"success": True})
    assert out["success"] is True
    assert out["advisories"][0]["step"] == "risk"
    assert out["passed"] == ["after"]             # chain continued


def test_unregistered_steps_skipped_and_reported():
    pipeline.register("a", _pass_step("a"))
    res = pipeline.set_pipeline(T, ["a", "ghost"])
    assert res["unregistered"] == ["ghost"]
    out = pipeline.apply(T, {"success": True})
    assert out["pipeline_trace"] == ["a"]         # ghost skipped at runtime


def test_context_is_passed():
    seen = {}

    def fn(tenant, result, context):
        seen.update(context)
        return result
    pipeline.register("cap", fn)
    pipeline.set_pipeline(T, ["cap"])
    pipeline.apply(T, {"success": True}, context={"lat": 5.6, "scope": "vault"})
    assert seen["scope"] == "vault"


def test_validation():
    with pytest.raises(ValueError):
        pipeline.register("", None)
