"""Native PHP-FPM version discovery and panel-managed lifecycle operations."""
from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import config


VERSION_RE = re.compile(r"^\d+\.\d+$")
PACKAGE_RE = re.compile(r"^php(\d+\.\d+)-fpm$")


class PHPDependencyService:
    """Manage only PHP versions explicitly installed through SRV Panel."""

    dependency_id = "php"
    CACHE_SECONDS = 30.0
    HELPER_PATH = Path("/usr/local/lib/srv-panel/php-runtime-manager")
    install_resource_profile = "native_light"

    def __init__(self) -> None:
        self._cache: dict[str, Any] | None = None
        self._cache_at = 0.0
        self._cache_lock = threading.Lock()

    @staticmethod
    def _command_prefix() -> list[str]:
        if os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0):
            return []
        return ["sudo", "-n"] if config.PRIVILEGED_SUDO else []

    def _run(self, command: list[str], *, timeout: int = 20) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            command, capture_output=True, text=True,
            timeout=timeout, check=False, shell=False,
        )

    @classmethod
    def _valid_version(cls, version: str) -> str | None:
        normalized = str(version or "").strip()
        return normalized if VERSION_RE.fullmatch(normalized) else None

    @staticmethod
    def _package_name(version: str) -> str:
        return f"php{version}-fpm"

    def _available_versions(self) -> list[str]:
        """Return FPM packages that are available from the current APT indexes."""
        if os.name == "nt":
            return []
        try:
            result = self._run(["apt-cache", "pkgnames"], timeout=20)
        except (OSError, subprocess.TimeoutExpired):
            return []
        if result.returncode != 0:
            return []
        versions = {
            match.group(1) for line in result.stdout.splitlines()
            if (match := PACKAGE_RE.fullmatch(line.strip()))
        }
        available: list[str] = []
        for version in sorted(versions, key=lambda value: tuple(int(part) for part in value.split("."))):
            try:
                policy = self._run(["apt-cache", "policy", self._package_name(version)], timeout=8)
            except (OSError, subprocess.TimeoutExpired):
                continue
            candidate = next((
                line.split(":", 1)[1].strip() for line in policy.stdout.splitlines()
                if line.strip().startswith("Candidate:")
            ), "")
            if policy.returncode == 0 and candidate and candidate != "(none)":
                available.append(version)
        return available

    def _installed_package_version(self, version: str) -> str | None:
        if os.name == "nt":
            return None
        try:
            result = self._run(
                ["dpkg-query", "-W", "-f=${db:Status-Abbrev}\t${Version}", self._package_name(version)],
                timeout=6,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if result.returncode != 0 or not result.stdout.startswith("ii"):
            return None
        _, _, package_version = result.stdout.strip().partition("\t")
        return package_version or None

    def _managed_versions(self) -> set[str]:
        if os.name == "nt" or not self.HELPER_PATH.is_file():
            return set()
        try:
            payload = self._helper_call("list_managed", timeout=10)
        except RuntimeError:
            return set()
        values = payload.get("versions") or []
        return {value for value in values if isinstance(value, str) and self._valid_version(value)}

    def _version_status(self, version: str, managed: bool) -> dict[str, Any]:
        package_version = self._installed_package_version(version)
        installed = package_version is not None
        running = False
        socket_path = f"/run/php/php{version}-fpm.sock"
        socket_healthy = False
        error = None
        if installed and os.name != "nt":
            try:
                status_result = self._run(["systemctl", "is-active", f"php{version}-fpm"], timeout=6)
                running = status_result.stdout.strip() == "active"
            except (OSError, subprocess.TimeoutExpired):
                error = f"PHP {version} service status check timed out."
            if running:
                try:
                    socket_healthy = stat.S_ISSOCK(Path(socket_path).stat().st_mode)
                except OSError:
                    socket_healthy = False
            if not running:
                error = error or f"PHP {version}-FPM is not running."
            elif not socket_healthy:
                error = f"PHP {version}-FPM socket is unavailable: {socket_path}."
        return {
            "version": version, "installed": installed, "managed": managed,
            "package_version": package_version, "running": running,
            "socket_path": socket_path, "socket_healthy": socket_healthy,
            "healthy": installed and running and socket_healthy,
            "state": "healthy" if installed and running and socket_healthy else ("stopped" if installed else "available"),
            "error": error, "can_install": not installed, "can_uninstall": installed and managed,
        }

    def _probe(self) -> dict[str, Any]:
        available = self._available_versions()
        configured = (
            {
                path.name for path in Path("/etc/php").iterdir()
                if path.is_dir() and self._valid_version(path.name)
            }
            if os.name != "nt" and Path("/etc/php").is_dir() else set()
        )
        versions = sorted(
            set(available) | configured,
            key=lambda value: tuple(int(part) for part in value.split(".")),
        )
        managed = self._managed_versions()
        runtime_versions = [self._version_status(version, version in managed) for version in versions]
        installed = [item for item in runtime_versions if item["installed"]]
        managed_installed = [item for item in installed if item["managed"]]
        external_installed = [item for item in installed if not item["managed"]]
        healthy = any(item["healthy"] for item in managed_installed)
        install_origin = (
            "not_installed" if not installed else
            ("mixed" if managed_installed and external_installed else
             ("panel_managed" if managed_installed else "external"))
        )
        return {
            "id": self.dependency_id, "installed": bool(installed), "running": healthy,
            "healthy": healthy,
            "state": "not_installed" if not installed else ("healthy" if healthy else "stopped"),
            "detected_version": ", ".join(item["version"] for item in installed) or None,
            "install_origin": install_origin,
            "error": None if healthy or not installed else "No panel-managed PHP-FPM version has a healthy socket.",
            "can_toggle": False, "versions": runtime_versions, "available_versions": available,
        }

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

    def _helper_call(self, operation: str, *, timeout: int = 900, **values: str) -> dict[str, Any]:
        if os.name == "nt":
            raise RuntimeError("PHP runtime management is available only on Linux.")
        if not self.HELPER_PATH.is_file():
            raise RuntimeError("PHP runtime helper is missing. Run the SRV Panel updater first.")
        request = json.dumps({"operation": operation, **values})
        try:
            result = subprocess.run(
                [*self._command_prefix(), str(self.HELPER_PATH)], input=request,
                capture_output=True, text=True, timeout=timeout, check=False, shell=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("PHP runtime operation timed out.") from exc
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout or "PHP runtime operation failed.").strip()[-2000:])
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError("PHP runtime helper returned an invalid response.") from exc
        if not payload.get("ok") or not isinstance(payload.get("result"), dict):
            raise RuntimeError("PHP runtime operation failed.")
        return dict(payload["result"])

    def install_version(self, version: str) -> tuple[bool, str]:
        normalized = self._valid_version(version)
        if normalized is None:
            return False, "Invalid PHP version."
        try:
            payload = self._helper_call("install_version", version=normalized)
        except RuntimeError as exc:
            return False, str(exc)
        self._invalidate()
        current = next(
            (item for item in self.get_status(force=True)["versions"] if item["version"] == normalized), None,
        )
        if not current or not current["managed"] or not current["healthy"]:
            return False, f"PHP {normalized} installed but PHP-FPM socket health verification failed."
        return True, str(payload.get("message") or f"PHP {normalized} installed successfully.")

    def uninstall_version(self, version: str) -> tuple[bool, str]:
        normalized = self._valid_version(version)
        if normalized is None:
            return False, "Invalid PHP version."
        current = next(
            (item for item in self.get_status(force=True)["versions"] if item["version"] == normalized), None,
        )
        if not current or not current["installed"]:
            return False, f"PHP {normalized} is not installed."
        if not current["managed"]:
            return False, f"PHP {normalized} was installed outside SRV Panel and is read-only."
        try:
            payload = self._helper_call("uninstall_version", version=normalized)
        except RuntimeError as exc:
            return False, str(exc)
        self._invalidate()
        remaining = next(
            (item for item in self.get_status(force=True)["versions"] if item["version"] == normalized), None,
        )
        if remaining and remaining["installed"]:
            return False, f"PHP {normalized} packages are still installed after the removal request."
        return True, str(payload.get("message") or f"PHP {normalized} uninstalled successfully.")

    def install(self) -> tuple[bool, str]:
        return False, "Choose a PHP version from the PHP Runtime page."

    def get_install_guide(self) -> dict[str, Any]:
        return {
            "supported": os.name != "nt",
            "command": "Choose a version and use Install in SRV Panel.",
            "warning": "PHP versions are never installed automatically.",
        }

    def get_uninstall_guide(self) -> dict[str, Any]:
        return {
            "command": "Use the Uninstall action beside a panel-managed PHP version.",
            "warning": "Uninstall removes PHP packages only. It never removes website files or databases.",
        }

    @staticmethod
    def list_containers() -> list[dict[str, Any]]:
        return []
