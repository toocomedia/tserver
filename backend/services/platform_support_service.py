"""Supported OS/version/architecture matrix shared by backend lifecycle guards."""
from __future__ import annotations

import os
import platform
import threading
import time
from pathlib import Path
from typing import Any, Iterable


SUPPORTED_PLATFORMS: dict[str, frozenset[str]] = {
    "ubuntu:22.04": frozenset(
        {"core", "docker", "php", "mariadb", "postgresql", "railpack_apps", "php_external_repository"}
    ),
    "ubuntu:24.04": frozenset(
        {"core", "docker", "php", "mariadb", "postgresql", "railpack_apps", "native_python", "php_external_repository"}
    ),
    "ubuntu:26.04": frozenset(
        {"core", "docker", "php", "mariadb", "postgresql", "railpack_apps", "native_python", "php_external_repository"}
    ),
    "debian:12": frozenset(
        {"core", "docker", "php", "mariadb", "postgresql", "railpack_apps", "native_python"}
    ),
    "debian:13": frozenset(
        {"core", "docker", "php", "mariadb", "postgresql", "railpack_apps", "native_python"}
    ),
}


def _read_os_release(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        contents = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return values
    for line in contents.splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value.strip().strip('"').strip("'")
    return values


class PlatformSupportService:
    CACHE_SECONDS = 300.0

    def __init__(self) -> None:
        self._cache: dict[str, Any] | None = None
        self._cache_at = 0.0
        self._lock = threading.Lock()

    def _probe(self) -> dict[str, Any]:
        release_path = Path(os.environ.get("SRV_OS_RELEASE_FILE", "/etc/os-release"))
        release = _read_os_release(release_path)
        os_id = release.get("ID", "unknown").lower()
        version_id = release.get("VERSION_ID", "unknown")
        pretty_name = release.get("PRETTY_NAME") or f"{os_id} {version_id}"
        raw_arch = os.environ.get("SRV_OS_ARCH") or platform.machine() or "unknown"
        arch = "amd64" if raw_arch.lower() in {"x86_64", "amd64"} else raw_arch.lower()
        selector = f"{os_id}:{version_id}"
        capabilities = SUPPORTED_PLATFORMS.get(selector, frozenset()) if arch == "amd64" else frozenset()

        if arch != "amd64":
            error = f"Unsupported CPU architecture {arch}. SRV Panel currently supports amd64 only."
        elif selector in SUPPORTED_PLATFORMS:
            error = None
        elif os_id == "ubuntu":
            error = f"Unsupported Ubuntu version {version_id}. Supported versions: 22.04, 24.04, 26.04."
        elif os_id == "debian":
            error = f"Unsupported Debian version {version_id}. Supported versions: 12, 13."
        else:
            error = (
                f"Unsupported operating system {pretty_name}. Supported systems: "
                "Ubuntu 22.04/24.04/26.04 and Debian 12/13."
            )
        return {
            "id": os_id,
            "version_id": version_id,
            "codename": release.get("UBUNTU_CODENAME") or release.get("VERSION_CODENAME") or "unknown",
            "pretty_name": pretty_name,
            "arch": arch,
            "selector": selector,
            "supported": error is None,
            "capabilities": sorted(capabilities),
            "error": error,
        }

    def get(self, *, force: bool = False) -> dict[str, Any]:
        now = time.monotonic()
        with self._lock:
            if not force and self._cache is not None and now - self._cache_at < self.CACHE_SECONDS:
                return dict(self._cache)
            self._cache = self._probe()
            self._cache_at = now
            return dict(self._cache)

    def supports(self, capability: str) -> bool:
        info = self.get()
        return bool(info["supported"] and capability in info["capabilities"])

    def capability_error(self, capability: str) -> str | None:
        info = self.get()
        if not info["supported"]:
            return str(info["error"])
        if capability not in info["capabilities"]:
            return f"{capability.replace('_', ' ').title()} is not supported on {info['pretty_name']}."
        return None

    def plugin_support(self, selectors: Iterable[str]) -> tuple[bool, str | None]:
        allowed = tuple(selectors)
        if not allowed:
            return True, None
        info = self.get()
        if not info["supported"]:
            return False, str(info["error"])
        if info["selector"] in allowed:
            return True, None
        return False, f"Plugin is not supported on {info['pretty_name']} ({info['arch']})."

    def install_guide(self, capability: str, command: str, warning: str) -> dict[str, Any]:
        info = self.get()
        error = self.capability_error(capability)
        return {
            "supported": error is None,
            "platform": info["pretty_name"],
            "unsupported_reason": error,
            "command": command,
            "warning": warning,
        }


platform_support_service = PlatformSupportService()
