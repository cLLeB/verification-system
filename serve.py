"""Production server launcher (cross-platform, via waitress).

Unlike ``python app.py`` (Flask dev server + self-signed adhoc TLS, single-purpose
for local phone testing), this serves the app with a real WSGI server suitable for
running behind a TLS-terminating reverse proxy or tunnel.

    python serve.py                 # http://0.0.0.0:5000
    PORT=8080 python serve.py       # custom port

TLS: terminate HTTPS at a reverse proxy (Caddy/nginx) or a tunnel (Cloudflare).
See docs/DEPLOY.md. Browsers require HTTPS for the camera, so a public deployment
MUST sit behind TLS.
"""

from __future__ import annotations

import os

from waitress import serve

from app import app  # noqa: F401  (imports warm the models once, at startup)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    threads = int(os.environ.get("WEB_THREADS", "8"))
    print(f"[serve] waitress on 0.0.0.0:{port} threads={threads} "
          f"(put TLS in front — see docs/DEPLOY.md)", flush=True)
    serve(app, host="0.0.0.0", port=port, threads=threads)
