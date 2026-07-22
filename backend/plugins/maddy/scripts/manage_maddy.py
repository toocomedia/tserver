#!/usr/bin/env python3
"""
scripts/manage_maddy.py — Privileged Maddy Account Management Helper.

Run as root via sudoers (NOPASSWD). Manages maddy credentials and IMAP
accounts by delegating entirely to maddy's own CLI — never touching SQLite
directly for auth, so maddy's internal password hash format is always correct.

Usage:
    python3 manage_maddy.py create <email> <plaintext_password>
    python3 manage_maddy.py delete <email>
"""
import sys
import os
import subprocess


MADDY_BIN = "/usr/local/bin/maddy"


def run(cmd: list, stdin_data: str = None, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        input=stdin_data,
        capture_output=True,
        text=True,
        check=check,
    )


def maddy_available() -> bool:
    return os.path.isfile(MADDY_BIN) and os.access(MADDY_BIN, os.X_OK)


def create_account(email: str, password: str):
    """
    Create a maddy credential + IMAP account folder.
    Uses 'maddy creds create' via stdin — maddy hashes the password internally.
    """
    if not maddy_available():
        print(f"Error: maddy binary not found at {MADDY_BIN}", file=sys.stderr)
        sys.exit(1)

    # 1. Create auth credential (maddy handles its own hash format)
    #    Pass password via stdin to avoid it showing in process list.
    res = run([MADDY_BIN, "creds", "create", email], stdin_data=password + "\n" + password + "\n")
    if res.returncode != 0:
        stderr = res.stderr.strip()
        # If account already exists in creds, treat as non-fatal so we can
        # still ensure the IMAP account/folders are created below.
        if "already exists" not in stderr.lower():
            print(f"Error creating credentials: {stderr}", file=sys.stderr)
            sys.exit(1)

    # 2. Create IMAP account (mailbox folders)
    res2 = run([MADDY_BIN, "imap-acct", "create", email], check=False)
    if res2.returncode != 0:
        stderr2 = res2.stderr.strip()
        if "already exists" not in stderr2.lower():
            print(f"Warning: imap-acct create returned non-zero: {stderr2}", file=sys.stderr)
            # Not fatal — creds were already created. IMAP folder may be
            # created on first login by maddy automatically.

    print(f"OK: account {email} created")


def delete_account(email: str):
    """
    Remove a maddy credential and IMAP account.
    """
    if not maddy_available():
        print(f"Error: maddy binary not found at {MADDY_BIN}", file=sys.stderr)
        sys.exit(1)

    errors = []

    # 1. Remove IMAP account (confirm twice with 'y')
    res1 = run([MADDY_BIN, "imap-acct", "remove", email], stdin_data="y\ny\n", check=False)
    if res1.returncode != 0:
        stderr1 = res1.stderr.strip()
        if "does not exist" not in stderr1.lower():
            errors.append(f"imap-acct remove: {stderr1}")

    # 2. Remove auth credential
    res2 = run([MADDY_BIN, "creds", "remove", email], check=False)
    if res2.returncode != 0:
        stderr2 = res2.stderr.strip()
        if "does not exist" not in stderr2.lower():
            errors.append(f"creds remove: {stderr2}")

    if errors:
        for err in errors:
            print(f"Error: {err}", file=sys.stderr)
        sys.exit(1)

    print(f"OK: account {email} deleted")


def main():
    if os.geteuid() != 0:
        print("Error: must run as root", file=sys.stderr)
        sys.exit(1)

    if len(sys.argv) < 3:
        print("Usage: manage_maddy.py <create|delete> <email> [password]", file=sys.stderr)
        sys.exit(1)

    action = sys.argv[1]
    email = sys.argv[2]

    if action == "create":
        if len(sys.argv) < 4:
            print("Error: password required for create", file=sys.stderr)
            sys.exit(1)
        create_account(email, sys.argv[3])

    elif action == "delete":
        delete_account(email)

    else:
        print(f"Error: unknown action '{action}'", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
