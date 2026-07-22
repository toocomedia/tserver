"""
backend/plugins/maddy/service.py — Maddy Mail Server Management Service.
Handles system service checks, mailbox account CRUD operations, and
automated PowerDNS mail record provisioning and cleanup.
"""
import os
import json
import logging
import subprocess
import shutil
import socket
from pathlib import Path
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

ACCOUNTS_FILE = Path("/var/lib/maddy/accounts.json") if os.name != "nt" else Path(os.getenv("TEMP", "C:/tmp")) / "maddy_accounts.json"


class MaddyService:

    def is_installed(self) -> bool:
        """Check if maddy binary exists on system."""
        return shutil.which("maddy") is not None or os.path.exists("/usr/local/bin/maddy")

    def get_status(self) -> Dict[str, Any]:
        """Check Maddy system service status, RAM usage, and port availability."""
        installed = self.is_installed()
        active = False
        ram_mb = 0.0
        pid = None

        if installed and os.name != "nt":
            try:
                res = subprocess.run(["systemctl", "is-active", "maddy"], capture_output=True, text=True)
                active = (res.stdout.strip() == "active")

                if active:
                    pid_res = subprocess.run(["pgrep", "-f", "maddy"], capture_output=True, text=True)
                    pids = pid_res.stdout.strip().split()
                    if pids:
                        pid = int(pids[0])
                        ps_res = subprocess.run(["ps", "-o", "rss=", "-p", str(pid)], capture_output=True, text=True)
                        rss_kb = float(ps_res.stdout.strip() or 0)
                        ram_mb = round(rss_kb / 1024.0, 1)
            except Exception as exc:
                logger.warning("Error querying Maddy service status: %s", exc)

        ports = {
            "25": self._check_port(25),
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
        """Check if port is listening locally."""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.5)
                return s.connect_ex(("127.0.0.1", port)) == 0
        except Exception:
            return False

    def list_accounts(self) -> List[Dict[str, str]]:
        """List created mail accounts."""
        if not ACCOUNTS_FILE.exists():
            return []
        try:
            with open(ACCOUNTS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as exc:
            logger.error("Error reading mail accounts file: %s", exc)
            return []

    def create_account(self, email: str, password: str) -> bool:
        """Add a new mailbox account."""
        accounts = self.list_accounts()
        if any(a["email"].lower() == email.lower() for a in accounts):
            raise ValueError(f"Account '{email}' already exists.")

        # Execute CLI if installed
        if self.is_installed() and os.name != "nt":
            try:
                subprocess.run(["maddy", "creds", "create", email], input=f"{password}\n{password}\n", text=True, check=True)
                subprocess.run(["maddy", "imap-acct", "create", email], check=True)
            except Exception as exc:
                logger.warning("Maddy CLI account creation warning: %s", exc)

        accounts.append({"email": email, "created_at": str(Path(__file__).stat().st_mtime)})
        ACCOUNTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(ACCOUNTS_FILE, "w", encoding="utf-8") as f:
            json.dump(accounts, f, indent=2)
        return True

    def delete_account(self, email: str) -> bool:
        """Delete an existing mailbox account."""
        accounts = self.list_accounts()
        filtered = [a for a in accounts if a["email"].lower() != email.lower()]

        if self.is_installed() and os.name != "nt":
            try:
                subprocess.run(["maddy", "creds", "remove", email], check=False)
                subprocess.run(["maddy", "imap-acct", "remove", email], check=False)
            except Exception as exc:
                logger.warning("Maddy CLI account deletion warning: %s", exc)

        with open(ACCOUNTS_FILE, "w", encoding="utf-8") as f:
            json.dump(filtered, f, indent=2)
        return True

    async def auto_setup_dns_records(self, domain_name: str, server_ip: str) -> Dict[str, Any]:
        """
        Auto-create PowerDNS records for mail hosting:
        - A record: mail.domain -> server_ip
        - MX record: @ -> 10 mail.domain.
        - TXT SPF: @ -> "v=spf1 mx ip4:server_ip ~all"
        - TXT DMARC: _dmarc.domain -> "v=DMARC1; p=none;"
        - TXT DKIM: default._domainkey.domain -> "v=DKIM1; k=rsa; p=..."
        """
        from services import dns_service

        records_to_create = [
            {"name": f"mail.{domain_name}", "type": "A", "content": server_ip, "ttl": 3600},
            {"name": domain_name, "type": "MX", "content": f"10 mail.{domain_name}.", "ttl": 3600},
            {"name": domain_name, "type": "TXT", "content": f"\"v=spf1 mx ip4:{server_ip} ~all\"", "ttl": 3600},
            {"name": f"_dmarc.{domain_name}", "type": "TXT", "content": "\"v=DMARC1; p=none;\"", "ttl": 3600},
            {"name": f"default._domainkey.{domain_name}", "type": "TXT", "content": "\"v=DKIM1; k=rsa; p=MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQDz...\"", "ttl": 3600},
        ]

        created_count = 0
        for rec in records_to_create:
            try:
                await dns_service.create_record(
                    domain_name=domain_name,
                    record_name=rec["name"],
                    record_type=rec["type"],
                    content=rec["content"],
                    ttl=rec["ttl"],
                )
                created_count += 1
            except Exception as exc:
                logger.warning("Failed creating record %s (%s): %s", rec["name"], rec["type"], exc)

        return {"domain": domain_name, "created_records": created_count}

    async def remove_dns_records(self, domain_name: str) -> Dict[str, Any]:
        """Clean up mail-related DNS records from PowerDNS."""
        from services import dns_service

        target_names = [f"mail.{domain_name}", domain_name, f"_dmarc.{domain_name}", f"default._domainkey.{domain_name}"]
        existing = await dns_service.list_records(domain_name)
        deleted_count = 0

        for rrset in existing:
            rec_name = rrset["name"].rstrip(".")
            rec_type = rrset["type"]
            if rec_name in target_names and rec_type in ["MX", "TXT"] or (rec_name == f"mail.{domain_name}" and rec_type == "A"):
                try:
                    await dns_service.delete_rrset(domain_name, rec_name, rec_type)
                    deleted_count += 1
                except Exception as exc:
                    logger.warning("Failed deleting record %s (%s): %s", rec_name, rec_type, exc)

        return {"domain": domain_name, "deleted_records": deleted_count}


maddy_service = MaddyService()
