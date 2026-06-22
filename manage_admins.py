"""Create / list / remove admin operator accounts.

    python manage_admins.py create alice              # prompts for a password
    python manage_admins.py create alice --password s3cret
    python manage_admins.py list
    python manage_admins.py remove alice

Accounts are stored hashed in admins.json (gitignored). Once at least one account
exists, the FACE_ADMIN_PASSWORD bootstrap login is disabled.
"""

from __future__ import annotations

import argparse
import getpass

from face_service import admins


def main() -> None:
    p = argparse.ArgumentParser(description="Manage admin operator accounts")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("create", help="create or reset an operator")
    c.add_argument("username")
    c.add_argument("--password", default=None, help="password (omit to be prompted)")

    sub.add_parser("list", help="list operator usernames")

    r = sub.add_parser("remove", help="remove an operator")
    r.add_argument("username")

    args = p.parse_args()
    if args.cmd == "create":
        pw = args.password or getpass.getpass(f"password for {args.username}: ")
        if admins.create_admin(args.username, pw):
            print(f"operator '{args.username}' saved.")
        else:
            print("username and password are required.")
    elif args.cmd == "list":
        names = admins.list_admins()
        print("\n".join(f"  {n}" for n in names) if names else "(no operators — bootstrap login active)")
    elif args.cmd == "remove":
        print(f"removed '{args.username}'." if admins.remove_admin(args.username)
              else f"no operator '{args.username}'.")


if __name__ == "__main__":
    main()
