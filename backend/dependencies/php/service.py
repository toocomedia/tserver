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
# Always show the common PHP lines so an administrator can immediately see
# whether their configured APT sources offer the version an older script needs.
KNOWN_VERSION_SERIES = ("7.4", "8.0", "8.1", "8.2", "8.3", "8.4", "8.5")
EXTERNAL_REPOSITORY_NAME = "Ondřej Surý PHP PPA"
EXTERNAL_REPOSITORY_PPA = "ppa:ondrej/php"
EXTERNAL_REPOSITORY_MARKERS = (
    "ppa.launchpadcontent.net/ondrej/php",
    "ppa.launchpad.net/ondrej/php",
)


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
        self._operation_lock = threading.Lock()

    def _operation_busy(self) -> tuple[bool, str]:
        return False, "Another PHP runtime operation is already running."

    def _with_operation_state(self, status: dict[str, Any]) -> dict[str, Any]:
        status["operation_in_progress"] = self._operation_lock.locked()
        return status

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

    @staticmethod
    def _external_repository_configured() -> bool:
        """Detect the fixed external PHP repository without changing APT."""
        if os.name == "nt":
            return False
        source_files = [Path("/etc/apt/sources.list")]
        source_directory = Path("/etc/apt/sources.list.d")
        if source_directory.is_dir():
            source_files.extend(source_directory.glob("*.list"))
            source_files.extend(source_directory.glob("*.sources"))
        for source_file in source_files:
            try:
                contents = source_file.read_text(encoding="utf-8", errors="ignore").lower()
            except OSError:
                continue
            if any(marker in contents for marker in EXTERNAL_REPOSITORY_MARKERS):
                return True
        return False

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
        ordered_versions = sorted(versions, key=lambda value: tuple(int(part) for part in value.split(".")))
        if not ordered_versions:
            return []
        try:
            policy = self._run(
                ["apt-cache", "policy", *(self._package_name(version) for version in ordered_versions)],
                timeout=max(20, len(ordered_versions) * 2),
            )
        except (OSError, subprocess.TimeoutExpired):
            return []
        if policy.returncode != 0:
            return []
        available: list[str] = []
        current_version = None
        for line in policy.stdout.splitlines():
            package_match = re.fullmatch(r"php(\d+\.\d+)-fpm:", line.strip())
            if package_match:
                current_version = package_match.group(1)
                continue
            if current_version and line.strip().startswith("Candidate:"):
                candidate = line.split(":", 1)[1].strip()
                if candidate and candidate != "(none)":
                    available.append(current_version)
                current_version = None
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

    def _version_status(self, version: str, managed: bool, available_from_apt: bool) -> dict[str, Any]:
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
            "state": "healthy" if installed and running and socket_healthy else (
                "stopped" if installed else ("available" if available_from_apt else "unavailable")
            ),
            "error": error, "available_from_apt": available_from_apt,
            "can_install": not installed and available_from_apt,
            "can_uninstall": installed and managed,
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
            set(KNOWN_VERSION_SERIES) | set(available) | configured,
            key=lambda value: tuple(int(part) for part in value.split(".")),
        )
        managed = self._managed_versions()
        runtime_versions = [
            self._version_status(version, version in managed, version in available)
            for version in versions
        ]
        installed = [item for item in runtime_versions if item["installed"]]
        managed_installed = [item for item in installed if item["managed"]]
        external_installed = [item for item in installed if not item["managed"]]
        running = any(item.get("running") for item in managed_installed)
        healthy = any(item["healthy"] for item in managed_installed)
        install_origin = (
            "not_installed" if not installed else
            ("mixed" if managed_installed and external_installed else
             ("panel_managed" if managed_installed else "external"))
        )
        external_repository_configured = self._external_repository_configured()
        return {
            "id": self.dependency_id, "installed": bool(installed), "running": running,
            "healthy": healthy,
            "state": "not_installed" if not installed else ("healthy" if healthy else "stopped"),
            "detected_version": ", ".join(item["version"] for item in installed) or None,
            "install_origin": install_origin,
            "error": None if healthy or not installed else "No panel-managed PHP-FPM version has a healthy socket.",
            "can_toggle": bool(managed_installed),
            "versions": runtime_versions, "available_versions": available,
            "external_repository": {
                "configured": external_repository_configured,
                "name": EXTERNAL_REPOSITORY_NAME,
                "ppa": EXTERNAL_REPOSITORY_PPA,
                "official_ubuntu": False,
            },
        }

    def get_status(self, *, force: bool = False) -> dict[str, Any]:
        now = time.monotonic()
        with self._cache_lock:
            if not force and self._cache and now - self._cache_at < self.CACHE_SECONDS:
                return self._with_operation_state(dict(self._cache))
            self._cache = self._probe()
            self._cache_at = now
            return self._with_operation_state(dict(self._cache))

    def get_cached_status(self) -> dict[str, Any]:
        """Return only an existing snapshot; never launch APT or system helpers."""
        with self._cache_lock:
            if self._cache is not None:
                return self._with_operation_state(dict(self._cache))
        return self._with_operation_state({
            "id": self.dependency_id,
            "installed": False,
            "running": False,
            "healthy": False,
            "state": "unknown",
            "detected_version": None,
            "install_origin": "unknown",
            "error": None,
            "can_toggle": False,
            "versions": [],
            "available_versions": [],
            "external_repository": {
                "configured": False,
                "name": EXTERNAL_REPOSITORY_NAME,
                "ppa": EXTERNAL_REPOSITORY_PPA,
                "official_ubuntu": False,
            },
        })

    def _invalidate(self) -> None:
        with self._cache_lock:
            self._cache = None
            self._cache_at = 0.0

    def _helper_call(self, operation: str, *, timeout: int = 900, **values: Any) -> dict[str, Any]:
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
        if not self._operation_lock.acquire(blocking=False):
            return self._operation_busy()
        try:
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
        finally:
            self._operation_lock.release()

    def check_available_versions(self) -> tuple[bool, str]:
        """Refresh APT only after the administrator explicitly requests it."""
        if not self._operation_lock.acquire(blocking=False):
            return self._operation_busy()
        try:
            try:
                payload = self._helper_call("check_available", timeout=300)
            except RuntimeError as exc:
                return False, str(exc)
            self._invalidate()
            return True, str(payload.get("message") or "PHP package availability refreshed.")
        finally:
            self._operation_lock.release()

    def enable_external_repository(self) -> tuple[bool, str]:
        """Enable only the reviewed PHP PPA, never a user-provided repository."""
        if not self._operation_lock.acquire(blocking=False):
            return self._operation_busy()
        try:
            try:
                payload = self._helper_call("enable_external_repository", timeout=600)
            except RuntimeError as exc:
                return False, str(exc)
            self._invalidate()
            return True, str(payload.get("message") or "External PHP repository enabled and package availability refreshed.")
        finally:
            self._operation_lock.release()

    def uninstall_version(self, version: str) -> tuple[bool, str]:
        normalized = self._valid_version(version)
        if normalized is None:
            return False, "Invalid PHP version."
        if not self._operation_lock.acquire(blocking=False):
            return self._operation_busy()
        try:
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
        finally:
            self._operation_lock.release()

    def toggle(self, enabled: bool) -> tuple[bool, str]:
        """Start or stop every panel-managed PHP-FPM version as one operation."""
        if not self._operation_lock.acquire(blocking=False):
            return self._operation_busy()
        try:
            try:
                payload = self._helper_call("set_all_enabled", enabled=bool(enabled), timeout=180)
            except RuntimeError as exc:
                return False, str(exc)
            self._invalidate()
            status = self.get_status(force=True)
            if enabled and not status["healthy"]:
                return False, "PHP-FPM services started but no managed PHP socket is healthy."
            if not enabled and status["running"]:
                return False, "One or more managed PHP-FPM services are still running."
            fallback = "All managed PHP-FPM services enabled." if enabled else "All managed PHP-FPM services disabled."
            return True, str(payload.get("message") or fallback)
        finally:
            self._operation_lock.release()

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
