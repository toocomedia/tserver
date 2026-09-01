"""Service for managing panel-level PHP CLI tools (Composer, WP-CLI)."""
from __future__ import annotations

import json
import os
import re
import subprocess
import threading
from pathlib import Path
from typing import Any

import config


class PhpToolsService:
    """Manages CLI tools required by panel PHP features."""

    CANDIDATE_HELPERS = (
        Path("/usr/local/lib/srv-panel/php-tools-manager"),
        Path(__file__).resolve().parents[2] / "scripts" / "php_tools_helper.py",
        Path(__file__).resolve().parents[3] / "scripts" / "php_tools_helper.py",
        Path("/opt/srv-panel/scripts/php_tools_helper.py"),
    )

    def __init__(self) -> None:
        self._lock = threading.Lock()

    def _get_helper_path(self) -> Path | None:
        for path in self.CANDIDATE_HELPERS:
            if path.is_file():
                return path
        return None

    @staticmethod
    def _command_prefix() -> list[str]:
        if os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0):
            return []
        return ["sudo", "-n"] if config.PRIVILEGED_SUDO else []

    @staticmethod
    def _inspect_locally() -> list[dict[str, Any]]:
        tools = [
            {
                "id": "composer",
                "name": "Composer",
                "description": "Dependency manager for modern PHP applications, Laravel, and Filament",
                "category": "Package Management",
                "binary": Path("/usr/local/bin/composer"),
                "version_cmd": ["/usr/local/bin/composer", "--version"],
                "version_re": r"Composer\s+version\s+([0-9.]+)",
                "latest_version": "2.10.2",
            },
            {
                "id": "wp",
                "name": "WP-CLI",
                "description": "Command-line interface for WordPress management and automation",
                "category": "WordPress CLI",
                "binary": Path("/usr/local/bin/wp"),
                "version_cmd": ["/usr/local/bin/wp", "--allow-root", "--version"],
                "version_re": r"WP-CLI\s+([0-9.]+)",
                "latest_version": "2.12.0",
            },
        ]

        def _version_tuple(v: str | None) -> tuple[int, ...]:
            parts = re.findall(r"\d+", str(v or ""))
            return tuple(int(p) for p in parts) if parts else (0,)

        results = []
        for t in tools:
            binary: Path = t["binary"]
            installed = binary.is_file() and not binary.is_symlink() and os.access(binary, os.X_OK)
            version = None
            if installed:
                try:
                    res = subprocess.run(
                        t["version_cmd"],
                        capture_output=True,
                        text=True,
                        timeout=10,
                        check=False,
                    )
                    if res.returncode == 0:
                        out = (res.stdout or res.stderr or "").strip()
                        m = re.search(t["version_re"], out)
                        version = m.group(1) if m else (out.split("\n")[0] if out else None)
                        installed = bool(version)
                    else:
                        installed = False
                        version = None
                except Exception:
                    installed = False
                    version = None

            latest_version = t.get("latest_version")
            has_update = bool(installed and version and latest_version and _version_tuple(version) < _version_tuple(latest_version))

            results.append({
                "id": t["id"],
                "name": t["name"],
                "description": t["description"],
                "category": t["category"],
                "installed": installed,
                "path": str(binary),
                "version": version,
                "latest_version": latest_version,
                "has_update": has_update,
            })
        return results

    def _call(self, operation: str, **kwargs: Any) -> dict[str, Any]:
        if os.name == "nt":
            if operation == "list_tools":
                return {"tools": self._inspect_locally()}
            return {"message": f"{operation} simulated on Windows.", "tool": {"installed": True}}

        helper = self._get_helper_path()
        if not helper:
            if operation == "list_tools":
                return {"tools": self._inspect_locally()}
            raise RuntimeError("PHP tools helper script is missing. Run the SRV Panel updater first.")

        request = json.dumps({"operation": operation, **kwargs})
        try:
            cmd = [*self._command_prefix(), "python3", str(helper)] if helper.suffix == ".py" else [*self._command_prefix(), str(helper)]
            res = subprocess.run(
                cmd,
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
            try:
                payload = self._call("list_tools")
                return list(payload.get("tools", []))
            except Exception:
                return self._inspect_locally()

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
