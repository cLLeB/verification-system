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
import time

from face_service import keys


def main() -> None:
    p = argparse.ArgumentParser(description="Manage face-verification API keys")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("create", help="mint a new API key")
    c.add_argument("name", help="human label for the integrating app")
    c.add_argument("--tenant", default=None, help="explicit tenant id (default: random)")
    c.add_argument("--role", default="admin", choices=keys.ROLES,
                   help="admin = full control; verify = recognition only (default: admin)")
    c.add_argument("--expires-in-days", type=int, default=None,
                   help="optional: key auto-expires after N days")
    c.add_argument("--sandbox", action="store_true",
                   help="sandbox key: returns canned responses (no model/storage) for testing")

    sub.add_parser("list", help="list tenants/keys (no secrets)")

    r = sub.add_parser("revoke", help="revoke all keys for a tenant")
    r.add_argument("tenant")

    rk = sub.add_parser("revoke-key", help="revoke a single key by its key_id")
    rk.add_argument("key_id")

    args = p.parse_args()
    if args.cmd == "create":
        info = keys.create_key(args.name, args.tenant, args.role, args.expires_in_days,
                               sandbox=args.sandbox)
        print("API key created — store the key now, it is not recoverable:\n")
        print(f"  api_key        : {info['api_key']}")
        print(f"  key_id         : {info['key_id']}")
        print(f"  tenant         : {info['tenant']}")
        print(f"  role           : {info['role']}")
        print(f"  signing_secret : {info['signing_secret']}")
        print(f"  name           : {info['name']}")
        if info.get("expires"):
            print(f"  expires        : {time.strftime('%Y-%m-%d', time.localtime(info['expires']))}")
        print("\nUse it as a header:  X-API-Key: " + info["api_key"])
    elif args.cmd == "list":
        rows = keys.list_keys()
        if not rows:
            print("(no keys)")
        for k in rows:
            used = (time.strftime("%Y-%m-%d", time.localtime(k["last_used"]))
                    if k.get("last_used") else "never")
            print(f"  {k['key_id']:14}  {k['tenant']:18}  {k['role']:7}  "
                  f"used:{used:10}  {k['name']}")
    elif args.cmd == "revoke":
        n = keys.revoke(args.tenant)
        print(f"revoked {n} key(s) for tenant '{args.tenant}'")
    elif args.cmd == "revoke-key":
        ok = keys.revoke_key(args.key_id)
        print(f"revoked key '{args.key_id}'" if ok else f"no key with id '{args.key_id}'")


if __name__ == "__main__":
    main()
