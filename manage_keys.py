"""Mint / list / revoke API keys for integrating apps.

    python manage_keys.py create "Acme App"          # mint a key (shown ONCE)
    python manage_keys.py create "Acme" --tenant acme # pin the tenant id
    python manage_keys.py list
    python manage_keys.py revoke <tenant>

Keys are stored hashed in apikeys.json (gitignored). Give the raw key + tenant to
the integrating developer; they send it as the X-API-Key header.
"""

from __future__ import annotations

import argparse

from face_service import keys


def main() -> None:
    p = argparse.ArgumentParser(description="Manage face-verification API keys")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("create", help="mint a new API key")
    c.add_argument("name", help="human label for the integrating app")
    c.add_argument("--tenant", default=None, help="explicit tenant id (default: random)")

    sub.add_parser("list", help="list tenants/keys (no secrets)")

    r = sub.add_parser("revoke", help="revoke all keys for a tenant")
    r.add_argument("tenant")

    args = p.parse_args()
    if args.cmd == "create":
        info = keys.create_key(args.name, args.tenant)
        print("API key created — store the key now, it is not recoverable:\n")
        print(f"  api_key        : {info['api_key']}")
        print(f"  tenant         : {info['tenant']}")
        print(f"  signing_secret : {info['signing_secret']}")
        print(f"  name           : {info['name']}\n")
        print("Use it as a header:  X-API-Key: " + info["api_key"])
    elif args.cmd == "list":
        rows = keys.list_keys()
        if not rows:
            print("(no keys)")
        for k in rows:
            print(f"  {k['tenant']:18}  {k['name']}")
    elif args.cmd == "revoke":
        n = keys.revoke(args.tenant)
        print(f"revoked {n} key(s) for tenant '{args.tenant}'")


if __name__ == "__main__":
    main()
