"""Manage the web users of Mini-NOC from the server (there are no default users).

    python -m tools.manage_users add alice --role admin
    python -m tools.manage_users passwd alice
    python -m tools.manage_users list
    python -m tools.manage_users delete alice

The password is asked on the terminal (twice) or read from MININOC_USER_PASSWORD; it is never a
command-line argument, so it does not end up in the shell history or in `ps`.
"""

import argparse
import getpass
import os
import sys
import time

from api import auth
from storage import create_repository


def read_password(environ, confirm):
    """Return the password from the environment or from two terminal prompts."""
    from_env = environ.get("MININOC_USER_PASSWORD", "")
    if from_env:
        return from_env
    first = getpass.getpass("Password: ")
    if confirm:
        second = getpass.getpass("Repeat password: ")
        if first != second:
            raise ValueError("the two passwords differ")
    return first


def build_parser():
    parser = argparse.ArgumentParser(description="Manage Mini-NOC web users")
    parser.add_argument("--db-path", default=os.environ.get("MININOC_DB_PATH", "data/mini_noc.db"))
    commands = parser.add_subparsers(dest="command", required=True)
    add = commands.add_parser("add", help="create a user")
    add.add_argument("username")
    add.add_argument("--role", choices=auth.VALID_ROLES, default="admin")
    passwd = commands.add_parser("passwd", help="change a password (invalidates that user's sessions)")
    passwd.add_argument("username")
    commands.add_parser("list", help="list users (no password hashes)")
    delete = commands.add_parser("delete", help="delete a user")
    delete.add_argument("username")
    return parser


def format_time(ts):
    if ts is None:
        return "never"
    return time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(ts))


def run(args, environ):
    repo = create_repository("sqlite", args.db_path)
    repo.init_storage()
    if args.command == "list":
        users = repo.list_users()
        if len(users) == 0:
            print("No users yet. Create one with: python -m tools.manage_users add <name>")
        for user in users:
            print(
                "%-20s %-7s created %s, last login %s"
                % (user["username"], user["role"], format_time(user["created_utc"]), format_time(user["last_login_utc"]))
            )
        return 0
    username = auth.normalize_username(args.username)
    if args.command == "delete":
        if not repo.delete_user(username):
            raise ValueError("no such user: " + username)
        print("Deleted " + username)
        return 0
    password = read_password(environ, True)
    auth.check_password_strength(password)
    password_hash = auth.hash_password(password)
    if args.command == "add":
        repo.create_user(username, password_hash, args.role, int(time.time()))
        print("Created %s (%s)" % (username, args.role))
        return 0
    if not repo.set_user_password(username, password_hash):
        raise ValueError("no such user: " + username)
    print("Password changed for " + username)
    return 0


def main(argv=None, environ=None):
    if environ is None:
        environ = os.environ
    args = build_parser().parse_args(argv)
    try:
        return run(args, environ)
    except ValueError as exc:
        print("error: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
