"""
backend/plugins/maddy/service.py — Maddy Mail Server Management Service.

Handles system service checks, mailbox account CRUD via maddy CLI, and
automated PowerDNS mail record provisioning and cleanup.

Account list is sourced directly from maddy's credentials SQLite database so
the panel always reflects real server state (no stale JSON cache).
"""
import os
import logging
import subprocess
import shutil
import socket
import sqlite3
from pathlib import Path
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

# Path to maddy's credentials SQLite database (created by maddy itself)
MADDY_CREDS_DB = Path("/var/lib/maddy/credentials.db")

# Privileged helper script path
MANAGE_SCRIPT = Path(__file__).parent / "scripts" / "manage_maddy.py"


class MaddyService:

    # ------------------------------------------------------------------
    # Installation / Status
    # ------------------------------------------------------------------

    def is_installed(self) -> bool:
        """Check if maddy binary exists on the system."""
        return shutil.which("maddy") is not None or os.path.exists("/usr/local/bin/maddy")

    def get_status(self) -> Dict[str, Any]:
        """Check Maddy service status, RAM usage, and port availability."""
        installed = self.is_installed()
        active = False
        ram_mb = 0.0
        pid = None

        if installed and os.name != "nt":
            try:
                res = subprocess.run(
                    ["systemctl", "is-active", "maddy"],
                    capture_output=True, text=True,
                )
                active = res.stdout.strip() == "active"

                if active:
                    pid_res = subprocess.run(
                        ["pgrep", "-f", "maddy"],
                        capture_output=True, text=True,
                    )
                    pids = pid_res.stdout.strip().split()
                    if pids:
                        pid = int(pids[0])
                        ps_res = subprocess.run(
                            ["ps", "-o", "rss=", "-p", str(pid)],
                            capture_output=True, text=True,
                        )
                        rss_kb = float(ps_res.stdout.strip() or 0)
                        ram_mb = round(rss_kb / 1024.0, 1)
            except Exception as exc:
                logger.warning("Error querying Maddy service status: %s", exc)

        ports = {
            "25":  self._check_port(25),
            "587": self._check_port(587),
            "465": self._check_port(465),
            "993": self._check_port(993),
        }

        return {
            "installed": installed,
            "running": active,
            "ram_mb": ram_mb if active else 0,
            "pid": pid,
            "ports": ports,
        }

    def _check_port(self, port: int) -> bool:
        """Check if a port is listening locally."""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.5)
                return s.connect_ex(("127.0.0.1", port)) == 0
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Account Management
    # ------------------------------------------------------------------

    def list_accounts(self) -> List[Dict[str, str]]:
        """
        List mail accounts by reading maddy's own credentials SQLite database.

        Falls back to an empty list if the database is not yet accessible
        (e.g. maddy not installed, or panel user lacks read permission).
        """
        if os.name == "nt":
            # Windows dev mode — no real maddy DB
            return []

        if not MADDY_CREDS_DB.exists():
            return []

        try:
            conn = sqlite3.connect(str(MADDY_CREDS_DB))
            try:
                rows = conn.execute("SELECT key FROM credentials ORDER BY key").fetchall()
                return [{"email": row[0], "created_at": "Active"} for row in rows]
            finally:
                conn.close()
        except sqlite3.OperationalError as exc:
            # Table may not exist yet if maddy just started for the first time
            logger.warning("Could not read maddy credentials DB: %s", exc)
            return []
        except PermissionError:
            logger.error(
                "Permission denied reading %s. "
                "Ensure the panel user is in the 'maddy' group or the DB has world-read permission.",
                MADDY_CREDS_DB,
            )
            return []
        except Exception as exc:
            logger.error("Error reading maddy credentials DB: %s", exc)
            return []

    def create_account(self, email: str, password: str) -> bool:
        """
        Create a new mailbox account via the privileged manage_maddy.py helper.

        The helper uses 'maddy creds create' so maddy handles its own password
        hash format — no raw SQLite writes, no bcrypt guessing.
        """
        # Validate email format minimally
        if "@" not in email or not email.strip():
            raise ValueError("Invalid email address.")

        # Check for duplicate using the real DB
        existing = [a["email"].lower() for a in self.list_accounts()]
        if email.lower() in existing:
            raise ValueError(f"Account '{email}' already exists.")

        if os.name == "nt":
            # Windows dev mode — simulate success
            logger.info("[DEV] Mock create account: %s", email)
            return True

        if not self.is_installed():
            raise RuntimeError("Maddy is not installed on this system.")

        res = subprocess.run(
            ["sudo", "-n", "python3", str(MANAGE_SCRIPT), "create", email, password],
            capture_output=True,
            text=True,
        )
        if res.returncode != 0:
            err = res.stderr.strip() or res.stdout.strip()
            if "a password is required" in err or "sudo:" in err:
                raise PermissionError(
                    "The panel needs NOPASSWD sudo access for manage_maddy.py. "
                    "Add the following to /etc/sudoers.d/panel:\n"
                    f"  panel ALL=(root) NOPASSWD: /usr/bin/python3 {MANAGE_SCRIPT}"
                )
            raise RuntimeError(f"Failed to create account '{email}': {err}")

        logger.info("Created mail account: %s", email)
        return True

    def delete_account(self, email: str) -> bool:
        """
        Delete an existing mailbox account via the privileged manage_maddy.py helper.
        """
        if not email.strip():
            raise ValueError("Email address is required.")

        if os.name == "nt":
            logger.info("[DEV] Mock delete account: %s", email)
            return True

        if not self.is_installed():
            raise RuntimeError("Maddy is not installed on this system.")

        res = subprocess.run(
            ["sudo", "-n", "python3", str(MANAGE_SCRIPT), "delete", email],
            capture_output=True,
            text=True,
        )
        if res.returncode != 0:
            err = res.stderr.strip() or res.stdout.strip()
            if "a password is required" in err or "sudo:" in err:
                raise PermissionError(
                    "The panel needs NOPASSWD sudo access for manage_maddy.py."
                )
            raise RuntimeError(f"Failed to delete account '{email}': {err}")

        logger.info("Deleted mail account: %s", email)
        return True

    # ------------------------------------------------------------------
    # DNS record provisioning
    # ------------------------------------------------------------------

    async def auto_setup_dns_records(self, domain_name: str, server_ip: str) -> Dict[str, Any]:
        """
        Auto-create PowerDNS records for mail hosting:
          A      mail.<domain>       → server_ip
          MX     @                  → 10 mail.<domain>.
          TXT    @                  → SPF record
          TXT    _dmarc             → DMARC policy (none)
          TXT    default._domainkey → placeholder DKIM (replace with real key)
        """
        from services import dns_service

        records_to_create = [
            {"name": "mail",               "type": "A",   "content": server_ip,                              "ttl": 3600},
            {"name": "@",                  "type": "MX",  "content": f"10 mail.{domain_name}.",              "ttl": 3600},
            {"name": "@",                  "type": "TXT", "content": f"v=spf1 mx ip4:{server_ip} ~all",     "ttl": 3600},
            {"name": "_dmarc",             "type": "TXT", "content": "v=DMARC1; p=none;",                    "ttl": 3600},
            {"name": "default._domainkey", "type": "TXT", "content": "v=DKIM1; k=rsa; p=PLACEHOLDER",       "ttl": 3600},
        ]

        created_count = 0
        for rec in records_to_create:
            try:
                await dns_service.add_record(
                    domain=domain_name,
                    name=rec["name"],
                    rtype=rec["type"],
                    content=rec["content"],
                    ttl=rec["ttl"],
                )
                created_count += 1
            except Exception as exc:
                logger.error(
                    "Failed creating mail record %s (%s) for %s: %s",
                    rec["name"], rec["type"], domain_name, exc,
                )

        return {"domain": domain_name, "created_records": created_count}

    async def remove_dns_records(self, domain_name: str) -> Dict[str, Any]:
        """Clean up mail-related DNS records from PowerDNS."""
        from services import dns_service

        target_records = [
            {"name": "mail",               "type": "A"},
            {"name": "@",                  "type": "MX"},
            {"name": "@",                  "type": "TXT"},
            {"name": "_dmarc",             "type": "TXT"},
            {"name": "default._domainkey", "type": "TXT"},
        ]
        deleted_count = 0

        for rec in target_records:
            try:
                await dns_service.delete_record(
                    domain=domain_name,
                    name=rec["name"],
                    rtype=rec["type"],
                )
                deleted_count += 1
            except Exception as exc:
                logger.warning(
                    "Failed deleting record %s (%s) for %s: %s",
                    rec["name"], rec["type"], domain_name, exc,
                )

        return {"domain": domain_name, "deleted_records": deleted_count}


maddy_service = MaddyService()
