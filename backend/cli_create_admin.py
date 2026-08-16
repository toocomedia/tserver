#!/usr/bin/env python3
"""
cli_create_admin.py — Create or reset the panel admin user.

Run from app dir (or with PYTHONPATH=app):
  /opt/srv-panel/venv/bin/python /opt/srv-panel/app/cli_create_admin.py \\
    --username admin --password '...' [--force]
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import sys


async def _run(username: str, password: str | None, force: bool, disable_2fa: bool) -> int:
    from database import init_db, AsyncSessionLocal
    from services import auth_service

    await init_db()
    async with AsyncSessionLocal() as session:
        try:
            if disable_2fa:
                await auth_service.disable_2fa_for_user(session, username)
                print(f"OK: disabled 2FA for admin user '{username}'")
                
            if password is not None:
                user, action = await auth_service.create_or_reset_admin(
                    session, username, password, force=force
                )
                print(f"OK: admin user '{user.username}' {action}")
            elif not disable_2fa:
                print("ERROR: Password required", file=sys.stderr)
                return 1

            await session.commit()
        except ValueError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            await session.rollback()
            return 1
        except Exception as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            await session.rollback()
            return 1

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create or reset the VPS panel admin user"
    )
    parser.add_argument(
        "--username", "-u", default="admin", help="Admin username (default: admin)"
    )
    parser.add_argument(
        "--password", "-p", default=None, help="Password (prompt if omitted)"
    )
    parser.add_argument(
        "--force",
        "-f",
        action="store_true",
        help="Reset password if the user already exists",
    )
    parser.add_argument(
        "--disable-2fa",
        action="store_true",
        help="Disable 2FA for the user",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit 0 if at least one user exists, 1 otherwise (no create)",
    )
    args = parser.parse_args()

    if args.check:
        return asyncio.run(_check_users())

    password = args.password
    if not password and not args.disable_2fa:
        password = getpass.getpass("Admin password: ")
        confirm = getpass.getpass("Confirm password: ")
        if password != confirm:
            print("ERROR: Passwords do not match", file=sys.stderr)
            return 1

        if len(password) < 8:
            print("ERROR: Password must be at least 8 characters", file=sys.stderr)
            return 1
    elif password is not None and len(password) < 8:
        print("ERROR: Password must be at least 8 characters", file=sys.stderr)
        return 1

    return asyncio.run(_run(args.username.strip() or "admin", password, args.force, args.disable_2fa))


async def _check_users() -> int:
    from database import init_db, AsyncSessionLocal
    from services import auth_service

    await init_db()
    async with AsyncSessionLocal() as session:
        n = await auth_service.count_users(session)
    if n > 0:
        print(f"users={n}")
        return 0
    print("users=0")
    return 1


if __name__ == "__main__":
    sys.exit(main())
