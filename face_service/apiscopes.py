"""Hierarchical API scopes for machine tokens.

RBAC roles ([[roles]]) answer "what may this *person* do"; API scopes answer "what
may this *token* do" — the OAuth-style least-privilege grant a caller stamps on a
key so a compromised integration token can't do everything. Scopes are dotted and
hierarchical (``verify.read``, ``enrol.write``, ``admin.*``) and a grant may use a
trailing ``*`` wildcard to cover a whole branch.

  * ``grant`` / ``revoke``  attach or remove scopes on a token id.
  * ``check``   does this token hold a scope satisfying ``required``? Honours
                wildcard grants (``verify.*`` satisfies ``verify.read``) and the
                super-grant ``*``.
  * ``scopes``  the effective scope set for a token.

Matching is deliberately one-directional: a *grant* may be a wildcard that covers a
concrete *required* scope, but a concrete grant never satisfies a wildcard request —
so ``check(tok, "verify.*")`` needs an actual wildcard grant, preventing accidental
privilege inflation.

Registry: ``apiscopes.json`` (env ``FACE_APISCOPES_FILE``).
"""

from __future__ import annotations

from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_APISCOPES_FILE", "apiscopes.json")


def _clean(scope: str) -> str:
    s = (scope or "").strip().lower()
    if not s:
        raise ValueError("a scope is required.")
    for part in s.split("."):
        if not part or (part != "*" and not part.replace("_", "").isalnum()):
            raise ValueError(f"invalid scope segment: {part!r}")
    return s


def grant(tenant: Optional[str], token: str, scopes: List[str]) -> List[str]:
    tok = (token or "").strip()
    if not tok:
        raise ValueError("token id is required.")
    add = {_clean(s) for s in (scopes or [])}
    if not add:
        raise ValueError("at least one scope is required.")
    with _reg.mutate() as data:
        t = data.setdefault(_reg.norm(tenant), {})
        cur = set(t.get(tok, []))
        t[tok] = sorted(cur | add)
    return sorted(add)


def revoke(tenant: Optional[str], token: str, scopes: List[str]) -> bool:
    tok = (token or "").strip()
    drop = {(s or "").strip().lower() for s in (scopes or [])}
    with _reg.mutate() as data:
        t = data.get(_reg.norm(tenant)) or {}
        if tok not in t:
            return False
        kept = [s for s in t[tok] if s not in drop]
        if kept:
            t[tok] = kept
        else:
            del t[tok]
    return True


def _grant_satisfies(granted: str, required: str) -> bool:
    if granted == "*":
        return True
    if granted == required:
        return True
    if granted.endswith(".*"):
        prefix = granted[:-1]          # keep trailing dot: "verify."
        return required.startswith(prefix)
    return False


def check(tenant: Optional[str], token: str, required: str) -> bool:
    req = (required or "").strip().lower()
    if not req:
        return False
    held = _reg.load().get(_reg.norm(tenant), {}).get((token or "").strip(), [])
    return any(_grant_satisfies(g, req) for g in held)


def scopes(tenant: Optional[str], token: str) -> List[str]:
    return list(_reg.load().get(_reg.norm(tenant), {}).get((token or "").strip(), []))
