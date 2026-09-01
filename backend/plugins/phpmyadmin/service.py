"""phpMyAdmin lifecycle: local PHP server unit, served behind panel auth."""
from __future__ import annotations

import json
import logging
import os
import re
import socket
import subprocess
import threading
from pathlib import Path
from typing import Any

import config

logger = logging.getLogger(__name__)

_VERSION_KEY = lambda value: tuple(int(part) for part in value.split("."))  # noqa: E731
PHP_STATE_PATH = Path("/var/lib/srv-panel/php-runtime/managed-versions.json")


class PhpMyAdminService:
    plugin_id = "phpmyadmin"
    config_version = "3"
    unit_name = "srv-panel-phpmyadmin"
    host = "127.0.0.1"
    default_port = 8090
    command_timeout = 15

    @property
    def port(self) -> int:
        """Port from state (chosen at install), env override, else default."""
        env_port = os.getenv("PHPMYADMIN_PORT")
        if env_port:
            try:
                return int(env_port)
            except ValueError:
                pass
        stored = self.read_state().get("port")
        if isinstance(stored, int) and 1024 <= stored <= 65535:
            return stored
        if os.name != "nt":
            unit_file = Path(f"/etc/systemd/system/{self.unit_name}.service")
            if unit_file.is_file():
                try:
                    content = unit_file.read_text(encoding="utf-8")
                    match = re.search(r"-S\s+(?:127\.0\.0\.1|localhost):(\d+)", content)
                    if match:
                        return int(match.group(1))
                except Exception:
                    pass
        return self.default_port

    def __init__(self) -> None:
        self._state_lock = threading.RLock()

    @property
    def data_dir(self) -> Path:
        configured = os.getenv("PHPMYADMIN_DATA_DIR")
        if configured:
            return Path(configured)
        if os.name == "nt":
            return Path(os.getenv("TEMP", "C:/tmp")) / "srv-panel-phpmyadmin"
        return Path("/opt/srv-panel/data/phpmyadmin")

    @property
    def htdocs(self) -> Path:
        return self.data_dir / "htdocs"

    @property
    def state_path(self) -> Path:
        return self.data_dir / "state.json"

    @property
    def secret_path(self) -> Path:
        return self.data_dir / "pma.secret"

    @property
    def marker_path(self) -> Path:
        return self.data_dir / "config_version"

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    # ---------------------------------------------------------------
    # Shell helpers
    # ---------------------------------------------------------------
    @staticmethod
    def _command_prefix() -> list[str]:
        if os.name == "nt":
            return []
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            return []
        return ["sudo", "-n"] if config.PRIVILEGED_SUDO else []

    def _run(
        self, command: list[str], *, timeout: int | None = None
    ) -> subprocess.CompletedProcess[str]:
        full_command = [*self._command_prefix(), *command]
        logger.info("Shell: %s", " ".join(full_command))
        return subprocess.run(
            full_command,
            capture_output=True,
            text=True,
            timeout=timeout or self.command_timeout,
            check=False,
            shell=False,
        )

    # ---------------------------------------------------------------
    # PHP runtime discovery
    # ---------------------------------------------------------------
    def php_version(self) -> str | None:
        """Highest panel-managed PHP version, else highest installed version."""
        try:
            data = json.loads(PHP_STATE_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        versions = [
            str(version)
            for version in data
            if isinstance(version, str) and re.fullmatch(r"^\d+\.\d+$", version)
        ]
        if not versions and os.name != "nt":
            try:
                res = subprocess.run(
                    ["dpkg-query", "-W", "-f=${db:Status-Abbrev}\t${Package}\n", "php*-fpm"],
                    capture_output=True, text=True, timeout=6, check=False
                )
                if res.returncode == 0:
                    for line in res.stdout.splitlines():
                        parts = line.strip().split("\t")
                        if len(parts) >= 2 and parts[0].startswith("ii"):
                            m = re.fullmatch(r"^php(\d+\.\d+)-fpm$", parts[1])
                            if m:
                                versions.append(m.group(1))
            except Exception:
                pass
        if not versions:
            return None
        return sorted(versions, key=_VERSION_KEY)[-1]

    def php_binary(self) -> str | None:
        """Path to the CLI binary for the active PHP version, if present."""
        version = self.php_version()
        if not version:
            return None
        candidates = [
            f"/usr/bin/php{version}",
            "/usr/bin/php",
        ]
        for candidate in candidates:
            if os.path.isfile(candidate):
                return candidate
        return None

    # ---------------------------------------------------------------
    # Status
    # ---------------------------------------------------------------
    def is_installed(self) -> bool:
        return (self.htdocs / "index.php").is_file()

    def needs_reconcile(self) -> bool:
        """True when installed files were built by older plugin code."""
        if not self.is_installed():
            return False
        try:
            marker = self.marker_path.read_text(encoding="utf-8").strip()
        except OSError:
            return True
        return marker != self.config_version

    @staticmethod
    def _port_open(host: str, port: int) -> bool:
        try:
            with socket.create_connection((host, port), timeout=1.5) as sock:
                if port != 3306:
                    # Send a minimal valid HTTP request so PHP's built-in CLI web
                    # server handles it cleanly without triggering speculative
                    # connection disconnect log warnings or worker process deadlocks.
                    try:
                        sock.sendall(b"HEAD / HTTP/1.0\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")
                        sock.recv(64)
                    except OSError:
                        pass
                return True
        except OSError:
            return False

    @staticmethod
    def mariadb_reachable() -> bool:
        if os.name == "nt":
            return False
        return PhpMyAdminService._port_open("127.0.0.1", 3306)

    def _unit_state(self) -> tuple[bool, str]:
        """Return (unit_exists, systemd active state)."""
        result = self._run(["systemctl", "is-active", self.unit_name], timeout=10)
        state = (result.stdout or "").strip().lower() or "unknown"
        exists = result.returncode in (0, 3) or state in (
            "active", "inactive", "failed", "activating", "deactivating",
        )
        return exists, state

    def unit_logs(self, lines: int = 8) -> str:
        """Last journal lines for the server unit (empty when unavailable)."""
        result = self._run(
            ["journalctl", "-u", self.unit_name, "-n", str(lines), "--no-pager", "-o", "cat"],
            timeout=10,
        )
        return (result.stdout or result.stderr or "").strip()

    def get_status(self) -> dict[str, Any]:
        status = {
            "installed": False,
            "running": False,
            "healthy": False,
            "state": "missing",
            "error": None,
            "php_version": self.php_version(),
            "port": self.port,
            "unit_exists": False,
            "unit_state": "unknown",
            "unit_logs": "",
            "pid": None,
        }
        if not self.is_installed():
            return status
        status["installed"] = True
        if not self.php_binary():
            status["state"] = "error"
            status["error"] = "No panel-managed PHP CLI is installed."
            return status
        unit_exists, unit_state = self._unit_state()
        running = self._port_open(self.host, self.port)
        mariadb = self.mariadb_reachable()
        healthy = running and mariadb
        if unit_exists and not running:
            status["unit_logs"] = self.unit_logs()
        status.update(
            {
                "running": running,
                "healthy": healthy,
                "state": "healthy" if healthy else ("running" if running else "stopped"),
                "unit_exists": unit_exists,
                "unit_state": unit_state,
                "error": (
                    None
                    if healthy
                    else (
                        "phpMyAdmin server is not running (systemd "
                        f"{unit_state}, unit {'present' if unit_exists else 'missing'})."
                        if not running
                        else "MariaDB is not reachable on 127.0.0.1:3306."
                    )
                ),
            }
        )
        return status

    def get_usage(self) -> dict[str, Any]:
        """Report the phpMyAdmin server process to the Usage page."""
        status = self.get_status()
        row = {
            "cpu": 0.0,
            "mem": 0.0,
            "memory": "0 MB",
            "count": 0,
            "status": status["state"],
        }
        if not status["installed"]:
            return row
        try:
            result = self._run(
                ["pgrep", "-f", f"php .*-S {self.host}:{self.port}"],
                timeout=5,
            )
            row["count"] = len(result.stdout.split()) if result.returncode == 0 else 0
            row["status"] = "running" if status["healthy"] else "unhealthy"
        except (OSError, subprocess.TimeoutExpired):
            row["status"] = "unhealthy"
        return row

    def pause(self) -> None:
        """Stop the owned phpMyAdmin server unit."""
        if not self.is_installed():
            return
        result = self._run(["systemctl", "stop", self.unit_name], timeout=30)
        if result.returncode != 0:
            raise RuntimeError(
                result.stderr.strip() or f"Could not stop {self.unit_name}."
            )

    def resume(self) -> None:
        """Start or restart the owned phpMyAdmin server unit."""
        if not self.is_installed():
            raise RuntimeError("phpMyAdmin is not installed.")
        result = self._run(["systemctl", "restart", self.unit_name], timeout=30)
        if result.returncode != 0:
            raise RuntimeError(
                result.stderr.strip() or f"Could not start {self.unit_name}."
            )

    # ---------------------------------------------------------------
    # State
    # ---------------------------------------------------------------
    def read_state(self) -> dict[str, Any]:
        with self._state_lock:
            try:
                data = json.loads(self.state_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return {"schema_version": 2}
            return data if isinstance(data, dict) else {"schema_version": 2}

    def write_state(self, state: dict[str, Any]) -> None:
        with self._state_lock:
            normalized = dict(state)
            normalized["schema_version"] = 2
            self.data_dir.mkdir(parents=True, exist_ok=True)
            temp = self.state_path.with_suffix(".tmp")
            temp.write_text(json.dumps(normalized, indent=2), encoding="utf-8")
            temp.replace(self.state_path)

    def update_state(self, **changes: Any) -> dict[str, Any]:
        with self._state_lock:
            state = self.read_state()
            state.update(changes)
            self.write_state(state)
            return state

    def purge_data(self) -> None:
        """Remove panel-side state and secrets after uninstall."""
        if self.is_installed():
            raise RuntimeError("Uninstall phpMyAdmin before purging its data.")
        for path in (self.state_path, self.secret_path, self.marker_path):
            try:
                path.unlink()
            except FileNotFoundError:
                pass


phpmyadmin_service = PhpMyAdminService()
