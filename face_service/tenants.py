"""Per-tenant settings: allowed browser origins (CORS) + an optional webhook.

Lets each integrating customer:
  * register the web origins their browser app calls from (so the API can be used
    directly from any web app, securely — combined with the API key), and
  * receive signed event callbacks (enroll / verify / identify) at a URL of theirs.

Stored as JSON (``tenants.json``). Read at request time, so changes take effect
without a restart.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading

TENANTS_FILE = os.environ.get("FACE_TENANTS_FILE", "tenants.json")
DEFAULT_EVENTS = ["enroll", "verify", "identify"]
_lock = threading.Lock()


def _load() -> dict:
    if not os.path.exists(TENANTS_FILE):
        return {}
    try:
        with open(TENANTS_FILE, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _save(data: dict) -> None:
    with open(TENANTS_FILE, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    try:
        os.chmod(TENANTS_FILE, 0o600)
    except OSError:
        pass


# Entitlement defaults. A tenant with no record is fully enabled + unlimited, so
# existing keys keep working; admins tighten/relax per tenant. `enabled=False` is the
# paywall/offboarding gate enforced on every API call.
_DEFAULT_ENABLED = True
_DEFAULT_PLAN = "standard"
_DEFAULT_MAX_KEYS = 0                     # 0 = unlimited
_DEFAULT_ROLES = ["admin", "verify"]
_DEFAULT_ALLOW_EXPORT = False             # template (embedding) export off unless opted in


def get(tenant: str) -> dict:
    rec = _load().get(tenant) or {}
    return {"tenant": tenant,
            "cors_origins": rec.get("cors_origins", []),
            "webhook_url": rec.get("webhook_url", ""),
            "webhook_secret": rec.get("webhook_secret", ""),
            "events": rec.get("events", DEFAULT_EVENTS),
            "enabled": rec.get("enabled", _DEFAULT_ENABLED),
            "plan": rec.get("plan", _DEFAULT_PLAN),
            "max_keys": int(rec.get("max_keys", _DEFAULT_MAX_KEYS)),
            "allowed_roles": rec.get("allowed_roles", list(_DEFAULT_ROLES)),
            "allow_export": rec.get("allow_export", _DEFAULT_ALLOW_EXPORT)}


def entitlement(tenant: str) -> dict:
    """Just the access-control fields (enabled / plan / max_keys / allowed_roles / export)."""
    t = get(tenant)
    return {"tenant": tenant, "enabled": t["enabled"], "plan": t["plan"],
            "max_keys": t["max_keys"], "allowed_roles": t["allowed_roles"],
            "allow_export": t["allow_export"]}


def is_enabled(tenant: str) -> bool:
    return bool(get(tenant)["enabled"])


def set_entitlement(tenant: str, enabled=None, plan=None, max_keys=None,
                    allowed_roles=None, allow_export=None) -> dict:
    """Admin sets a tenant's 'green light' + constraints. The paywall hook: flip
    ``enabled`` (or a future billing check) to gate all API access instantly.
    ``allow_export`` opts the tenant into letting devices pull templates for offline sync."""
    with _lock:
        data = _load()
        rec = data.setdefault(tenant, {})
        if enabled is not None:
            rec["enabled"] = bool(enabled)
        if plan is not None:
            rec["plan"] = str(plan).strip() or _DEFAULT_PLAN
        if max_keys is not None:
            rec["max_keys"] = max(0, int(max_keys))
        if allowed_roles is not None:
            rec["allowed_roles"] = [r for r in allowed_roles if r in ("admin", "verify")] or list(_DEFAULT_ROLES)
        if allow_export is not None:
            rec["allow_export"] = bool(allow_export)
        _save(data)
    return entitlement(tenant)


def can_mint(tenant: str, roles: list, adding: int, current_count: int):
    """Policy shared by the admin console and the tenant self-service portal: may
    this tenant be granted ``adding`` more keys of ``roles``? Returns (ok, message).
    (Does not check ``enabled`` — callers decide if a disabled tenant may still mint;
    the portal blocks it, admin provisioning does not.)"""
    if not tenant:
        return True, ""
    ent = entitlement(tenant)
    for r in roles:
        if r not in ent["allowed_roles"]:
            return False, (f"Tenant '{tenant}' is not permitted to hold '{r}' keys "
                           f"(allowed: {', '.join(ent['allowed_roles'])}).")
    if ent["max_keys"] and current_count + adding > ent["max_keys"]:
        return False, (f"Key limit reached ({ent['max_keys']}). "
                       f"Revoke unused keys or ask the provider to raise the limit.")
    return True, ""


# --- tenant self-service portal login (separate from the platform admin) -----
_PW_ITER = 200_000


def _pw_hash(pw: str, salt: bytes = None) -> str:
    salt = salt or os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), salt, _PW_ITER)
    return base64.b64encode(salt).decode() + ":" + base64.b64encode(dk).decode()


def _pw_check(pw: str, stored: str) -> bool:
    try:
        s, d = stored.split(":")
        dk = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), base64.b64decode(s), _PW_ITER)
        return hmac.compare_digest(dk, base64.b64decode(d))
    except Exception:
        return False


def set_portal_password(tenant: str, password: str) -> bool:
    """Admin sets/resets a tenant's portal login password (PBKDF2-hashed)."""
    if not tenant or not password:
        return False
    with _lock:
        data = _load()
        data.setdefault(tenant, {})["portal_pw"] = _pw_hash(password)
        _save(data)
    return True


def has_portal_login(tenant: str) -> bool:
    return bool((_load().get(tenant) or {}).get("portal_pw"))


def check_portal_password(tenant: str, password: str) -> bool:
    rec = _load().get((tenant or "").strip()) or {}
    h = rec.get("portal_pw")
    return bool(h) and _pw_check(password, h)


def remove(tenant: str) -> bool:
    """Drop a tenant's settings record entirely (used on offboarding)."""
    with _lock:
        data = _load()
        if tenant in data:
            del data[tenant]
            _save(data)
            return True
    return False


def set_settings(tenant: str, cors_origins=None, webhook_url=None, events=None) -> dict:
    with _lock:
        data = _load()
        rec = data.setdefault(tenant, {})
        if cors_origins is not None:
            rec["cors_origins"] = [o.strip() for o in cors_origins if o.strip()]
        if webhook_url is not None:
            rec["webhook_url"] = webhook_url.strip()
            if rec["webhook_url"] and not rec.get("webhook_secret"):
                rec["webhook_secret"] = "whsec_" + secrets.token_urlsafe(24)
        if events is not None:
            rec["events"] = [e for e in events if e in DEFAULT_EVENTS]
        _save(data)
    return get(tenant)


def all_settings() -> list:
    return [get(t) for t in sorted(_load())]


def all_cors_origins() -> set:
    out = set()
    for rec in _load().values():
        out.update(rec.get("cors_origins", []))
    return out
