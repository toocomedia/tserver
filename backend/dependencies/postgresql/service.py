"""PostgreSQL service detection and lifecycle control."""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import config


class PostgreSQLDependencyService:
    dependency_id = "postgresql"
    CACHE_SECONDS = 30.0

    def __init__(self) -> None:
        self._cache: dict[str, Any] | None = None
        self._cache_at = 0.0
        self._cache_lock = threading.Lock()

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

    def _probe(self) -> dict[str, Any]:
        installed = shutil.which("psql") is not None or Path("/usr/bin/psql").is_file()
        running = False
        version = None
        error = None
        if installed and os.name != "nt":
            try:
                running = self._run(["systemctl", "is-active", "postgresql"], timeout=5).stdout.strip() == "active"
                if running:
                    version = self._run(["psql", "--version"], timeout=5).stdout.strip() or None
            except subprocess.TimeoutExpired:
                error = "PostgreSQL status check timed out."
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
            "error": error or (None if healthy else "PostgreSQL is not running on port 5432."),
            "can_toggle": True,
        }

    @staticmethod
    def _port_open() -> bool:
        try:
            with socket.create_connection(("127.0.0.1", 5432), timeout=0.5):
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
        with self._cache_lock:
            return dict(self._cache) if self._cache else self._probe()

    def _invalidate(self) -> None:
        with self._cache_lock:
            self._cache = None
            self._cache_at = 0.0

    def toggle(self, enabled: bool) -> tuple[bool, str]:
        if os.name == "nt":
            return False, "PostgreSQL service control is only available on Linux."
        command = ["systemctl", "enable", "--now", "postgresql"] if enabled else ["systemctl", "disable", "--now", "postgresql"]
        try:
            result = self._run(command)
        except subprocess.TimeoutExpired:
            return False, "PostgreSQL service control timed out."
        if result.returncode != 0:
            return False, result.stderr.strip() or result.stdout.strip() or "PostgreSQL service control failed."
        self._invalidate()
        status = self.get_status(force=True)
        if enabled and not status["healthy"]:
            return False, status["error"] or "PostgreSQL did not become healthy."
        if not enabled and status["running"]:
            return False, "PostgreSQL is still running after the stop request."
        return True, "PostgreSQL enabled." if enabled else "PostgreSQL disabled."

    @staticmethod
    def _installer_path() -> Path:
        return Path(__file__).resolve().parents[2] / "plugins" / "postgres_manager" / "scripts" / "install.sh"

    def install(self) -> tuple[bool, str]:
        if os.name == "nt":
            return False, "PostgreSQL installation is only available on Linux."
        installer = self._installer_path()
        if not installer.is_file():
            return False, "PostgreSQL installer script is missing."
        try:
            result = self._run(["bash", str(installer)], timeout=180)
        except subprocess.TimeoutExpired:
            return False, "PostgreSQL installation timed out."
        if result.returncode != 0:
            return False, (result.stderr or result.stdout or "PostgreSQL installation failed.").strip()[-2000:]
        self._invalidate()
        if not self.get_status(force=True)["healthy"]:
            return False, "PostgreSQL installed but is not healthy."
        return True, "PostgreSQL installed successfully."

    def get_install_guide(self) -> dict[str, Any]:
        return {"supported": os.name != "nt", "command": "sudo apt-get install -y postgresql postgresql-client", "warning": "The panel installer also configures PostgreSQL Manager permissions."}

    def get_uninstall_guide(self) -> dict[str, Any]:
        return {"command": "sudo systemctl disable --now postgresql", "warning": "Database data is preserved; SRV Panel never removes /var/lib/postgresql."}

    @staticmethod
    def list_containers() -> list[dict[str, Any]]:
        return []
