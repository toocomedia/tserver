"""Service for managing panel-level PHP CLI tools (Composer, WP-CLI)."""
from __future__ import annotations

import json
import os
import subprocess
import threading
from pathlib import Path
from typing import Any

import config


class PhpToolsService:
    """Manages CLI tools required by panel PHP features."""

    HELPER_PATH = Path(__file__).resolve().parents[3] / "scripts" / "php_tools_helper.py"

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
            if operation == "list_tools":
                return {
                    "tools": [
                        {
                            "id": "composer",
                            "name": "Composer",
                            "description": "Dependency manager for modern PHP applications, Laravel, and Filament",
                            "category": "Package Management",
                            "installed": False,
                            "path": "/usr/local/bin/composer",
                            "version": None,
                        },
                        {
                            "id": "wp",
                            "name": "WP-CLI",
                            "description": "Command-line interface for WordPress management and automation",
                            "category": "WordPress CLI",
                            "installed": False,
                            "path": "/usr/local/bin/wp",
                            "version": None,
                        },
                    ]
                }
            return {"message": f"{operation} simulated on Windows.", "tool": {"installed": True}}

        if not self.HELPER_PATH.is_file():
            raise RuntimeError("PHP tools helper script is missing.")

        request = json.dumps({"operation": operation, **kwargs})
        try:
            res = subprocess.run(
                [*self._command_prefix(), "python3", str(self.HELPER_PATH)],
                input=request,
                capture_output=True,
                text=True,
                timeout=320,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("PHP tool operation timed out.") from exc

        if res.returncode != 0:
            raise RuntimeError((res.stderr or res.stdout or "Tool operation failed.").strip()[-2000:])

        try:
            payload = json.loads(res.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Invalid response from PHP tools helper.") from exc

        if not payload.get("ok") or not isinstance(payload.get("result"), dict):
            raise RuntimeError("PHP tool operation failed.")

        return dict(payload["result"])

    def get_tools_status(self) -> list[dict[str, Any]]:
        with self._lock:
            payload = self._call("list_tools")
            return list(payload.get("tools", []))

    def install_tool(self, tool_id: str) -> tuple[bool, str, dict[str, Any]]:
        with self._lock:
            try:
                payload = self._call("install_tool", tool=tool_id)
                return True, str(payload.get("message") or f"{tool_id} installed successfully."), payload.get("tool", {})
            except Exception as exc:
                return False, str(exc), {}

    def uninstall_tool(self, tool_id: str) -> tuple[bool, str, dict[str, Any]]:
        with self._lock:
            try:
                payload = self._call("uninstall_tool", tool=tool_id)
                return True, str(payload.get("message") or f"{tool_id} uninstalled successfully."), payload.get("tool", {})
            except Exception as exc:
                return False, str(exc), {}


php_tools_service = PhpToolsService()
