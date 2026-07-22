"""Operate the LIVE deployment from here: list who's enrolled, and remove people
so they can re-enrol cleanly.

    .\\venv\\Scripts\\python manage_remote.py users
    .\\venv\\Scripts\\python manage_remote.py delete caleb Edwina Edleft
    .\\venv\\Scripts\\python manage_remote.py delete caleb --yes      # no prompt

It signs in with the operator password (the one you use at /admin) and calls the
same endpoints the console does. The password is asked for interactively so it
never lands in your shell history; ``--password`` or FACE_ADMIN_PASSWORD also work
for scripting.

Deleting is PERMANENT and covers BOTH modalities — a person's face and palm
templates go together, along with any credential issued to them. It prints exactly
who it is about to remove and waits for you to type ``yes``.

    --url   defaults to $SPACE_URL, else the production Space
"""

from __future__ import annotations

import argparse
import getpass
import http.cookiejar
import json
import os
import urllib.error
import urllib.request

DEFAULT_URL = "https://kyereboatengcaleb-faceverify-palm.hf.space"


class Session:
    """A logged-in admin session against the live deployment."""

    def __init__(self, base: str):
        self.base = base.rstrip("/")
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar))

    def call(self, path: str, payload: dict = None, timeout: int = 60) -> dict:
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(self.base + path, data=data,
                                     headers={"Content-Type": "application/json"})
        try:
            with self.opener.open(req, timeout=timeout) as r:
                body = r.read()
        except urllib.error.HTTPError as exc:
            body = exc.read()
            try:
                return json.loads(body)
            except ValueError:
                raise SystemExit(f"{path}: HTTP {exc.code}")
        if not body.lstrip().startswith((b"{", b"[")):
            raise SystemExit("the deployment returned a web page, not data - it is "
                             "asleep or rebuilding. Open it in a browser, wait for "
                             "the app to load, then try again.")
        return json.loads(body)

    def login(self, username: str, password: str) -> None:
        out = self.call("/admin/login", {"username": username, "password": password})
        if not out.get("success"):
            raise SystemExit("login failed: " + out.get("message", "wrong password"))
        print(f"signed in as {out.get('user', username)}")


def cmd_users(s: Session) -> int:
    out = s.call("/api/users")
    users = out.get("users") or []
    if not users:
        print("nobody enrolled.")
        return 0
    print(f"{len(users)} enrolled:")
    for u in users:
        if isinstance(u, dict):
            mods = ",".join(u.get("modalities") or []) or "?"
            print(f"   {u.get('user_id')}   [{mods}]")
        else:
            print(f"   {u}")
    return 0


def cmd_delete(s: Session, names: list, assume_yes: bool) -> int:
    print("\nabout to PERMANENTLY delete (face AND palm, plus any issued credential):")
    for n in names:
        print(f"   {n}")
    if not assume_yes:
        if input("\ntype 'yes' to confirm: ").strip().lower() != "yes":
            print("cancelled - nothing was deleted.")
            return 1
    gone, missing = [], []
    for n in names:
        out = s.call("/api/users/delete", {"user_id": n})
        if out.get("success"):
            mods = ",".join(out.get("deleted_modalities") or []) or "?"
            gone.append(n)
            print(f"   deleted {n}  [{mods}]")
        else:
            missing.append(n)
            print(f"   NOT FOUND {n} - {out.get('message', '')}")
    print(f"\n{len(gone)} deleted, {len(missing)} not found.")
    if gone:
        print("they can now re-enrol from the link with no password.")
    return 0 if gone else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=("users", "delete"))
    ap.add_argument("names", nargs="*", help="user ids (delete only)")
    ap.add_argument("--url", default=os.environ.get("SPACE_URL", DEFAULT_URL))
    ap.add_argument("--username", default="admin")
    ap.add_argument("--password", default=os.environ.get("FACE_ADMIN_PASSWORD", ""))
    ap.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    a = ap.parse_args()

    if a.command == "delete" and not a.names:
        raise SystemExit("delete needs at least one user id")
    password = a.password or getpass.getpass(f"admin password for {a.url}: ")

    s = Session(a.url)
    s.login(a.username, password)
    if a.command == "users":
        return cmd_users(s)
    return cmd_delete(s, a.names, a.yes)


if __name__ == "__main__":
    raise SystemExit(main())
