"""Localized message templates with safe variable substitution.

Notifications, alerts and receipts are sent to people in different languages and want
consistent, editable wording rather than strings hard-coded across the app. This
subsystem is a small template store: register a template per (key, locale), then
render it with variables, resolving the best locale via a fallback chain
(``fr-CA`` → ``fr`` → default). Substitution is deliberately restricted to named
``{placeholders}`` — no format specifiers, no attribute access, no code — so a
template authored by a tenant admin can never do more than fill in blanks.

  * ``set_template``   store/replace a template body for a (key, locale).
  * ``render``         render a key in a requested locale with a variables dict;
                       returns the text plus which locale actually matched.
  * ``missing_vars``   which placeholders a template needs (for validation UIs).
  * ``locales``        which locales a key has been translated into.

A missing variable is left as its literal ``{name}`` rather than raising, so a partial
variables dict degrades gracefully instead of failing a notification send.

Registry: ``messagetemplates.json`` (env ``FACE_MSGTEMPLATES_FILE``).
"""

from __future__ import annotations

import re
from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_MSGTEMPLATES_FILE", "messagetemplates.json")

_PLACEHOLDER = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")
_DEFAULT_LOCALE = "default"




def _norm_locale(locale: Optional[str]) -> str:
    return (locale or "").strip().lower().replace("_", "-") or _DEFAULT_LOCALE


def set_template(tenant: Optional[str], key: str, body: str,
                 locale: str = _DEFAULT_LOCALE) -> dict:
    key = (key or "").strip()
    if not key:
        raise ValueError("template key is required.")
    if body is None:
        raise ValueError("template body is required.")
    loc = _norm_locale(locale)
    with _reg.mutate() as data:
        data.setdefault(_reg.norm(tenant), {}).setdefault(key, {})[loc] = str(body)
    return {"key": key, "locale": loc, "variables": _vars(str(body))}


def _fallback_chain(locale: str) -> List[str]:
    loc = _norm_locale(locale)
    chain = [loc]
    if "-" in loc:
        chain.append(loc.split("-", 1)[0])
    if _DEFAULT_LOCALE not in chain:
        chain.append(_DEFAULT_LOCALE)
    return chain


def _render_body(body: str, variables: dict) -> str:
    """Substitute only bare ``{name}`` placeholders from a flat variables dict.

    Uses a strict identifier regex, so attribute/index access (``{x.attr}``,
    ``{x[0]}``) and format specs are NOT honoured — closing the format-string
    injection surface that ``str.format``/``string.Formatter`` would open. A
    missing variable is left as its literal ``{name}``.
    """
    def repl(m):
        name = m.group(1)
        return str(variables[name]) if name in variables else "{" + name + "}"
    return _PLACEHOLDER.sub(repl, body)


def render(tenant: Optional[str], key: str, variables: Optional[dict] = None,
           locale: str = _DEFAULT_LOCALE) -> dict:
    templates = (_reg.load().get(_reg.norm(tenant)) or {}).get((key or "").strip(), {})
    if not templates:
        return {"found": False}
    variables = variables or {}
    for candidate in _fallback_chain(locale):
        if candidate in templates:
            text = _render_body(templates[candidate], variables)
            return {"found": True, "text": text, "locale": candidate,
                    "requested": _norm_locale(locale)}
    # a key exists but none of the fallback locales matched: use any stored one
    any_loc, body = next(iter(templates.items()))
    return {"found": True, "text": _render_body(body, variables), "locale": any_loc,
            "requested": _norm_locale(locale)}


def _vars(body: str) -> List[str]:
    return sorted(set(_PLACEHOLDER.findall(body or "")))


def missing_vars(tenant: Optional[str], key: str, provided: List[str],
                 locale: str = _DEFAULT_LOCALE) -> List[str]:
    templates = (_reg.load().get(_reg.norm(tenant)) or {}).get((key or "").strip(), {})
    loc = _norm_locale(locale)
    body = templates.get(loc) or (next(iter(templates.values())) if templates else "")
    return [v for v in _vars(body) if v not in set(provided or [])]


def locales(tenant: Optional[str], key: str) -> List[str]:
    templates = (_reg.load().get(_reg.norm(tenant)) or {}).get((key or "").strip(), {})
    return sorted(templates.keys())
