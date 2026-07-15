"""Policy pipeline — run many gates in a configured order, as one decision.

This package now has a whole family of post-match gates (consent, watchlist,
schedules, geofence, risk, cooldown, and many more), each a function that takes a
verify result and returns it, possibly flipping ``success``. In production a
deployment wants to apply *several* of them, in a defined order, and stop at the
first hard denial. This subsystem is that orchestrator.

Gates register themselves by name as small adapters — ``fn(tenant, result,
context) -> result`` — so the pipeline stays decoupled from every module's exact
signature (the adapter pulls what it needs out of ``context``). A tenant then
configures which steps run and in what order. ``apply`` walks them:

  * each enabled step runs in order;
  * if a step sets ``success`` False, the chain short-circuits (a denied verify
    should not keep being evaluated) unless the tenant marks it ``advisory``;
  * the trace of which steps ran (and which denied) rides on the result.

This is what turns twenty independent gates into one coherent access policy.

Registry: ``pipeline.json`` (env ``FACE_PIPELINE_FILE``) — per-tenant step order.
The step *implementations* live in a process-local registry populated by
``register`` at startup, never persisted.
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional

from ._registry import Registry

_reg = Registry("FACE_PIPELINE_FILE", "pipeline.json")

# process-local: name -> adapter(tenant, result, context) -> result
_STEPS: Dict[str, Callable] = {}


def register(name: str, fn: Callable) -> None:
    """Register a gate adapter under a name. Idempotent (last wins)."""
    name = (name or "").strip()
    if not name or not callable(fn):
        raise ValueError("register needs a name and a callable.")
    _STEPS[name] = fn


def registered() -> List[str]:
    return sorted(_STEPS)


def set_pipeline(tenant: Optional[str], steps: List[str],
                 advisory: Optional[List[str]] = None) -> dict:
    """Set the ordered enabled steps for a tenant. Unknown step names are kept
    (a step may be registered later) but reported."""
    steps = [s.strip() for s in steps if s and s.strip()]
    advisory = [s.strip() for s in (advisory or []) if s and s.strip()]
    with _reg.mutate() as data:
        data[_reg.norm(tenant)] = {"steps": steps, "advisory": advisory}
    return {"steps": steps, "advisory": advisory,
            "unregistered": [s for s in steps if s not in _STEPS]}


def get_pipeline(tenant: Optional[str]) -> dict:
    doc = _reg.load().get(_reg.norm(tenant)) or {}
    return {"steps": list(doc.get("steps") or []),
            "advisory": list(doc.get("advisory") or [])}


def apply(tenant: Optional[str], result: dict,
          context: Optional[dict] = None) -> dict:
    """Run the tenant's configured pipeline over a verify RESULT. Mutates and
    returns it; attaches ``pipeline_trace`` and, on denial, ``denied_by``."""
    context = context or {}
    cfg = get_pipeline(tenant)
    advisory = set(cfg["advisory"])
    trace: List[str] = []
    for name in cfg["steps"]:
        fn = _STEPS.get(name)
        if fn is None:
            continue
        before = result.get("success", True)
        result = fn(tenant, result, context) or result
        trace.append(name)
        if before and not result.get("success", True):
            # this step denied
            if name in advisory:
                # advisory step: note it but let the verify continue
                result["success"] = True
                result.setdefault("advisories", []).append(
                    {"step": name, "code": result.get("code")})
                result.pop("code", None)
                result.pop("message", None)
            else:
                result["denied_by"] = name
                result["pipeline_trace"] = trace
                return result
    result["pipeline_trace"] = trace
    return result


def clear(tenant: Optional[str]) -> None:
    t = _reg.norm(tenant)
    with _reg.mutate() as data:
        data.pop(t, None)
