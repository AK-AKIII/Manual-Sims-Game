"""Small admin CLI: `python -m prplatform.manage set-admin NAME [--off]`,
`python -m prplatform.manage list-users`, `python -m prplatform.manage set-password NAME`.

Admins can also be named in PR_ADMINS (no restart of the DB needed, but the
api must be restarted to pick up the variable)."""

import argparse
import getpass
import sys

from . import auth, config, db


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m prplatform.manage")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("set-admin", help="make a registered user an admin (or --off)")
    a.add_argument("username")
    a.add_argument("--off", action="store_true")
    sub.add_parser("list-users")
    p = sub.add_parser("set-password", help="reset a user's password (prompts)")
    p.add_argument("username")
    args = ap.parse_args(argv)
    settings = config.load()
    db.init(settings.db)
    conn = db.connect(settings.db)
    if args.cmd == "set-admin":
        n = conn.execute("UPDATE users SET is_admin = ? WHERE username = ?",
                         (0 if args.off else 1, args.username)).rowcount
        if not n:
            print(f"no user {args.username!r}", file=sys.stderr)
            return 1
        print(f"{args.username}: is_admin = {0 if args.off else 1}")
    elif args.cmd == "list-users":
        for r in conn.execute("SELECT id, username, created, is_admin FROM users ORDER BY id"):
            print(r["id"], r["username"], r["created"], "admin" if r["is_admin"] else "")
    elif args.cmd == "set-password":
        pw = getpass.getpass("new password: ")
        if not (auth.PASSWORD_MIN <= len(pw) <= auth.PASSWORD_MAX):
            print("password must be 8-256 characters", file=sys.stderr)
            return 1
        with db.transaction(conn, immediate=True):
            row = conn.execute("SELECT id FROM users WHERE username = ?", (args.username,)).fetchone()
            if row is None:
                print(f"no user {args.username!r}", file=sys.stderr)
                return 1
            conn.execute("UPDATE users SET pw_hash = ? WHERE id = ?", (auth.hash_password(pw), row["id"]))
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (row["id"],))
        print("password changed; existing sessions logged out")
    return 0


if __name__ == "__main__":
    sys.exit(main())
