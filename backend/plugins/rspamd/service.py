"""
backend/plugins/rspamd/service.py — Rspamd Spam Filter Management Service.
"""
import os
import json
import logging
import subprocess
import shutil
import socket
import urllib.request
from pathlib import Path
from typing import Dict, Any, List

logger = logging.getLogger(__name__)

MADDY_CONF = Path("/etc/maddy/maddy.conf")
MANAGE_SCRIPT = Path(__file__).parent / "scripts" / "manage_rspamd.py"
RSPAMD_SCAN_PORT = 11333
RSPAMD_CONTROLLER_PORT = 11334


class RspamdService:

    def is_installed(self) -> bool:
        """Check if rspamd binary exists on the system."""
        return shutil.which("rspamd") is not None or os.path.exists("/usr/bin/rspamd") or os.path.exists("/usr/local/bin/rspamd")

    def is_maddy_integrated(self) -> bool:
        """Check if Maddy configuration is patched with Rspamd check."""
        if not MADDY_CONF.exists():
            return False
        try:
            content = MADDY_CONF.read_text(encoding="utf-8")
            return (
                "rspamd http://127.0.0.1:11333" in content
                or "rspamd {" in content
                or "check.rspamd" in content
            )
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
                res = subprocess.run(["systemctl", "is-active", "rspamd"], capture_output=True, text=True)
                active = res.stdout.strip() == "active"

                if active:
                    pid_res = subprocess.run(["pgrep", "-f", "rspamd"], capture_output=True, text=True)
                    pids = pid_res.stdout.strip().split()
                    if pids:
                        pid = int(pids[0])
                        ps_res = subprocess.run(["ps", "-o", "rss=", "-p", str(pid)], capture_output=True, text=True)
                        rss_kb = float(ps_res.stdout.strip() or 0)
                        ram_mb = round(rss_kb / 1024.0, 1)
            except Exception as exc:
                logger.warning("Error querying Rspamd service status: %s", exc)

        return {
            "installed": installed,
            "running": active,
            "port_listening": self._check_port(RSPAMD_SCAN_PORT),
            "maddy_integrated": self.is_maddy_integrated(),
            "ram_mb": ram_mb if active else 0,
            "pid": pid,
        }

    def _check_port(self, port: int) -> bool:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.5)
                return s.connect_ex(("127.0.0.1", port)) == 0
        except Exception:
            return False

    def get_stats(self) -> Dict[str, Any]:
        """Query Rspamd statistics from its loopback-only controller API."""
        defaults = {"scanned": 0, "clean": 0, "spam": 0, "junk": 0, "rejected": 0, "learned": 0, "spam_percentage": 0.0}
        if not self._check_port(RSPAMD_CONTROLLER_PORT):
            return defaults

        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{RSPAMD_CONTROLLER_PORT}/stat",
                headers={"Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    scanned = data.get("scanned", 0)
                    actions = data.get("actions", {})
                    reject = actions.get("reject", 0)
                    add_header = actions.get("add header", 0) + actions.get("rewrite subject", 0)
                    spam_count = reject + add_header
                    return {
                        "scanned": scanned,
                        "clean": actions.get("no action", 0),
                        "spam": spam_count,
                        "junk": add_header,
                        "rejected": reject,
                        "learned": data.get("learned", 0),
                        "spam_percentage": round((spam_count / scanned * 100), 1) if scanned > 0 else 0.0,
                    }
        except Exception as exc:
            logger.debug("Failed to query Rspamd /stat endpoint: %s", exc)
        return defaults

    def get_thresholds(self) -> Dict[str, float]:
        """Read active score thresholds from Rspamd local.d/actions.conf."""
        actions_conf = Path("/etc/rspamd/local.d/actions.conf")
        reject, add_header = 15.0, 6.0
        if actions_conf.exists():
            try:
                for line in actions_conf.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line.startswith("reject"):
                        reject = float(line.split("=")[1].strip().rstrip(";"))
                    elif line.startswith("add_header"):
                        add_header = float(line.split("=")[1].strip().rstrip(";"))
            except Exception as exc:
                logger.warning("Error reading Rspamd actions.conf: %s", exc)
        return {"reject": reject, "add_header": add_header}

    def update_thresholds(self, reject: float, add_header: float) -> Dict[str, Any]:
        """Update Rspamd action thresholds."""
        return self._run_manage_cmd("update-thresholds", str(reject), str(add_header), timeout=15)

    def control_service(self, action: str) -> Dict[str, Any]:
        """Start, stop, or restart Rspamd daemon."""
        if action not in ("start", "stop", "restart"):
            return {"success": False, "error": f"Invalid action '{action}'"}
        return self._run_manage_cmd("service-control", action, timeout=15)

    def sync_maddy_integration(self, enable: bool) -> Dict[str, Any]:
        """Patch or unpatch Maddy configuration."""
        return self._run_manage_cmd("sync-maddy", "enable" if enable else "disable", timeout=20)

    def install(self) -> Dict[str, Any]:
        """Trigger Rspamd installer script via privileged helper."""
        return self._run_manage_cmd("install", timeout=120)

    def uninstall(self) -> Dict[str, Any]:
        """Trigger Rspamd uninstaller script via privileged helper."""
        return self._run_manage_cmd("uninstall", timeout=60)

    def _run_manage_cmd(self, *args: str, timeout: int = 15) -> Dict[str, Any]:
        cmd = self._get_manage_cmd(*args)
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            if res.returncode == 0:
                return {"success": True, "message": res.stdout.strip() or "Success"}
            return {"success": False, "error": res.stderr.strip() or res.stdout.strip() or "Execution failed"}
        except Exception as exc:
            logger.error("Error executing command %s: %s", args, exc)
            return {"success": False, "error": str(exc)}

    def _get_manage_cmd(self, *args: str) -> List[str]:
        if os.name == "nt":
            return ["python", str(MANAGE_SCRIPT)] + list(args)
        if os.geteuid() == 0:
            return ["/usr/bin/python3", str(MANAGE_SCRIPT)] + list(args)
        return ["sudo", "/usr/bin/python3", str(MANAGE_SCRIPT)] + list(args)

    def get_usage_details(self) -> Dict[str, str]:
        status, stats = self.get_status(), self.get_stats()
        text = f"Active | Scanned: {stats.get('scanned', 0)} | Spam blocked: {stats.get('spam', 0)}" if status.get("running") else "Service Inactive"
        return {"details": text}

    def pause(self) -> None:
        self.control_service("stop")

    def resume(self) -> None:
        self.control_service("start")


# Export service instance for plugin auto-discovery
rspamd_service = RspamdService()
