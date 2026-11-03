"""Small admin CLI for SSH use (every change is written to the audit log with
actor "(cli)"; the safety rules of the admin API apply, except "not yourself"):

    python -m prplatform.manage list-users
    python -m prplatform.manage set-admin NAME [--off]
    python -m prplatform.manage set-password NAME          (prompts)
    python -m prplatform.manage disable NAME [--reason TEXT]
    python -m prplatform.manage enable NAME
    python -m prplatform.manage delete-user NAME --confirm NAME
    python -m prplatform.manage delete-submission ID
    python -m prplatform.manage set-email NAME EMAIL       ("" clears it)
    python -m prplatform.manage audit [--limit N]

Admins can also be named in PR_ADMINS (no DB change needed, but the api must
be restarted to pick up the variable)."""

import argparse
import getpass
import json
import sys

from . import admin, config, db


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m prplatform.manage")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("set-admin", help="make a registered user an admin (or --off)")
    a.add_argument("username")
    a.add_argument("--off", action="store_true")
    sub.add_parser("list-users")
    p = sub.add_parser("set-password", help="reset a user's password (prompts); logs them out everywhere")
    p.add_argument("username")
    d = sub.add_parser("disable", help="disable an account: logs it out, cancels its queue, hides it from the board")
    d.add_argument("username")
    d.add_argument("--reason", default=None)
    e = sub.add_parser("enable", help="re-enable a disabled account")
    e.add_argument("username")
    x = sub.add_parser("delete-user", help="permanently delete a user, their submissions and results")
    x.add_argument("username")
    x.add_argument("--confirm", required=True, help="repeat the username")
    ds = sub.add_parser("delete-submission", help="permanently delete one submission and its results")
    ds.add_argument("id", type=int)
    em = sub.add_parser("set-email", help="set (or with \"\" clear) a user's email")
    em.add_argument("username")
    em.add_argument("email")
    au = sub.add_parser("audit", help="show the admin audit log, newest first")
    au.add_argument("--limit", type=int, default=50)
    args = ap.parse_args(argv)
    settings = config.load()
    db.init(settings.db)
    conn = db.connect(settings.db)
    actor = admin.CLI_ACTOR
    try:
        if args.cmd == "list-users":
            for r in admin.user_rows(conn, settings):
                print(r["id"], r["username"], r["email"] or "-", r["created"], r["status"],
                      "admin" if r["is_admin"] else "", f"rank={r['rank']}" if r["rank"] else "")
            return 0
        if args.cmd == "audit":
            for r in admin.audit_rows(conn, limit=args.limit):
                print(r["created"], r["actor"], r["action"], r["target"] or "-", json.dumps(r["details"]))
            return 0
        if args.cmd == "set-password":
            pw = getpass.getpass("new password: ")
            admin.check_password(pw)
        with db.transaction(conn, immediate=True):
            if args.cmd == "delete-submission":
                admin.delete_submission(conn, settings, actor, args.id)
                print(f"submission {args.id} deleted")
                return 0
            target = admin.find_user(conn, args.username)
            uid = target["id"]
            if args.cmd == "set-admin":
                changed = admin.set_admin(conn, settings, actor, uid, not args.off)
                print(f"{target['username']}: is_admin = {0 if args.off else 1}" + ("" if changed else " (unchanged)"))
            elif args.cmd == "set-password":
                n = admin.reset_password(conn, settings, actor, uid, pw)
                print(f"password changed; {n} session(s) logged out")
            elif args.cmd == "disable":
                changed, cancelled = admin.disable(conn, settings, actor, uid, args.reason)
                print(f"{target['username']} disabled; {len(cancelled)} queued submission(s) cancelled"
                      if changed else f"{target['username']} was already disabled")
            elif args.cmd == "enable":
                changed = admin.enable(conn, settings, actor, uid)
                print(f"{target['username']} enabled" if changed else f"{target['username']} was not disabled")
            elif args.cmd == "delete-user":
                name, n = admin.delete_user(conn, settings, actor, uid, args.confirm)
                print(f"deleted {name} and {n} submission(s)")
            elif args.cmd == "set-email":
                admin.set_email(conn, actor, uid, args.email)
                print(f"{target['username']}: email = {args.email or '(none)'}")
    except admin.AdminError as err:
        print(err.message, file=sys.stderr)
        return 1
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
