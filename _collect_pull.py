"""Pull the collected liveness images from the live Space into pad_data/, ready for
the anti-spoof (PAD) benchmark. Temporary tuning tool (git-ignored output).

    python _collect_pull.py --url https://<space>.hf.space --token <FACE_ANALYTICS_TOKEN>
    python -m bench run --suite pad          # then measure + publish the number

Add --wipe to clear the server copy after a successful pull (teardown).
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import urllib.request
import zipfile


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default=os.environ.get("SPACE_URL", ""),
                    help="live Space base URL")
    ap.add_argument("--token", default=os.environ.get("FACE_ANALYTICS_TOKEN", ""),
                    help="the FACE_ANALYTICS_TOKEN set on the Space")
    ap.add_argument("--out", default="pad_data", help="output folder (default pad_data/)")
    ap.add_argument("--wipe", action="store_true", help="clear the server copy after pulling")
    a = ap.parse_args()
    if not a.url or not a.token:
        raise SystemExit("need --url and --token (or SPACE_URL / FACE_ANALYTICS_TOKEN env)")

    url = a.url.rstrip("/") + "/api/analytics/collect" + ("?wipe=1" if a.wipe else "")
    req = urllib.request.Request(url, headers={"X-Analytics-Token": a.token})
    with urllib.request.urlopen(req, timeout=180) as r:
        d = json.loads(r.read())
    if not d.get("success"):
        raise SystemExit(f"export failed: {d}")

    os.makedirs(a.out, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(base64.b64decode(d["zip_b64"]))) as z:
        z.extractall(a.out)                       # -> pad_data/{live,spoof,palm_live,palm_spoof}/
    print("pulled:", d.get("counts"), "-> extracted to", a.out)
    print("next:  python -m bench run --suite pad      (face live/ + spoof/ are ready)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
