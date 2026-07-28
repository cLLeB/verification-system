"""Private-link gate: the whole site opens only from a link that carries a secret.

Why
---
A biometric enrolment page that anyone on the internet can reach and use is both a
policy problem for the host and a data-poisoning problem for us. But asking testers
for a password is exactly the friction we removed. This is the middle: the invite
link carries the secret, so a person who has the link just taps and enrols, while
someone who guesses the hostname sees nothing at all.

How
---
Set ``FACE_LINK_TOKEN`` and share ``https://<host>/?k=<token>``.

  * The first request with ``?k=`` stores a long-lived cookie and redirects to the
    clean URL, so the secret leaves the address bar immediately and the person can
    bookmark / re-open the page normally.
  * Every later request is authorised by that cookie.
  * Anything without either gets a plain 404 - no login form, no hint that an app
    is here, nothing for a crawler or scanner to find.

Unset (the default) the whole thing is a no-op, so local dev and the test suite
behave exactly as before.

Deliberately NOT gated: ``/healthz`` and ``/readyz`` (the host's own probes must
reach them, and they expose nothing), and ``/v1/*`` (the integration API, which is
already authenticated per-request by API key).
"""

from __future__ import annotations

import hmac
import os

from flask import make_response, redirect, request

COOKIE = "face_link"
PARAM = "k"
_MAX_AGE = 30 * 24 * 3600                    # 30 days: a tester enrols once, returns later

TOKEN = os.environ.get("FACE_LINK_TOKEN", "").strip()

# Paths that must answer even without the link secret, because they carry their
# own credential and are reached by tools rather than browsers:
#   /healthz,/readyz  the host's probes (a gated 404 reads as "unhealthy" and gets
#                     the service killed)
#   /v1/              the integration API - authenticated per request by API key
#   /api/analytics/   the data-pull surface - gated on FACE_ANALYTICS_TOKEN, and
#                     404s outright when that secret is unset, so opening it here
#                     grants nothing. Without this the pull tooling cannot reach a
#                     link-gated deployment at all.
_OPEN_EXACT = ("/healthz", "/readyz")
_OPEN_PREFIX = ("/v1/", "/api/analytics/")


def enabled() -> bool:
    return bool(TOKEN)


def _authorised(supplied: str) -> bool:
    return bool(supplied) and hmac.compare_digest(supplied, TOKEN)


def _is_open(path: str) -> bool:
    return path in _OPEN_EXACT or path.startswith(_OPEN_PREFIX)


def check():
    """Flask ``before_request`` hook. Returns a response to short-circuit, else None.

    Order matters: this runs before rate limiting and before any route, so an
    un-invited request costs almost nothing and reveals nothing."""
    if not TOKEN or _is_open(request.path):
        return None
    if _authorised(request.cookies.get(COOKIE, "")):
        return None
    supplied = request.args.get(PARAM, "")
    if _authorised(supplied):
        # Strip the secret from the URL, keep any other query args.
        rest = {k: v for k, v in request.args.items(True) if k != PARAM}
        target = request.path + ("?" + "&".join(f"{k}={v}" for k, v in rest.items())
                                 if rest else "")
        resp = make_response(redirect(target, code=302))
        resp.set_cookie(COOKIE, TOKEN, max_age=_MAX_AGE, httponly=True,
                        samesite="Lax", secure=request.is_secure)
        return resp
    # Indistinguishable from a hostname that hosts nothing.
    return make_response("Not Found", 404)
