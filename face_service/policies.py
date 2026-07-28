"""Access policies - the authorization layer on top of biometric verification.

Verification answers *"who is this?"*; a policy answers *"and are they allowed
here, NOW?"*. Policies are evaluated strictly AFTER the biometric decision, so
the matching pipeline (thresholds, liveness, routing) is never touched - a
policy can only ever narrow an already-granted match, never widen one.

Per-tenant policy document (``policies.json``, env ``FACE_POLICIES_FILE``):

  * ``mode``     - ``off`` (default: nothing changes anywhere), ``advise``
                   (verify/identify responses gain an ``access`` block but the
                   decision is untouched), or ``enforce`` (a policy deny flips
                   the response to ``success=False, code=access_denied``).
  * ``default``  - ``allow`` | ``deny``: the outcome when no rule matches.
  * ``tz_offset_minutes`` - the tenant's local-time offset from UTC used for
                   schedule windows (kiosks and servers rarely share a zone).
  * ``groups``   - named user_id sets (``{"staff": ["ama", "kofi"]}``).
  * ``rules``    - ordered list; see ``upsert_rule``. DENY beats ALLOW.

Rule subjects: ``"*"`` (everyone), ``"user:<id>"``, ``"group:<name>"``.
Schedule: ``days`` (subset of mon..sun; empty = every day), ``start``/``end``
("HH:MM" local; absent = all day; start > end wraps midnight, e.g. a night
shift 22:00–06:00), optional ``valid_from``/``valid_until`` epochs.

Mirrors the storage/locking shape of [[keys]] and [[invites]] (JSON file,
process lock, env-overridable path, read-at-call so edits apply instantly).
"""

from __future__ import annotations

import json
import os
import secrets
import threading
import time
from typing import List, Optional

POLICIES_FILE = os.environ.get("FACE_POLICIES_FILE", "policies.json")

MODES = ("off", "advise", "enforce")
EFFECTS = ("allow", "deny")
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

_lock = threading.Lock()


def _load() -> dict:
    if not os.path.exists(POLICIES_FILE):
        return {}
    try:
        with open(POLICIES_FILE, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _save(data: dict) -> None:
    with open(POLICIES_FILE, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    try:
        os.chmod(POLICIES_FILE, 0o600)
    except OSError:
        pass


def _norm(tenant: Optional[str]) -> str:
    return (tenant or "default").strip() or "default"


def _blank() -> dict:
    return {"mode": "off", "default": "allow", "tz_offset_minutes": 0,
            "groups": {}, "rules": []}


def get(tenant: Optional[str]) -> dict:
    doc = _load().get(_norm(tenant)) or {}
    out = _blank()
    out["mode"] = doc.get("mode") if doc.get("mode") in MODES else "off"
    out["default"] = doc.get("default") if doc.get("default") in EFFECTS else "allow"
    try:
        out["tz_offset_minutes"] = int(doc.get("tz_offset_minutes", 0))
    except (TypeError, ValueError):
        out["tz_offset_minutes"] = 0
    groups = doc.get("groups") or {}
    out["groups"] = {str(k): [str(u) for u in v] for k, v in groups.items()
                     if isinstance(v, list)}
    out["rules"] = [r for r in (doc.get("rules") or []) if isinstance(r, dict)]
    return out


def configure(tenant: Optional[str], mode=None, default=None,
              tz_offset_minutes=None) -> dict:
    """Set the tenant's policy mode / default outcome / schedule timezone."""
    t = _norm(tenant)
    with _lock:
        data = _load()
        doc = data.setdefault(t, _blank())
        if mode is not None and str(mode).lower() in MODES:
            doc["mode"] = str(mode).lower()
        if default is not None and str(default).lower() in EFFECTS:
            doc["default"] = str(default).lower()
        if tz_offset_minutes is not None:
            try:
                doc["tz_offset_minutes"] = max(-840, min(840, int(tz_offset_minutes)))
            except (TypeError, ValueError):
                pass
        _save(data)
    return get(t)


def set_group(tenant: Optional[str], name: str, members: List[str]) -> dict:
    name = (name or "").strip()
    if not name:
        raise ValueError("A group name is required.")
    t = _norm(tenant)
    with _lock:
        data = _load()
        doc = data.setdefault(t, _blank())
        doc.setdefault("groups", {})[name] = sorted(
            {str(m).strip() for m in (members or []) if str(m).strip()})
        _save(data)
    return get(t)


def delete_group(tenant: Optional[str], name: str) -> bool:
    t = _norm(tenant)
    with _lock:
        data = _load()
        groups = (data.get(t) or {}).get("groups") or {}
        if name not in groups:
            return False
        del groups[name]
        _save(data)
    return True


def _clean_rule(rule: dict) -> dict:
    """Validate + normalise a rule body. Raises ValueError on nonsense so a bad
    admin request can never silently create an unevaluable rule."""
    out = {
        "rule_id": rule.get("rule_id") or ("pr_" + secrets.token_hex(5)),
        "name": str(rule.get("name") or "").strip() or "Unnamed rule",
        "effect": str(rule.get("effect") or "allow").lower(),
        "subjects": [],
        "days": [],
        "start": None,
        "end": None,
        "valid_from": None,
        "valid_until": None,
        "enabled": bool(rule.get("enabled", True)),
    }
    if out["effect"] not in EFFECTS:
        raise ValueError("effect must be 'allow' or 'deny'.")
    subs = rule.get("subjects")
    if isinstance(subs, str):
        subs = [s.strip() for s in subs.split(",") if s.strip()]
    for s in subs or ["*"]:
        s = str(s).strip()
        if s == "*" or s.startswith("user:") or s.startswith("group:"):
            out["subjects"].append(s)
        else:                                   # bare name -> a user subject
            out["subjects"].append(f"user:{s}")
    days = rule.get("days")
    if isinstance(days, str):
        days = [d.strip() for d in days.split(",") if d.strip()]
    for d in days or []:
        d = str(d).lower()[:3]
        if d in DAYS and d not in out["days"]:
            out["days"].append(d)
    for key in ("start", "end"):
        v = rule.get(key)
        if v in (None, ""):
            continue
        v = str(v).strip()
        parts = v.split(":")
        if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit() \
                or not (0 <= int(parts[0]) <= 23 and 0 <= int(parts[1]) <= 59):
            raise ValueError(f"{key} must be 'HH:MM' (24h).")
        out[key] = f"{int(parts[0]):02d}:{int(parts[1]):02d}"
    if (out["start"] is None) != (out["end"] is None):
        raise ValueError("Provide both 'start' and 'end', or neither.")
    for key in ("valid_from", "valid_until"):
        v = rule.get(key)
        if v in (None, ""):
            continue
        try:
            out[key] = int(v)
        except (TypeError, ValueError):
            raise ValueError(f"{key} must be an epoch integer.")
    return out


def upsert_rule(tenant: Optional[str], rule: dict) -> dict:
    """Add a rule, or replace the one with the same ``rule_id``. Returns the
    stored (normalised) rule."""
    clean = _clean_rule(rule or {})
    t = _norm(tenant)
    with _lock:
        data = _load()
        doc = data.setdefault(t, _blank())
        rules = doc.setdefault("rules", [])
        for i, r in enumerate(rules):
            if r.get("rule_id") == clean["rule_id"]:
                rules[i] = clean
                break
        else:
            rules.append(clean)
        _save(data)
    return clean


def delete_rule(tenant: Optional[str], rule_id: str) -> bool:
    t = _norm(tenant)
    with _lock:
        data = _load()
        rules = (data.get(t) or {}).get("rules") or []
        keep = [r for r in rules if r.get("rule_id") != rule_id]
        if len(keep) == len(rules):
            return False
        data[t]["rules"] = keep
        _save(data)
    return True


# --- evaluation ---------------------------------------------------------------
def _subject_match(rule: dict, user_id: str, groups: dict) -> bool:
    for s in rule.get("subjects") or []:
        if s == "*":
            return True
        if s.startswith("user:") and s[5:] == user_id:
            return True
        if s.startswith("group:") and user_id in (groups.get(s[6:]) or []):
            return True
    return False


def _time_match(rule: dict, now: float, tz_offset_minutes: int) -> bool:
    vf, vu = rule.get("valid_from"), rule.get("valid_until")
    if vf is not None and now < vf:
        return False
    if vu is not None and now > vu:
        return False
    local = time.gmtime(now + tz_offset_minutes * 60)
    if rule.get("days"):
        # tm_wday: Monday = 0 - aligns with DAYS ordering.
        if DAYS[local.tm_wday] not in rule["days"]:
            return False
    start, end = rule.get("start"), rule.get("end")
    if start and end:
        minutes = local.tm_hour * 60 + local.tm_min
        s = int(start[:2]) * 60 + int(start[3:])
        e = int(end[:2]) * 60 + int(end[3:])
        if s <= e:                                  # same-day window
            if not (s <= minutes <= e):
                return False
        else:                                       # wraps midnight (night shift)
            if not (minutes >= s or minutes <= e):
                return False
    return True


def evaluate(tenant: Optional[str], user_id: str,
             now: Optional[float] = None) -> dict:
    """Policy decision for one matched user. Pure and side-effect free.
    Returns {mode, allowed, matched_rule, rule_name, reason}."""
    doc = get(tenant)
    if doc["mode"] == "off":
        return {"mode": "off", "allowed": True, "matched_rule": None,
                "rule_name": None, "reason": "policies off"}
    now = time.time() if now is None else float(now)
    uid = (user_id or "").strip()
    tz = doc["tz_offset_minutes"]
    allow_hit = None
    for rule in doc["rules"]:
        if not rule.get("enabled", True):
            continue
        if not _subject_match(rule, uid, doc["groups"]):
            continue
        if not _time_match(rule, now, tz):
            continue
        if rule["effect"] == "deny":                # deny wins immediately
            return {"mode": doc["mode"], "allowed": False,
                    "matched_rule": rule["rule_id"], "rule_name": rule["name"],
                    "reason": f"denied by rule '{rule['name']}'"}
        if allow_hit is None:
            allow_hit = rule
    if allow_hit is not None:
        return {"mode": doc["mode"], "allowed": True,
                "matched_rule": allow_hit["rule_id"],
                "rule_name": allow_hit["name"],
                "reason": f"allowed by rule '{allow_hit['name']}'"}
    allowed = doc["default"] == "allow"
    return {"mode": doc["mode"], "allowed": allowed, "matched_rule": None,
            "rule_name": None,
            "reason": f"no rule matched - default {doc['default']}"}


def apply(tenant: Optional[str], result: dict,
          now: Optional[float] = None) -> dict:
    """Fold the policy decision into a verify/identify RESULT dict (mutates and
    returns it). Called strictly after the biometric decision:

      * mode off             -> untouched (byte-for-byte legacy behaviour).
      * no biometric grant   -> untouched (policies only narrow granted matches).
      * advise               -> adds ``access`` (decision unchanged).
      * enforce + deny       -> success=False, code=access_denied, ``access``
                                carries the who/why so integrators can message it.
    """
    if not result.get("success") or not result.get("user_id"):
        return result
    decision = evaluate(tenant, result["user_id"], now=now)
    if decision["mode"] == "off":
        return result
    result["access"] = {k: decision[k] for k in
                        ("allowed", "matched_rule", "rule_name", "reason", "mode")}
    if decision["mode"] == "enforce" and not decision["allowed"]:
        result["success"] = False
        result["code"] = "access_denied"
        result["message"] = ("Identity confirmed, but access is not permitted "
                             "right now: " + decision["reason"])
    return result


def remove_tenant(tenant: Optional[str]) -> bool:
    """Offboarding: drop the tenant's policy document."""
    t = _norm(tenant)
    with _lock:
        data = _load()
        if t not in data:
            return False
        del data[t]
        _save(data)
    return True
