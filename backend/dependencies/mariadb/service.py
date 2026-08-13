"""Native MariaDB service detection, lifecycle control, and updates."""
from __future__ import annotations

from datetime import datetime, timezone
import os
import re
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import config


class MariaDBDependencyService:
    dependency_id = "mariadb"
    CACHE_SECONDS = 30.0
    update_confirmation = "UPDATE MARIADB"
    update_policy = "panel_managed"
    install_resource_profile = "database_mariadb"
    update_resource_profile = "native_light"

    def __init__(self) -> None:
        self._cache: dict[str, Any] | None = None
        self._cache_at = 0.0
        self._update_cache: dict[str, Any] = self._empty_update_status()
        self._cache_lock = threading.Lock()

    @staticmethod
    def _empty_update_status() -> dict[str, Any]:
        return {
            "state": "not_checked",
            "available": False,
            "candidate_version": None,
            "source": "Configured APT repositories",
            "message": "Check for updates to read this server's configured APT sources.",
            "last_checked": None,
            "major_change": False,
        }

    @staticmethod
    def _command_prefix() -> list[str]:
        if os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0):
            return []
        return ["sudo", "-n"] if config.PRIVILEGED_SUDO else []

    def _run(self, command: list[str], *, timeout: int = 30) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [*self._command_prefix(), *command],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            shell=False,
        )

    @staticmethod
    def _script_path(filename: str) -> Path:
        deployed = Path("/opt/srv-panel/scripts") / filename
        if deployed.is_file():
            return deployed
        return Path(__file__).resolve().parents[3] / "scripts" / filename

    def _probe(self) -> dict[str, Any]:
        client = shutil.which("mariadb") or shutil.which("mysql")
        installed = bool(client or Path("/usr/bin/mariadb").is_file())
        running = False
        version = None
        error = None
        if installed and os.name != "nt":
            try:
                running = self._run(["systemctl", "is-active", "mariadb"], timeout=5).stdout.strip() == "active"
                if running:
                    version_result = self._run([client or "mariadb", "--version"], timeout=5)
                    version = version_result.stdout.strip() or version_result.stderr.strip() or None
            except subprocess.TimeoutExpired:
                error = "MariaDB status check timed out."
            except OSError as exc:
                error = str(exc)
        port_open = self._port_open() if running else False
        healthy = installed and running and port_open
        return {
            "id": self.dependency_id,
            "installed": installed,
            "running": running,
            "healthy": healthy,
            "state": "not_installed" if not installed else ("healthy" if healthy else "stopped"),
            "detected_version": version,
            "error": error or (None if healthy else "MariaDB is not running on localhost port 3306."),
            "can_toggle": True,
        }

    @staticmethod
    def _port_open() -> bool:
        try:
            with socket.create_connection(("127.0.0.1", 3306), timeout=0.5):
                return True
        except OSError:
            return False

    def get_status(self, *, force: bool = False) -> dict[str, Any]:
        now = time.monotonic()
        with self._cache_lock:
            if not force and self._cache and now - self._cache_at < self.CACHE_SECONDS:
                return dict(self._cache)
            self._cache = self._probe()
            self._cache_at = now
            return dict(self._cache)

    def get_cached_status(self) -> dict[str, Any]:
        """Return only an existing snapshot; never launch systemctl."""
        with self._cache_lock:
            if self._cache is not None:
                return dict(self._cache)
        installed = bool(shutil.which("mariadb") or shutil.which("mysql") or Path("/usr/bin/mariadb").is_file())
        return {
            "id": self.dependency_id,
            "installed": installed,
            "running": False,
            "healthy": False,
            "state": "unknown" if installed else "not_installed",
            "detected_version": None,
            "error": None,
            "can_toggle": True,
        }

    def get_cached_update_status(self) -> dict[str, Any]:
        with self._cache_lock:
            return dict(self._update_cache)

    def _invalidate(self) -> None:
        with self._cache_lock:
            self._cache = None
            self._cache_at = 0.0

    def toggle(self, enabled: bool) -> tuple[bool, str]:
        if os.name == "nt":
            return False, "MariaDB service control is only available on Linux."
        command = ["systemctl", "enable", "--now", "mariadb"] if enabled else ["systemctl", "disable", "--now", "mariadb"]
        try:
            result = self._run(command)
        except subprocess.TimeoutExpired:
            return False, "MariaDB service control timed out."
        if result.returncode != 0:
            return False, result.stderr.strip() or result.stdout.strip() or "MariaDB service control failed."
        self._invalidate()
        status = self.get_status(force=True)
        if enabled and not status["healthy"]:
            return False, status["error"] or "MariaDB did not become healthy."
        if not enabled and status["running"]:
            return False, "MariaDB is still running after the stop request."
        return True, "MariaDB enabled." if enabled else "MariaDB disabled."

    def install(self) -> tuple[bool, str]:
        if os.name == "nt":
            return False, "MariaDB installation is only available on Linux."
        installer = self._script_path("install_mariadb.sh")
        if not installer.is_file():
            return False, "MariaDB installer script is missing. Run the panel updater first."
        try:
            result = self._run(["bash", str(installer)], timeout=600)
        except subprocess.TimeoutExpired:
            return False, "MariaDB installation timed out after 10 minutes."
        if result.returncode != 0:
            return False, (result.stderr or result.stdout or "MariaDB installation failed.").strip()[-2000:]
        self._invalidate()
        if not self.get_status(force=True)["healthy"]:
            return False, "MariaDB installed but is not healthy."
        return True, result.stdout.strip()[-2000:] or "MariaDB installed successfully."

    @staticmethod
    def _parse_update_output(output: str) -> dict[str, str]:
        values: dict[str, str] = {}
        for line in output.splitlines():
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key in {"installed", "candidate", "available", "major_change", "source"}:
                values[key] = value.strip()
        return values

    def check_update(self) -> tuple[bool, str]:
        if os.name == "nt":
            return False, "MariaDB update checks are only available on Linux."
        checker = self._script_path("check_mariadb_update.sh")
        if not checker.is_file():
            return False, "MariaDB update-check script is missing. Run the panel updater first."
        try:
            result = self._run(["bash", str(checker)], timeout=180)
        except subprocess.TimeoutExpired:
            return False, "MariaDB update check timed out."
        if result.returncode != 0:
            message = (result.stderr or result.stdout or "MariaDB update check failed.").strip()[-2000:]
            with self._cache_lock:
                self._update_cache = {
                    **self._empty_update_status(),
                    "state": "error",
                    "message": message,
                    "last_checked": datetime.now(timezone.utc).isoformat(),
                }
            return False, message
        values = self._parse_update_output(result.stdout)
        available = values.get("available") == "true"
        major_change = values.get("major_change") == "true"
        state = "major_available" if available and major_change else ("available" if available else "up_to_date")
        candidate = values.get("candidate") or None
        with self._cache_lock:
            self._update_cache = {
                "state": state,
                "available": available and not major_change,
                "candidate_version": candidate,
                "source": values.get("source") or "Configured APT repositories",
                "message": (
                    "A MariaDB major upgrade needs a dedicated migration workflow."
                    if major_change else ("MariaDB update is available." if available else "MariaDB is up to date.")
                ),
                "last_checked": datetime.now(timezone.utc).isoformat(),
                "major_change": major_change,
            }
        return True, self._update_cache["message"]

    def update(self) -> tuple[bool, str]:
        updater = self._script_path("update_mariadb.sh")
        if not updater.is_file():
            return False, "MariaDB update script is missing. Run the panel updater first."
        try:
            result = self._run(["bash", str(updater)], timeout=900)
        except subprocess.TimeoutExpired:
            return False, "MariaDB update timed out after 15 minutes."
        if result.returncode != 0:
            return False, (result.stderr or result.stdout or "MariaDB update failed.").strip()[-2000:]
        self._invalidate()
        if not self.get_status(force=True)["healthy"]:
            return False, "MariaDB packages updated but the service is not healthy."
        self.check_update()
        return True, result.stdout.strip()[-2000:] or "MariaDB updated successfully."

    def get_install_guide(self) -> dict[str, Any]:
        return {
            "supported": os.name != "nt",
            "command": "sudo bash /opt/srv-panel/scripts/install_mariadb.sh",
            "warning": "Uses this server's configured APT repositories and binds MariaDB to localhost only.",
        }

    def get_uninstall_guide(self) -> dict[str, Any]:
        return {
            "command": "sudo systemctl disable --now mariadb",
            "data_path": "/var/lib/mysql",
            "warning": "MariaDB data is preserved. Full data removal is a separate destructive operation.",
        }

    @staticmethod
    def list_containers() -> list[dict[str, Any]]:
        return []
