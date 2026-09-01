"""Supported OS/architecture capabilities shared by backend lifecycle guards."""
from __future__ import annotations

import os
import platform
import threading
import time
from pathlib import Path
from typing import Any, Iterable

SUPPORTED_CAPABILITIES: frozenset[str] = frozenset({
    "core",
    "docker",
    "php",
    "mariadb",
    "postgresql",
    "railpack_apps",
    "native_python",
    "php_external_repository",
})

SUPPORTED_ARCHITECTURES: frozenset[str] = frozenset({"amd64", "arm64"})


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
        os_id = release.get("ID", "linux" if os.name != "nt" else "unknown").lower()
        version_id = release.get("VERSION_ID", "")
        pretty_name = release.get("PRETTY_NAME") or (f"{os_id} {version_id}".strip() or "Linux")
        
        raw_arch = os.environ.get("SRV_OS_ARCH") or platform.machine() or "unknown"
        raw_arch_lower = raw_arch.lower()
        if raw_arch_lower in {"x86_64", "amd64"}:
            arch = "amd64"
        elif raw_arch_lower in {"aarch64", "arm64"}:
            arch = "arm64"
        else:
            arch = raw_arch_lower

        selector = f"{os_id}:{version_id}" if version_id else os_id

        # Verify host platform architecture (64-bit required)
        error: str | None = None
        if arch not in SUPPORTED_ARCHITECTURES:
            error = f"Unsupported CPU architecture {arch}. SRV Panel requires 64-bit architecture (amd64 or arm64)."

        capabilities = sorted(SUPPORTED_CAPABILITIES) if error is None else []

        return {
            "id": os_id,
            "version_id": version_id,
            "codename": release.get("UBUNTU_CODENAME") or release.get("VERSION_CODENAME") or "",
            "pretty_name": pretty_name,
            "arch": arch,
            "selector": selector,
            "supported": error is None,
            "capabilities": capabilities,
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

    def plugin_support(self, selectors: Iterable[str] = ()) -> tuple[bool, str | None]:
        info = self.get()
        if not info["supported"]:
            return False, str(info["error"])
        return True, None

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
