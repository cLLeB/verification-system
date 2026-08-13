"""Mount the service subsystems as a uniform API surface.

The package accumulated ~166 subsystems - lockers, occupancy, shifts, geofencing,
watchlists, anti-passback, escorts, budgets, and so on - that were written and tested
but never given a route. They were real code with no way to reach it.

Hand-writing 851 endpoint functions for them would be 851 chances to forget an auth
check or leak a tenant. Instead this mounts them generically, because they already
share one shape: a module-level ``Registry`` for storage, and public functions whose
first parameter is ``tenant``. That regularity is what makes a single, well-tested
layer safe enough to expose all of them at once.

    GET  /v1/services                      catalogue: every subsystem and what it does
    GET  /v1/services/<module>             one subsystem's functions and their arguments
    POST /v1/services/<module>/<function>  call it; the JSON body supplies the arguments

Design rules, each of which exists to close a specific hole:

* **The tenant is never an argument.** It is taken from the authenticated key and
  injected. A body that tries to pass ``tenant`` is rejected outright rather than
  ignored, so a caller can never read or write another tenant's data by guessing a
  parameter name - the single most dangerous thing a generic dispatcher could do.
* **Allow-list, not deny-list.** Only functions discovered on an explicitly mounted
  module are callable, and only if public. Nothing reaches ``os``, ``subprocess`` or
  any import, because dispatch resolves names inside one module's namespace only.
* **Everything requires the ``manage`` scope**, i.e. an admin key. These are
  configuration surfaces, not the verification path a kiosk key should touch.
* **The verification pipeline is untouched.** 44 of these modules expose a ``gate()``
  that could narrow a match, but none is chained into verify here. Wiring a gate into
  live matching changes who gets through a door, so it stays a deliberate, separate
  act - see ``gate_preview`` below, which lets you see what a gate WOULD decide
  without it deciding anything.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
from typing import Any, Dict, List, Optional

from flask import Blueprint, g, jsonify, request

from .auth import require_scope

bp = Blueprint("services", __name__, url_prefix="/v1/services")

# Subsystems that are reached through their own dedicated, hand-written endpoints
# (or are infrastructure rather than a feature). Mounting them here too would give
# one capability two doors with different validation, which is how they drift apart.
_NOT_MOUNTED = {
    "v1", "admin", "portal", "auth", "security", "keys", "admins", "tenants",
    "audit", "usage", "webhooks", "credentials", "issuer_keys", "invites",
    "devices", "guests", "guardians", "consent", "policies", "modality",
    "bundle", "glance", "fielddata", "persistence", "metrics", "idempotency",
    "linkgate", "services", "_registry", "__init__",
}

_CACHE: Optional[Dict[str, Any]] = None


def _describe(fn) -> dict:
    """Public shape of one function: what it is called with, and what it is for."""
    sig = inspect.signature(fn)
    params = []
    for name, p in sig.parameters.items():
        if name == "tenant":
            continue                                  # injected, never accepted
        if p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
            params.append({"name": name, "variadic": True, "required": False})
            continue
        params.append({
            "name": name,
            "required": p.default is inspect.Parameter.empty,
            "default": None if p.default is inspect.Parameter.empty else _jsonable(p.default),
        })
    doc = (inspect.getdoc(fn) or "").strip().splitlines()
    return {"params": params, "summary": doc[0] if doc else "",
            "takes_tenant": "tenant" in sig.parameters}


def _jsonable(v):
    return v if isinstance(v, (str, int, float, bool, type(None), list, dict)) else str(v)


def catalogue() -> Dict[str, Any]:
    """Discover every mountable subsystem once, then serve it from memory."""
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    import face_service

    found: Dict[str, Any] = {}
    for mod in pkgutil.iter_modules(face_service.__path__):
        name = mod.name
        if name in _NOT_MOUNTED or name.startswith("_"):
            continue
        try:
            m = importlib.import_module(f"face_service.{name}")
        except Exception:                              # a broken module must not
            continue                                   # take the whole catalogue down
        fns = {}
        for fname, fn in inspect.getmembers(m, inspect.isfunction):
            if fname.startswith("_"):
                continue
            if getattr(fn, "__module__", "") != m.__name__:
                continue                               # re-exported import, not its own
            try:
                fns[fname] = _describe(fn)
            except (ValueError, TypeError):
                continue
        if not fns:
            continue
        doc = (inspect.getdoc(m) or "").strip().splitlines()
        found[name] = {
            "summary": doc[0] if doc else "",
            "functions": fns,
            "has_gate": "gate" in fns,
        }
    _CACHE = found
    return found


def _resolve(module: str, function: str):
    cat = catalogue()
    if module not in cat:
        return None, ("no_such_service", f"No service subsystem named '{module}'.")
    if function not in cat[module]["functions"]:
        return None, ("no_such_function",
                      f"'{module}' has no callable '{function}'.")
    m = importlib.import_module(f"face_service.{module}")
    return getattr(m, function), None


@bp.get("")
@require_scope("manage")
def services_index():
    """Every mounted subsystem, with a one-line summary of what it does."""
    cat = catalogue()
    gated = request.args.get("gates") == "1"
    out = {name: {"summary": info["summary"],
                  "functions": sorted(info["functions"]),
                  "has_gate": info["has_gate"]}
           for name, info in sorted(cat.items())
           if not gated or info["has_gate"]}
    return jsonify({"success": True, "count": len(out), "services": out})


@bp.get("/<module>")
@require_scope("manage")
def service_detail(module):
    cat = catalogue()
    if module not in cat:
        return jsonify({"success": False, "code": "no_such_service",
                        "message": f"No service subsystem named '{module}'."}), 404
    return jsonify({"success": True, "service": module, **cat[module]})


@bp.post("/<module>/<function>")
@require_scope("manage")
def service_call(module, function):
    fn, err = _resolve(module, function)
    if err:
        return jsonify({"success": False, "code": err[0], "message": err[1]}), 404

    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return jsonify({"success": False, "code": "bad_request",
                        "message": "The body must be a JSON object of arguments."}), 400
    if "tenant" in body:
        # Refused, not ignored: a caller who thinks they are choosing the tenant
        # should be told they are not, rather than silently writing to their own.
        return jsonify({"success": False, "code": "tenant_not_allowed",
                        "message": "'tenant' is taken from your API key and cannot "
                                   "be passed."}), 400

    kwargs = dict(body)
    sig = inspect.signature(fn)
    if "tenant" in sig.parameters:
        kwargs["tenant"] = g.tenant

    unexpected = [k for k in kwargs
                  if k not in sig.parameters
                  and not any(p.kind == p.VAR_KEYWORD for p in sig.parameters.values())]
    if unexpected:
        return jsonify({"success": False, "code": "unexpected_argument",
                        "message": f"Unknown argument(s): {', '.join(sorted(unexpected))}. "
                                   f"GET /v1/services/{module} lists what this takes."}), 400
    try:
        result = fn(**kwargs)
    except TypeError as exc:                           # wrong/missing arguments
        return jsonify({"success": False, "code": "bad_arguments",
                        "message": str(exc)}), 400
    except (ValueError, KeyError) as exc:              # the subsystem's own rejection
        return jsonify({"success": False, "code": "rejected",
                        "message": str(exc)}), 400
    return jsonify({"success": True, "service": module, "function": function,
                    "result": _jsonable_deep(result)})


def _jsonable_deep(v):
    if isinstance(v, dict):
        return {str(k): _jsonable_deep(x) for k, x in v.items()}
    if isinstance(v, (list, tuple, set)):
        return [_jsonable_deep(x) for x in v]
    return _jsonable(v)


@bp.post("/<module>/gate_preview")
@require_scope("manage")
def gate_preview(module):
    """What WOULD this gate decide, without it deciding anything.

    44 subsystems expose a ``gate(tenant, result, ...)`` that can turn a granted
    match into a refusal. None of them is wired into live verification, because
    doing that changes who gets through a door and must be a deliberate act. This
    lets you rehearse one against a hypothetical result first: pass the match you
    expect, see what the gate would return, and only then decide whether to adopt it.
    """
    cat = catalogue()
    if module not in cat or not cat[module]["has_gate"]:
        return jsonify({"success": False, "code": "no_such_gate",
                        "message": f"'{module}' does not expose a gate."}), 404
    body = request.get_json(silent=True) or {}
    result = body.pop("result", None) or {"success": True,
                                          "user_id": body.get("user_id") or "someone"}
    body.pop("tenant", None)
    m = importlib.import_module(f"face_service.{module}")
    try:
        decided = m.gate(tenant=g.tenant, result=dict(result), **body)
    except TypeError:
        try:
            decided = m.gate(g.tenant, dict(result))
        except Exception as exc:
            return jsonify({"success": False, "code": "bad_arguments",
                            "message": str(exc)}), 400
    except (ValueError, KeyError) as exc:
        return jsonify({"success": False, "code": "rejected", "message": str(exc)}), 400
    return jsonify({"success": True, "service": module, "before": result,
                    "after": _jsonable_deep(decided),
                    "would_change": bool(result.get("success")) != bool(
                        (decided or {}).get("success"))})
