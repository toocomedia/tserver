"""
backend/plugins/rspamd/service.py — Rspamd Spam Filter Management Service.

Handles system service checks, Rspamd HTTP API statistics retrieval, score threshold management,
and automatic integration with Maddy Mail Server.
"""
import os
import json
import logging
import subprocess
import shutil
import socket
import urllib.request
import urllib.error
from pathlib import Path
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

# Path to Maddy configuration file
MADDY_CONF = Path("/etc/maddy/maddy.conf")

# Privileged helper script path
MANAGE_SCRIPT = Path(__file__).parent / "scripts" / "manage_rspamd.py"


class RspamdService:

    # ------------------------------------------------------------------
    # Installation & Status
    # ------------------------------------------------------------------

    def is_installed(self) -> bool:
        """Check if rspamd binary exists on the system."""
        return shutil.which("rspamd") is not None or os.path.exists("/usr/bin/rspamd") or os.path.exists("/usr/local/bin/rspamd")

    def is_maddy_integrated(self) -> bool:
        """Check if Maddy configuration is patched with Rspamd check."""
        if not MADDY_CONF.exists():
            return False
        try:
            content = MADDY_CONF.read_text(encoding="utf-8")
            return "rspamd http://127.0.0.1:11333" in content or "check.rspamd" in content
        except Exception as exc:
            logger.warning("Error reading maddy.conf for Rspamd check: %s", exc)
            return False

    def get_status(self) -> Dict[str, Any]:
        """Check Rspamd service status, RAM usage, port listening, and Maddy integration."""
        installed = self.is_installed()
        active = False
        ram_mb = 0.0
        pid = None

        if installed and os.name != "nt":
            try:
                res = subprocess.run(
                    ["systemctl", "is-active", "rspamd"],
                    capture_output=True, text=True,
                )
                active = res.stdout.strip() == "active"

                if active:
                    pid_res = subprocess.run(
                        ["pgrep", "-f", "rspamd"],
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
                logger.warning("Error querying Rspamd service status: %s", exc)

        port_listening = self._check_port(11333)
        maddy_integrated = self.is_maddy_integrated()

        return {
            "installed": installed,
            "running": active,
            "port_listening": port_listening,
            "maddy_integrated": maddy_integrated,
            "ram_mb": ram_mb if active else 0,
            "pid": pid,
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
    # Rspamd Statistics API
    # ------------------------------------------------------------------

    def get_stats(self) -> Dict[str, Any]:
        """Query statistics directly from Rspamd HTTP API (127.0.0.1:11333/stat)."""
        default_stats = {
            "scanned": 0,
            "clean": 0,
            "spam": 0,
            "junk": 0,
            "rejected": 0,
            "learned": 0,
            "spam_percentage": 0.0,
        }

        if not self._check_port(11333):
            return default_stats

        try:
            req = urllib.request.Request("http://127.0.0.1:11333/stat", headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    scanned = data.get("scanned", 0)
                    actions = data.get("actions", {})
                    reject = actions.get("reject", 0)
                    add_header = actions.get("add header", 0) + actions.get("rewrite subject", 0)
                    no_action = actions.get("no action", 0)
                    learned = data.get("learned", 0)

                    spam_count = reject + add_header
                    spam_percentage = round((spam_count / scanned * 100), 1) if scanned > 0 else 0.0

                    return {
                        "scanned": scanned,
                        "clean": no_action,
                        "spam": spam_count,
                        "junk": add_header,
                        "rejected": reject,
                        "learned": learned,
                        "spam_percentage": spam_percentage,
                    }
        except Exception as exc:
            logger.debug("Failed to query Rspamd /stat endpoint: %s", exc)

        return default_stats

    # ------------------------------------------------------------------
    # Threshold Configuration
    # ------------------------------------------------------------------

    def get_thresholds(self) -> Dict[str, float]:
        """Read active score thresholds from Rspamd local.d/actions.conf or return defaults."""
        actions_conf = Path("/etc/rspamd/local.d/actions.conf")
        reject = 15.0
        add_header = 6.0

        if actions_conf.exists():
            try:
                content = actions_conf.read_text(encoding="utf-8")
                for line in content.splitlines():
                    line = line.strip()
                    if line.startswith("reject"):
                        reject = float(line.split("=")[1].strip().rstrip(";"))
                    elif line.startswith("add_header"):
                        add_header = float(line.split("=")[1].strip().rstrip(";"))
            except Exception as exc:
                logger.warning("Error reading Rspamd actions.conf: %s", exc)

        return {
            "reject": reject,
            "add_header": add_header,
        }

    def update_thresholds(self, reject: float, add_header: float) -> Dict[str, Any]:
        """Update Rspamd action thresholds using privileged script."""
        try:
            cmd = self._get_manage_cmd("update-thresholds", str(reject), str(add_header))
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            if res.returncode == 0:
                return {"success": True, "message": "Spam thresholds updated successfully"}
            return {"success": False, "error": res.stderr.strip() or "Failed to update thresholds"}
        except Exception as exc:
            logger.error("Error executing threshold update: %s", exc)
            return {"success": False, "error": str(exc)}

    # ------------------------------------------------------------------
    # Service Management & Maddy Integration Sync
    # ------------------------------------------------------------------

    def control_service(self, action: str) -> Dict[str, Any]:
        """Start, stop, or restart Rspamd daemon."""
        if action not in ("start", "stop", "restart"):
            return {"success": False, "error": f"Invalid action '{action}'"}

        try:
            cmd = self._get_manage_cmd("service-control", action)
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            if res.returncode == 0:
                return {"success": True, "message": f"Rspamd {action}ed successfully"}
            return {"success": False, "error": res.stderr.strip() or f"Failed to {action} Rspamd"}
        except Exception as exc:
            logger.error("Error executing service control '%s': %s", action, exc)
            return {"success": False, "error": str(exc)}

    def sync_maddy_integration(self, enable: bool) -> Dict[str, Any]:
        """Patch or unpatch Maddy configuration to route incoming mail through Rspamd."""
        mode = "enable" if enable else "disable"
        try:
            cmd = self._get_manage_cmd("sync-maddy", mode)
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
            if res.returncode == 0:
                return {"success": True, "message": f"Maddy integration {mode}d successfully"}
            return {"success": False, "error": res.stderr.strip() or "Failed to sync Maddy integration"}
        except Exception as exc:
            logger.error("Error syncing Maddy integration (%s): %s", mode, exc)
            return {"success": False, "error": str(exc)}

    # ------------------------------------------------------------------
    # Helper & Lifecycle Contracts
    # ------------------------------------------------------------------

    def _get_manage_cmd(self, *args: str) -> List[str]:
        """Build execution command for manage_rspamd.py helper script."""
        if os.name == "nt":
            return ["python", str(MANAGE_SCRIPT)] + list(args)
        if os.geteuid() == 0:
            return ["/usr/bin/python3", str(MANAGE_SCRIPT)] + list(args)
        return ["sudo", "/usr/bin/python3", str(MANAGE_SCRIPT)] + list(args)

    def get_usage_details(self) -> Dict[str, str]:
        """Return Rspamd metrics details for the Panel Usage page."""
        status = self.get_status()
        stats = self.get_stats()
        is_active = status.get("running", False)
        
        if is_active:
            text = f"Active | Scanned: {stats.get('scanned', 0)} | Spam blocked: {stats.get('spam', 0)}"
        else:
            text = "Service Inactive"

        return {
            "details": text,
        }

    def pause(self) -> None:
        """Stop owned background service without removing configurations (Plugin Contract)."""
        logger.info("Pausing Rspamd service...")
        self.control_service("stop")

    def resume(self) -> None:
        """Resume owned background service (Plugin Contract)."""
        logger.info("Resuming Rspamd service...")
        self.control_service("start")
