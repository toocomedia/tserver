"""Cached PostgreSQL health and lifecycle driver."""
from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from typing import Any

import config


class PostgreSQLDependencyService:
    dependency_id = "postgresql"
    CACHE_SECONDS = 5.0

    def __init__(self) -> None:
        self._cache: dict[str, Any] | None = None
        self._cache_at = 0.0
        self._lock = threading.Lock()

    @staticmethod
    def _prefix() -> list[str]:
        if os.name == "nt" or not hasattr(os, "geteuid") or os.geteuid() == 0:
            return []
        return ["sudo", "-n"] if config.PRIVILEGED_SUDO else []

    def _run(self, command: list[str], *, privileged: bool = False):
        prefix = self._prefix() if privileged else []
        return subprocess.run(
            [*prefix, *command], capture_output=True, text=True, timeout=5,
            check=False, shell=False,
        )

    def _probe(self) -> dict[str, Any]:
        installed = shutil.which("pg_isready") is not None
        version = None
        error = None
        running = False
        if installed:
            try:
                version_result = self._run(["psql", "--version"])
                version = (version_result.stdout or version_result.stderr).strip() or None
                ready = self._run(["pg_isready", "-q"], privileged=True)
                running = ready.returncode == 0
                if not running:
                    error = "PostgreSQL is not accepting connections."
            except subprocess.TimeoutExpired:
                error = "PostgreSQL status check timed out."
            except OSError as exc:
                error = str(exc)
        return {
            "id": self.dependency_id,
            "installed": installed,
            "running": running,
            "healthy": installed and running,
            "state": "healthy" if installed and running else ("stopped" if installed else "not_installed"),
            "detected_version": version,
            "error": error,
        }

    def get_status(self, *, force: bool = False) -> dict[str, Any]:
        now = time.monotonic()
        with self._lock:
            if not force and self._cache is not None and now - self._cache_at < self.CACHE_SECONDS:
                return dict(self._cache)
            self._cache = self._probe()
            self._cache_at = now
            return dict(self._cache)

    def get_cached_status(self) -> dict[str, Any]:
        with self._lock:
            if self._cache is not None:
                return dict(self._cache)
        installed = shutil.which("pg_isready") is not None
        return {
            "id": self.dependency_id,
            "installed": installed,
            "running": False,
            "healthy": False,
            "state": "unknown" if installed else "not_installed",
            "detected_version": None,
            "error": None,
        }

    def toggle(self, enabled: bool) -> tuple[bool, str]:
        if os.name == "nt":
            return False, "PostgreSQL service control is only available on Linux."
        command = ["systemctl", "enable" if enabled else "disable", "--now", "postgresql.service"]
        try:
            result = self._run(command, privileged=True)
        except subprocess.TimeoutExpired:
            return False, "PostgreSQL service action timed out."
        with self._lock:
            self._cache = None
            self._cache_at = 0.0
        status = self.get_status(force=True)
        if enabled and not status["healthy"]:
            return False, status["error"] or "PostgreSQL did not become healthy."
        if not enabled and status["running"]:
            return False, "PostgreSQL is still running after the stop request."
        if result.returncode != 0:
            return False, result.stderr.strip() or result.stdout.strip() or "PostgreSQL service action failed."
        return True, "PostgreSQL enabled." if enabled else "PostgreSQL disabled."

    def get_install_guide(self) -> dict[str, Any]:
        return {"supported": False, "command": "sudo bash /opt/srv-panel/scripts/update.sh", "warning": "Install PostgreSQL through the panel installer."}

    def get_uninstall_guide(self) -> dict[str, Any]:
        return {"command": "Not available", "warning": "Use PostgreSQL Manager before removing PostgreSQL manually."}
