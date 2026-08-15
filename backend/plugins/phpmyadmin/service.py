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
    config_version = "2"
    unit_name = "srv-panel-phpmyadmin"
    host = "127.0.0.1"
    port = int(os.getenv("PHPMYADMIN_PORT", "8090"))
    command_timeout = 15

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
        """Highest panel-managed PHP version, else highest installed directory."""
        try:
            data = json.loads(PHP_STATE_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        versions = [
            str(version)
            for version in data
            if isinstance(version, str) and re.fullmatch(r"\d+\.\d+", version)
        ]
        if not versions:
            fpm_dir = Path("/etc/php")
            if fpm_dir.is_dir():
                versions = sorted(
                    (entry.name for entry in fpm_dir.iterdir() if entry.is_dir()),
                    key=_VERSION_KEY,
                )
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
            with socket.create_connection((host, port), timeout=1.0):
                return True
        except OSError:
            return False

    @staticmethod
    def mariadb_reachable() -> bool:
        if os.name == "nt":
            return False
        return PhpMyAdminService._port_open("127.0.0.1", 3306)

    def get_status(self) -> dict[str, Any]:
        status = {
            "installed": False,
            "running": False,
            "healthy": False,
            "state": "missing",
            "error": None,
            "php_version": self.php_version(),
            "port": self.port,
            "pid": None,
        }
        if not self.is_installed():
            return status
        status["installed"] = True
        if not self.php_binary():
            status["state"] = "error"
            status["error"] = "No panel-managed PHP CLI is installed."
            return status
        running = self._port_open(self.host, self.port)
        mariadb = self.mariadb_reachable()
        healthy = running and mariadb
        status.update(
            {
                "running": running,
                "healthy": healthy,
                "state": "healthy" if healthy else ("running" if running else "stopped"),
                "error": (
                    None
                    if healthy
                    else (
                        "phpMyAdmin server is not running."
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
        """Start the owned phpMyAdmin server unit."""
        if not self.is_installed():
            raise RuntimeError("phpMyAdmin is not installed.")
        result = self._run(["systemctl", "start", self.unit_name], timeout=30)
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
