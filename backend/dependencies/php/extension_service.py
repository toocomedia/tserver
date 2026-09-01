"""Service for managing PHP extensions across installed PHP-FPM versions."""
from __future__ import annotations

import json
import os
import re
import subprocess
import threading
from pathlib import Path
from typing import Any

import config


VERSION_RE = re.compile(r"^\d+\.\d+$")


class PhpExtensionService:
    """Manages installation, uninstallation, and discovery of PHP extensions."""

    HELPER_PATH = Path("/usr/local/lib/srv-panel/php-runtime-manager")

    def __init__(self) -> None:
        self._lock = threading.Lock()

    @staticmethod
    def _command_prefix() -> list[str]:
        if os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0):
            return []
        return ["sudo", "-n"] if config.PRIVILEGED_SUDO else []

    def _call(self, operation: str, **kwargs: Any) -> dict[str, Any]:
        if os.name == "nt":
            # Mock / stub response on non-Linux development environments
            if operation == "list_extensions":
                from scripts.php_runtime_helper import EXTENSION_METADATA
                return {
                    "version": kwargs.get("version", "8.3"),
                    "extensions": [
                        {
                            "name": name,
                            "package": f"php{kwargs.get('version', '8.3')}-{name}",
                            "installed": name in {"curl", "mbstring", "mysql", "xml", "zip", "opcache"},
                            "loaded": name in {"curl", "mbstring", "mysql", "xml", "zip", "opcache"},
                            "category": meta.get("category", "General"),
                            "description": meta.get("description", ""),
                        }
                        for name, meta in EXTENSION_METADATA.items()
                    ]
                }
            return {"message": f"{operation} simulated on Windows."}

        if not self.HELPER_PATH.is_file():
            raise RuntimeError("PHP runtime helper is missing. Run the SRV Panel updater first.")

        request = json.dumps({"operation": operation, **kwargs})
        try:
            res = subprocess.run(
                [*self._command_prefix(), str(self.HELPER_PATH)],
                input=request,
                capture_output=True,
                text=True,
                timeout=900,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("PHP extension operation timed out.") from exc

        if res.returncode != 0:
            raise RuntimeError((res.stderr or res.stdout or "PHP extension operation failed.").strip()[-2000:])

        try:
            payload = json.loads(res.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Invalid response from PHP runtime helper.") from exc

        if not payload.get("ok") or not isinstance(payload.get("result"), dict):
            raise RuntimeError("PHP extension operation failed.")

        return dict(payload["result"])

    @staticmethod
    def _valid_version(version: str) -> str | None:
        normalized = str(version or "").strip()
        return normalized if VERSION_RE.fullmatch(normalized) else None

    def list_extensions(self, version: str) -> dict[str, Any]:
        normalized = self._valid_version(version)
        if not normalized:
            raise ValueError(f"Invalid PHP version: {version}")
        with self._lock:
            return self._call("list_extensions", version=normalized)

    def install_extension(self, version: str, extension: str) -> tuple[bool, str]:
        normalized = self._valid_version(version)
        if not normalized:
            return False, f"Invalid PHP version: {version}"
        clean_ext = str(extension or "").strip().lower()
        with self._lock:
            try:
                payload = self._call("install_extension", version=normalized, extension=clean_ext)
                return True, str(payload.get("message") or f"Extension {clean_ext} installed successfully.")
            except Exception as exc:
                return False, str(exc)

    def uninstall_extension(self, version: str, extension: str) -> tuple[bool, str]:
        normalized = self._valid_version(version)
        if not normalized:
            return False, f"Invalid PHP version: {version}"
        clean_ext = str(extension or "").strip().lower()
        with self._lock:
            try:
                payload = self._call("uninstall_extension", version=normalized, extension=clean_ext)
                return True, str(payload.get("message") or f"Extension {clean_ext} uninstalled successfully.")
            except Exception as exc:
                return False, str(exc)


php_extension_service = PhpExtensionService()
