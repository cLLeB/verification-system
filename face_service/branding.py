"""Per-tenant branding — white-label the enrolment and verification surfaces.

When the platform is resold, each tenant wants their own name, logo and colours on the
self-enrolment page, receipts and emails. This subsystem stores per-tenant branding
tokens, validates them (colours must be real hex, URLs must look like URLs), and
resolves an effective theme by layering a tenant's overrides over sensible defaults —
so a surface always has a complete theme to render even when a tenant sets only a logo.

  * ``set_branding``  update one or more tokens for a tenant (partial updates merge).
  * ``resolve``       the effective theme: defaults overlaid with tenant overrides.
  * ``reset``         clear a tenant's overrides (back to defaults).

Validated tokens: ``product_name``, ``logo_url``, ``primary_color`` / ``accent_color``
(``#RRGGBB``), ``support_email``, ``footer_text``. Unknown tokens are rejected so a typo
can't silently store dead configuration.

Registry: ``branding.json`` (env ``FACE_BRANDING_FILE``).
"""

from __future__ import annotations

import re
from typing import Optional

from ._registry import Registry

_reg = Registry("FACE_BRANDING_FILE", "branding.json")

_DEFAULTS = {
    "product_name": "Contactless ID",
    "logo_url": "",
    "primary_color": "#2563EB",
    "accent_color": "#10B981",
    "support_email": "",
    "footer_text": "",
}

_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_URL = re.compile(r"^https?://[^\s]+$")


def _validate(token: str, value: str) -> str:
    value = "" if value is None else str(value).strip()
    if token in ("primary_color", "accent_color"):
        if value and not _HEX.match(value):
            raise ValueError(f"{token} must be a #RRGGBB hex colour.")
        return value.upper() if value else value
    if token == "logo_url":
        if value and not _URL.match(value):
            raise ValueError("logo_url must be an http(s) URL.")
    if token == "support_email":
        if value and not _EMAIL.match(value):
            raise ValueError("support_email must be a valid email.")
    if token == "product_name" and len(value) > 60:
        raise ValueError("product_name is too long (max 60).")
    return value


def set_branding(tenant: Optional[str], **tokens) -> dict:
    if not tokens:
        raise ValueError("no branding tokens supplied.")
    cleaned = {}
    for k, v in tokens.items():
        if k not in _DEFAULTS:
            raise ValueError(f"unknown branding token: {k}")
        cleaned[k] = _validate(k, v)
    with _reg.mutate() as data:
        data.setdefault(_reg.norm(tenant), {}).update(cleaned)
    return resolve(tenant)


def resolve(tenant: Optional[str]) -> dict:
    overrides = _reg.load().get(_reg.norm(tenant)) or {}
    theme = dict(_DEFAULTS)
    theme.update({k: v for k, v in overrides.items() if k in _DEFAULTS})
    return theme


def reset(tenant: Optional[str]) -> bool:
    with _reg.mutate() as data:
        return data.pop(_reg.norm(tenant), None) is not None
