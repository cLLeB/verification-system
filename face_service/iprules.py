"""Per-tenant IP allow/deny rules for admin and API access.

Biometric verification says *who* is at the reader; it says nothing about *where*
an API call or admin login came from. Many deployments want a network guardrail
on top: "the admin console is only reachable from the office range" or "block this
abusive /24". This subsystem evaluates a client IP against an ordered rule list
and returns an allow/deny decision, supporting both IPv4 and IPv6 CIDR blocks.

  * ``add_rule``   append an allow/deny rule (single IP or CIDR) with a note.
  * ``check``      evaluate an IP: first matching rule wins; a default applies
                   when nothing matches (``allow`` unless the tenant is in
                   default-deny mode, i.e. any allow rule exists).
  * ``list_rules`` / ``remove`` - manage the rule set.

Default policy is deliberate: with no rules everything is allowed (opt-in), but as
soon as a tenant adds *any* allow rule the default flips to deny - the presence of
an allowlist means "only these", which is the least-surprise behaviour for an
allowlist. Explicit deny rules always take precedence by ordering.

Registry: ``iprules.json`` (env ``FACE_IPRULES_FILE``).
"""

from __future__ import annotations

import ipaddress
import uuid
from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_IPRULES_FILE", "iprules.json")


def _net(cidr: str):
    """Parse an IP or CIDR into a network; host bits are tolerated."""
    cidr = (cidr or "").strip()
    if not cidr:
        raise ValueError("an IP or CIDR is required.")
    if "/" not in cidr:
        return ipaddress.ip_network(cidr, strict=False)
    return ipaddress.ip_network(cidr, strict=False)


def add_rule(tenant: Optional[str], cidr: str, action: str = "allow",
             note: str = "") -> dict:
    action = (action or "").strip().lower()
    if action not in ("allow", "deny"):
        raise ValueError("action must be 'allow' or 'deny'.")
    net = _net(cidr)                       # validates
    rule = {"id": "ip_" + uuid.uuid4().hex[:8], "cidr": str(net),
            "action": action, "note": (note or "").strip()}
    with _reg.mutate() as data:
        data.setdefault(_reg.norm(tenant), []).append(rule)
    return rule


def _match(ip_obj, net_str: str) -> bool:
    try:
        net = ipaddress.ip_network(net_str, strict=False)
    except ValueError:
        return False
    return ip_obj.version == net.version and ip_obj in net


def check(tenant: Optional[str], ip: str) -> dict:
    try:
        ip_obj = ipaddress.ip_address((ip or "").strip())
    except ValueError:
        return {"allowed": False, "reason": "invalid-ip", "ip": ip}
    rules = _reg.load().get(_reg.norm(tenant)) or []
    for rule in rules:
        if _match(ip_obj, rule["cidr"]):
            return {"allowed": rule["action"] == "allow",
                    "reason": f"{rule['action']}-rule", "matched": rule["id"],
                    "cidr": rule["cidr"], "ip": str(ip_obj)}
    # no explicit match: default-deny once an allowlist exists, else default-allow
    has_allow = any(r["action"] == "allow" for r in rules)
    return {"allowed": not has_allow,
            "reason": "default-deny" if has_allow else "default-allow",
            "ip": str(ip_obj)}


def list_rules(tenant: Optional[str]) -> List[dict]:
    return list(_reg.load().get(_reg.norm(tenant)) or [])


def remove(tenant: Optional[str], rule_id: str) -> bool:
    rid = (rule_id or "").strip()
    with _reg.mutate() as data:
        t = _reg.norm(tenant)
        rules = data.get(t) or []
        kept = [r for r in rules if r["id"] != rid]
        if len(kept) == len(rules):
            return False
        data[t] = kept
    return True
