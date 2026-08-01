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
RSPAMD_MARKER = Path("/etc/srv-panel/maddy-rspamd.enabled")
HOST_RE = re.compile(
    r"^mail\.([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+)$"
)
DOMAIN_RE = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$"
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


def _mail_domains(domains: list[str]) -> list[str]:
    """Normalize the panel-owned domains used in a generated Maddy config."""
    normalized = []
    for domain in domains:
        domain = domain.strip().lower()
        if not DOMAIN_RE.fullmatch(domain) or domain.endswith(".local"):
            raise ValueError(f"Invalid mail domain: {domain}")
        if domain not in normalized:
            normalized.append(domain)
    if not normalized:
        raise ValueError("Add a mail domain in the panel before rebuilding Maddy.")
    return normalized


def _restore_letsencrypt_certificates(domains: list[str]) -> None:
    """Restore copied Maddy SNI certificates after a Maddy reinstall."""
    uid = pwd.getpwnam("maddy").pw_uid
    gid = grp.getgrnam("maddy").gr_gid
    for domain in domains:
        mail_host = f"mail.{domain}"
        source = LE_LIVE_DIR / mail_host
        cert = source / "fullchain.pem"
        key = source / "privkey.pem"
        if cert.is_file() and key.is_file():
            target = MADDY_CERTS_DIR / mail_host
            _atomic_copy(cert, target / "fullchain.pem", uid, gid, 0o644)
            _atomic_copy(key, target / "privkey.pem", uid, gid, 0o640)


def _configured_mail_domains() -> list[str]:
    """Read the real mail domains from a working Maddy configuration."""
    content = _read_conf()
    primary = re.search(
        r"^\$\(primary_domain\)\s*=\s*([^\s#]+)", content, re.MULTILINE
    )
    local = re.search(
        r"^\$\(local_domains\)\s*=\s*(.+)$", content, re.MULTILINE
    )
    if not primary or not local:
        raise RuntimeError("Maddy primary/local domain configuration is missing.")
    domains = [primary.group(1)]
    domains.extend(
        primary.group(1) if item == "$(primary_domain)" else item
        for item in local.group(1).split()
    )
    return _mail_domains(domains)


def _rspamd_check() -> str:
    if not RSPAMD_MARKER.is_file():
        return ""
    return """        rspamd {
            api_path http://127.0.0.1:11333
            io_error_action ignore
            error_resp_action ignore
        }
"""


def set_rspamd(enabled: bool) -> None:
    """Persist Rspamd integration and rebuild only from existing mail domains."""
    previous = RSPAMD_MARKER.is_file()
    try:
        if enabled:
            RSPAMD_MARKER.parent.mkdir(parents=True, exist_ok=True)
            RSPAMD_MARKER.touch(mode=0o600, exist_ok=True)
        else:
            RSPAMD_MARKER.unlink(missing_ok=True)
        if not Path(MADDY_CONF).is_file():
            print("OK: Rspamd integration preference saved for the next Maddy install")
            return
        repair_config(_configured_mail_domains())
    except BaseException:
        if previous:
            RSPAMD_MARKER.parent.mkdir(parents=True, exist_ok=True)
            RSPAMD_MARKER.touch(mode=0o600, exist_ok=True)
        else:
            RSPAMD_MARKER.unlink(missing_ok=True)
        raise
    print(f"OK: Rspamd integration {'enabled' if enabled else 'disabled'}")


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

def repair_config(domains: list[str]):
    """Rebuild Maddy from the panel's configured mail domains."""
    if not os.path.exists(MADDY_CONF):
        print("ERROR: maddy.conf does not exist", file=sys.stderr)
        sys.exit(1)

    try:
        mail_domains = _mail_domains(domains)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    primary_domain_val = mail_domains[0]
    hostname_val = f"mail.{primary_domain_val}"
    local_domains_val = " ".join(mail_domains)
    rspamd_check = _rspamd_check()
    try:
        _restore_letsencrypt_certificates(mail_domains)
        tls_line = _tls_line(_certificate_pairs())
    except (KeyError, OSError, RuntimeError) as exc:
        print(f"ERROR: unable to restore Maddy TLS certificates: {exc}", file=sys.stderr)
        sys.exit(1)

    fresh_conf = f"""# Maddy Mail Server - default configuration file
$(hostname) = {hostname_val}
$(primary_domain) = {primary_domain_val}
$(local_domains) = {local_domains_val}

{tls_line}

auth.pass_table local_authdb {{
    table sql_table {{
        driver sqlite3
        dsn /var/lib/maddy/credentials.db
        table_name credentials
    }}
}}

storage.imapsql local_mailboxes {{
    driver sqlite3
    dsn /var/lib/maddy/imapsql.db
}}

hostname $(hostname)

table.chain local_rewrites {{
    optional_step regexp "(.+)\\+(.+)@(.+)" "$1@$3"
    optional_step static {{
        entry postmaster postmaster@$(primary_domain)
    }}
}}

msgpipeline local_routing {{
    destination postmaster $(local_domains) {{
        modify {{
            replace_rcpt &local_rewrites
        }}
        deliver_to &local_mailboxes
    }}
    default_destination {{
        reject 550 5.1.1 "User doesn't exist"
    }}
}}

smtp tcp://0.0.0.0:25 {{
    limits {{
        all rate 20 1s
        all concurrency 10
    }}

    dmarc yes
    check {{
        require_mx_record
        dkim
        spf
{rspamd_check}
    }}

    source $(local_domains) {{
        reject 501 5.1.8 "Use Submission for outgoing SMTP"
    }}
    default_source {{
        destination postmaster $(local_domains) {{
            deliver_to &local_routing
        }}
        default_destination {{
            reject 550 5.1.1 "User doesn't exist"
        }}
    }}
}}

submission tls://0.0.0.0:465 tcp://0.0.0.0:587 {{
    limits {{
        all rate 50 1s
    }}

    auth &local_authdb
    insecure_auth yes

    source $(local_domains) {{
        check {{
            authorize_sender {{
                prepare_email &local_rewrites
                user_to_email identity
            }}
        }}
        destination postmaster $(local_domains) {{
            deliver_to &local_routing
        }}
        default_destination {{
            modify {{
                dkim $(primary_domain) $(local_domains) default
            }}
            deliver_to &remote_queue
        }}
    }}
    default_source {{
        reject 501 5.1.8 "Non-local sender domain"
    }}
}}

target.remote outbound_delivery {{
    limits {{
        destination rate 20 1s
        destination concurrency 10
    }}
    mx_auth {{
        dane
        mtasts {{
            cache fs
            fs_dir /var/lib/maddy/mtasts_cache/
        }}
        local_policy {{
            min_tls_level encrypted
            min_mx_level none
        }}
    }}
}}

target.queue remote_queue {{
    target &outbound_delivery
    autogenerated_msg_domain $(primary_domain)
    bounce {{
        destination postmaster $(local_domains) {{
            deliver_to &local_routing
        }}
        default_destination {{
            reject 550 5.0.0 "Refusing to send DSNs to non-local addresses"
        }}
    }}
}}

imap tls://0.0.0.0:993 tcp://0.0.0.0:143 {{
    auth &local_authdb
    storage &local_mailboxes
    insecure_auth yes
}}
"""
    old_conf = _read_conf()
    backup_path = Path(MADDY_CONF).with_suffix(".conf.bak")
    try:
        shutil.copy2(MADDY_CONF, backup_path)
    except Exception:
        pass

    _write_conf(fresh_conf)
    restart = run(["systemctl", "restart", "maddy"], check=False)
    active = run(["systemctl", "is-active", "--quiet", "maddy"], check=False)
    if restart.returncode != 0 or active.returncode != 0:
        _write_conf(old_conf)
        restart_maddy()
        print(
            restart.stderr.strip() or "Maddy rejected the rebuilt configuration.",
            file=sys.stderr,
        )
        sys.exit(1)
    print("Maddy configuration repaired and service restarted.")


def diagnose_maddy():
    """Fetch diagnostic information for Maddy service and logs."""
    logs = run(["journalctl", "-u", "maddy", "-n", "20", "--no-pager"], check=False).stdout
    status = run(["systemctl", "status", "maddy"], check=False).stdout
    print(f"--- MADDY DIAGNOSTIC LOGS ---\n{logs}\n\n--- SERVICE STATUS ---\n{status}")


def main():
    if len(sys.argv) < 2:
        print(
            "Usage:\n"
            "  manage_maddy.py create <email> <password>\n"
            "  manage_maddy.py delete <email>\n"
            "  manage_maddy.py add-domain <domain>\n"
            "  manage_maddy.py remove-domain <domain>\n"
            "  manage_maddy.py sync-cert <mail.domain>\n"
            "  manage_maddy.py remove-cert <mail.domain>\n"
            "  manage_maddy.py repair-config <domain> [domain ...]\n"
            "  manage_maddy.py rspamd <enable|disable>\n"
            "  manage_maddy.py diagnose",
            file=sys.stderr,
        )
        sys.exit(1)

    action = sys.argv[1]
    
    if action == "repair-config":
        repair_config(sys.argv[2:])
        return
    if action == "rspamd":
        if len(sys.argv) != 3 or sys.argv[2] not in {"enable", "disable"}:
            print("Error: rspamd requires enable or disable", file=sys.stderr)
            sys.exit(1)
        set_rspamd(sys.argv[2] == "enable")
        return
    elif action == "diagnose":
        diagnose_maddy()
        return

    if len(sys.argv) < 3:
        print("Error: missing required argument", file=sys.stderr)
        sys.exit(1)

    arg = sys.argv[2]

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
