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
    python3 manage_maddy.py sync-cert <mail.domain>
    python3 manage_maddy.py remove-cert <mail.domain>
"""
import grp
import sys
import os
import pwd
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

MADDY_BIN  = "/usr/local/bin/maddy"
MADDY_CONF = "/etc/maddy/maddy.conf"
MADDY_CERTS_DIR = Path("/etc/maddy/certs")
LE_LIVE_DIR = Path("/etc/letsencrypt/live")
HOST_RE = re.compile(
    r"^mail\.([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+)$"
)


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


def _atomic_write(path: Path, content: str, mode: int = 0o640):
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.stat() if path.exists() else None
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, existing.st_mode & 0o777 if existing else mode)
        if existing:
            os.chown(temp_name, existing.st_uid, existing.st_gid)
        os.replace(temp_name, path)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def _atomic_copy(source: Path, target: Path, uid: int, gid: int, mode: int):
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=str(target.parent))
    os.close(fd)
    try:
        shutil.copyfile(source, temp_name)
        os.chown(temp_name, uid, gid)
        os.chmod(temp_name, mode)
        os.replace(temp_name, target)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


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
    _atomic_write(Path(MADDY_CONF), content)


def _configured_domains(content: str) -> set[str]:
    primary_match = re.search(
        r"^\$\(primary_domain\)\s*=\s*([^\s#]+)",
        content,
        re.MULTILINE,
    )
    local_match = re.search(
        r"^\$\(local_domains\)\s*=\s*(.+)$",
        content,
        re.MULTILINE,
    )
    if not primary_match or not local_match:
        raise RuntimeError("Maddy primary/local domain configuration is missing.")
    primary = primary_match.group(1).strip().lower()
    domains = {
        token.strip().lower()
        for token in local_match.group(1).split()
        if token != "$(primary_domain)"
    }
    domains.add(primary)
    return domains


def _certificate_pairs() -> list[tuple[Path, Path]]:
    pairs: list[tuple[Path, Path]] = []
    if MADDY_CERTS_DIR.exists():
        for directory in sorted(MADDY_CERTS_DIR.iterdir()):
            if not directory.is_dir() or not HOST_RE.fullmatch(directory.name):
                continue
            cert = directory / "fullchain.pem"
            key = directory / "privkey.pem"
            if cert.is_file() and key.is_file():
                pairs.append((cert, key))
    if not pairs:
        legacy_cert = MADDY_CERTS_DIR / "fullchain.pem"
        legacy_key = MADDY_CERTS_DIR / "privkey.pem"
        if legacy_cert.is_file() and legacy_key.is_file():
            pairs.append((legacy_cert, legacy_key))
    return pairs


def _tls_line(pairs: list[tuple[Path, Path]]) -> str:
    if not pairs:
        raise RuntimeError("No readable Maddy TLS certificate pair was found.")
    values = " ".join(f"{cert} {key}" for cert, key in pairs)
    return f"tls file {values}"


def sync_certificate(mail_host: str):
    """Install a Let's Encrypt pair and retain all existing SNI certificates."""
    mail_host = mail_host.strip().lower()
    match = HOST_RE.fullmatch(mail_host)
    if not match:
        raise ValueError("Certificate hostname must be mail.<configured-domain>.")

    old_conf = _read_conf()
    if match.group(1) not in _configured_domains(old_conf):
        raise ValueError("Certificate hostname is not a configured Maddy domain.")

    source_dir = LE_LIVE_DIR / mail_host
    source_cert = source_dir / "fullchain.pem"
    source_key = source_dir / "privkey.pem"
    if not source_cert.is_file() or not source_key.is_file():
        raise FileNotFoundError(f"Let's Encrypt certificate is missing for {mail_host}.")

    destination = MADDY_CERTS_DIR / mail_host
    destination.mkdir(parents=True, exist_ok=True)
    target_cert = destination / "fullchain.pem"
    target_key = destination / "privkey.pem"
    old_cert = target_cert.read_bytes() if target_cert.exists() else None
    old_key = target_key.read_bytes() if target_key.exists() else None

    try:
        uid = pwd.getpwnam("maddy").pw_uid
        gid = grp.getgrnam("maddy").gr_gid
        _atomic_copy(source_cert, target_cert, uid, gid, 0o644)
        _atomic_copy(source_key, target_key, uid, gid, 0o640)

        new_line = _tls_line(_certificate_pairs())
        new_conf, replacements = re.subn(
            r"^tls\s+file\s+.+$",
            new_line,
            old_conf,
            count=1,
            flags=re.MULTILINE,
        )
        if replacements != 1:
            raise RuntimeError("Maddy TLS configuration line was not found.")
        _write_conf(new_conf)

        restart = run(["systemctl", "restart", "maddy"], check=False)
        active = run(["systemctl", "is-active", "--quiet", "maddy"], check=False)
        if restart.returncode != 0 or active.returncode != 0:
            raise RuntimeError(
                restart.stderr.strip() or "Maddy did not become active after TLS update."
            )
    except Exception:
        _write_conf(old_conf)
        if old_cert is None:
            target_cert.unlink(missing_ok=True)
        else:
            target_cert.write_bytes(old_cert)
        if old_key is None:
            target_key.unlink(missing_ok=True)
        else:
            target_key.write_bytes(old_key)
        restart_maddy()
        raise

    print(f"OK: certificate installed for {mail_host}; Maddy SNI updated")


def remove_certificate(mail_host: str):
    """Remove one managed SNI pair while leaving all other domains intact."""
    mail_host = mail_host.strip().lower()
    if not HOST_RE.fullmatch(mail_host):
        raise ValueError("Certificate hostname must be mail.<configured-domain>.")
    destination = MADDY_CERTS_DIR / mail_host
    if not destination.is_dir():
        print(f"OK: no managed certificate exists for {mail_host}")
        return

    old_conf = _read_conf()
    old_cert = (destination / "fullchain.pem").read_bytes()
    old_key = (destination / "privkey.pem").read_bytes()
    try:
        shutil.rmtree(destination)
        new_line = _tls_line(_certificate_pairs())
        new_conf, replacements = re.subn(
            r"^tls\s+file\s+.+$",
            new_line,
            old_conf,
            count=1,
            flags=re.MULTILINE,
        )
        if replacements != 1:
            raise RuntimeError("Maddy TLS configuration line was not found.")
        _write_conf(new_conf)
        restart = run(["systemctl", "restart", "maddy"], check=False)
        active = run(["systemctl", "is-active", "--quiet", "maddy"], check=False)
        if restart.returncode != 0 or active.returncode != 0:
            raise RuntimeError(
                restart.stderr.strip() or "Maddy did not become active after TLS update."
            )
    except Exception:
        destination.mkdir(parents=True, exist_ok=True)
        restored_cert = destination / "fullchain.pem"
        restored_key = destination / "privkey.pem"
        restored_cert.write_bytes(old_cert)
        restored_key.write_bytes(old_key)
        uid = pwd.getpwnam("maddy").pw_uid
        gid = grp.getgrnam("maddy").gr_gid
        for path, mode in ((restored_cert, 0o644), (restored_key, 0o640)):
            os.chown(path, uid, gid)
            os.chmod(path, mode)
        _write_conf(old_conf)
        restart_maddy()
        raise
    print(f"OK: certificate removed for {mail_host}; remaining SNI pairs preserved")


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
            "  manage_maddy.py remove-domain <domain>\n"
            "  manage_maddy.py sync-cert <mail.domain>\n"
            "  manage_maddy.py remove-cert <mail.domain>",
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

    elif action == "sync-cert":
        try:
            sync_certificate(arg)
        except (OSError, RuntimeError, ValueError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)

    elif action == "remove-cert":
        try:
            remove_certificate(arg)
        except (OSError, RuntimeError, ValueError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)

    else:
        print(f"Error: unknown action '{action}'", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
