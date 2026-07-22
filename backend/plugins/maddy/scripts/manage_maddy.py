#!/usr/bin/env python3
"""
scripts/manage_maddy.py — Privileged Maddy Account & Domain Management Helper.

Run as root via sudoers (NOPASSWD). Never touch SQLite directly for auth —
delegates to maddy's own CLI so the internal password hash format is always correct.

Usage:
    python3 manage_maddy.py create <email> <plaintext_password>
    python3 manage_maddy.py delete <email>
    python3 manage_maddy.py add-domain <domain>
    python3 manage_maddy.py remove-domain <domain>
"""
import sys
import os
import re
import subprocess

MADDY_BIN  = "/usr/local/bin/maddy"
MADDY_CONF = "/etc/maddy/maddy.conf"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


def restart_maddy():
    subprocess.run(["systemctl", "restart", "maddy"], check=False)


# ---------------------------------------------------------------------------
# Account management
# ---------------------------------------------------------------------------

def create_account(email: str, password: str):
    """
    Create a maddy credential + IMAP account folder.
    Uses 'maddy creds create' via stdin — maddy hashes the password internally.
    """
    if not maddy_available():
        print(f"Error: maddy binary not found at {MADDY_BIN}", file=sys.stderr)
        sys.exit(1)

    # 1. Create auth credential (maddy handles its own hash format)
    res = run([MADDY_BIN, "creds", "create", email], stdin_data=password + "\n" + password + "\n")
    if res.returncode != 0:
        stderr = res.stderr.strip()
        if "already exists" not in stderr.lower():
            print(f"Error creating credentials: {stderr}", file=sys.stderr)
            sys.exit(1)

    # 2. Create IMAP account (mailbox folders)
    res2 = run([MADDY_BIN, "imap-acct", "create", email], check=False)
    if res2.returncode != 0:
        stderr2 = res2.stderr.strip()
        if "already exists" not in stderr2.lower():
            print(f"Warning: imap-acct create: {stderr2}", file=sys.stderr)

    print(f"OK: account {email} created")


def delete_account(email: str):
    """Remove a maddy credential and IMAP account."""
    if not maddy_available():
        print(f"Error: maddy binary not found at {MADDY_BIN}", file=sys.stderr)
        sys.exit(1)

    errors = []

    res1 = run([MADDY_BIN, "imap-acct", "remove", email], stdin_data="y\ny\n", check=False)
    if res1.returncode != 0:
        stderr1 = res1.stderr.strip()
        if "does not exist" not in stderr1.lower():
            errors.append(f"imap-acct remove: {stderr1}")

    res2 = run([MADDY_BIN, "creds", "remove", email], stdin_data="y\ny\n", check=False)
    if res2.returncode != 0:
        stderr2 = res2.stderr.strip()
        if "does not exist" not in stderr2.lower():
            errors.append(f"creds remove: {stderr2}")

    if errors:
        for err in errors:
            print(f"Error: {err}", file=sys.stderr)
        sys.exit(1)

    print(f"OK: account {email} deleted")


# ---------------------------------------------------------------------------
# Domain management — edit $(local_domains) in maddy.conf
# ---------------------------------------------------------------------------

def _read_conf() -> str:
    with open(MADDY_CONF, "r") as f:
        return f.read()


def _write_conf(content: str):
    with open(MADDY_CONF, "w") as f:
        f.write(content)


def _update_local_domains(action: str, domain: str):
    """
    Add or remove a domain from the $(local_domains) line in maddy.conf.
    The line looks like:
        $(local_domains) = $(primary_domain) extra.com
    """
    content = _read_conf()

    match = re.search(
        r"^\$\(local_domains\)\s*=\s*(.+)$",
        content,
        re.MULTILINE,
    )
    if not match:
        print(f"Error: $(local_domains) line not found in {MADDY_CONF}", file=sys.stderr)
        sys.exit(1)

    # Split current value, preserving $(primary_domain) as a token
    current = match.group(1).strip()
    parts = current.split()

    if action == "add":
        if domain not in parts:
            parts.append(domain)
        else:
            print(f"OK: {domain} already in $(local_domains)")
            return
    elif action == "remove":
        if domain not in parts:
            print(f"OK: {domain} not in $(local_domains), nothing to remove")
            return
        parts = [p for p in parts if p != domain]

    new_line = "$(local_domains) = " + " ".join(parts)
    new_content = re.sub(
        r"^\$\(local_domains\)\s*=\s*.+$",
        new_line,
        content,
        flags=re.MULTILINE,
    )
    _write_conf(new_content)
    print(f"OK: maddy.conf updated — $(local_domains) = {' '.join(parts)}")


def add_domain(domain: str):
    """Add domain to maddy $(local_domains) and restart maddy."""
    if not os.path.isfile(MADDY_CONF):
        print(f"Error: {MADDY_CONF} not found — is maddy installed?", file=sys.stderr)
        sys.exit(1)

    _update_local_domains("add", domain)
    restart_maddy()
    print(f"OK: maddy restarted — {domain} is now a local mail domain")


def remove_domain(domain: str):
    """Remove domain from maddy $(local_domains) and restart maddy."""
    if not os.path.isfile(MADDY_CONF):
        print(f"Error: {MADDY_CONF} not found — is maddy installed?", file=sys.stderr)
        sys.exit(1)

    _update_local_domains("remove", domain)
    restart_maddy()
    print(f"OK: maddy restarted — {domain} removed from local mail domains")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    if os.geteuid() != 0:
        print("Error: must run as root", file=sys.stderr)
        sys.exit(1)

    if len(sys.argv) < 3:
        print(
            "Usage:\n"
            "  manage_maddy.py create <email> <password>\n"
            "  manage_maddy.py delete <email>\n"
            "  manage_maddy.py add-domain <domain>\n"
            "  manage_maddy.py remove-domain <domain>",
            file=sys.stderr,
        )
        sys.exit(1)

    action = sys.argv[1]
    arg    = sys.argv[2]

    if action == "create":
        if len(sys.argv) < 4:
            print("Error: password required for create", file=sys.stderr)
            sys.exit(1)
        create_account(arg, sys.argv[3])

    elif action == "delete":
        delete_account(arg)

    elif action == "add-domain":
        add_domain(arg)

    elif action == "remove-domain":
        remove_domain(arg)

    else:
        print(f"Error: unknown action '{action}'", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
