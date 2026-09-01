#!/usr/bin/env python3
"""Helper script to manage panel-required PHP tools (Composer, WP-CLI)."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


TOOLS = {
    "composer": {
        "name": "Composer",
        "binary": Path("/usr/local/bin/composer"),
        "description": "Dependency manager for modern PHP applications, Laravel, and Filament",
        "category": "Package Management",
        "script_name": "install_composer.sh",
        "version_cmd": ["/usr/local/bin/composer", "--version"],
        "version_regex": r"Composer\s+version\s+([0-9.]+)",
        "latest_version": "2.10.2",
        "direct_url": "https://getcomposer.org/download/2.10.2/composer.phar",
        "sha256": "5ee7125f8a30a34d246cefdc0bc85b8a783b28f2aec968994118512350d28027",
    },
    "wp": {
        "name": "WP-CLI",
        "binary": Path("/usr/local/bin/wp"),
        "description": "Command-line interface for WordPress management and automation",
        "category": "WordPress CLI",
        "script_name": "install_wp_cli.sh",
        "version_cmd": ["/usr/local/bin/wp", "--allow-root", "--version"],
        "version_regex": r"WP-CLI\s+([0-9.]+)",
        "latest_version": "2.12.0",
        "direct_url": "https://github.com/wp-cli/wp-cli/releases/download/v2.12.0/wp-cli-2.12.0.phar",
    },
}


def version_tuple(v: str | None) -> tuple[int, ...]:
    parts = re.findall(r"\d+", str(v or ""))
    return tuple(int(p) for p in parts) if parts else (0,)


def find_script(script_name: str) -> Path | None:
    candidates = (
        Path(__file__).resolve().parent / script_name,
        Path("/usr/local/lib/srv-panel") / script_name,
        Path("/opt/srv-panel/scripts") / script_name,
        Path("/srv-panel/scripts") / script_name,
        Path(__file__).resolve().parents[1] / "scripts" / script_name,
    )
    for c in candidates:
        if c.is_file():
            return c
    return None


def fail(message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(1)


def run(command: list[str], *, timeout: int = 300) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        fail(f"{' '.join(command[:2])} timed out.")
    if result.returncode != 0:
        fail((result.stderr or result.stdout or f"{' '.join(command)} failed.").strip()[-2000:])
    return result


def inspect_tool(tool_id: str) -> dict[str, Any]:
    tool = TOOLS.get(tool_id)
    if not tool:
        fail(f"Unknown tool: {tool_id}")
    binary: Path = tool["binary"]
    installed = binary.is_file() and not binary.is_symlink() and os.access(binary, os.X_OK)
    version = None
    if installed:
        try:
            res = subprocess.run(
                tool["version_cmd"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if res.returncode == 0:
                out = (res.stdout or res.stderr or "").strip()
                match = re.search(tool["version_regex"], out)
                version = match.group(1) if match else (out.split("\n")[0] if out else None)
                installed = bool(version)
            else:
                installed = False
                version = None
        except Exception:
            installed = False
            version = None

    latest_version = tool.get("latest_version")
    has_update = bool(installed and version and latest_version and version_tuple(version) < version_tuple(latest_version))

    return {
        "id": tool_id,
        "name": tool["name"],
        "description": tool["description"],
        "category": tool["category"],
        "installed": installed,
        "path": str(binary),
        "version": version,
        "latest_version": latest_version,
        "has_update": has_update,
    }


def list_tools(_: dict[str, Any]) -> dict[str, Any]:
    return {"tools": [inspect_tool(tid) for tid in TOOLS]}


def install_tool(data: dict[str, Any]) -> dict[str, Any]:
    tool_id = str(data.get("tool") or "").strip().lower()
    tool = TOOLS.get(tool_id)
    if not tool:
        fail(f"Unknown tool: {tool_id}")

    script_path = find_script(tool.get("script_name", ""))
    if script_path:
        run(["bash", str(script_path)], timeout=300)
    else:
        url = tool.get("direct_url")
        binary = tool["binary"]
        if not url:
            fail(f"Installer not available for tool: {tool['name']}")
        print(f"==> Downloading {tool['name']} directly...", file=sys.stderr)
        tmp_target = f"/tmp/{tool_id}.phar"
        run(["curl", "-fL", "--retry", "3", "--connect-timeout", "15", url, "-o", tmp_target], timeout=120)
        sha256 = tool.get("sha256")
        if sha256:
            import hashlib
            with open(tmp_target, "rb") as f:
                actual = hashlib.sha256(f.read()).hexdigest()
            if actual != sha256:
                try:
                    os.unlink(tmp_target)
                except OSError:
                    pass
                fail(f"{tool['name']} checksum verification failed.")
        run(["install", "-m", "0755", tmp_target, str(binary)], timeout=30)
        try:
            os.unlink(tmp_target)
        except OSError:
            pass

    info = inspect_tool(tool_id)
    if not info["installed"]:
        fail(f"Tool {tool['name']} installation failed verification.")
    return {"message": f"{tool['name']} installed successfully.", "tool": info}


def uninstall_tool(data: dict[str, Any]) -> dict[str, Any]:
    tool_id = str(data.get("tool") or "").strip().lower()
    tool = TOOLS.get(tool_id)
    if not tool:
        fail(f"Unknown tool: {tool_id}")
    binary: Path = tool["binary"]
    if binary.is_file() or binary.is_symlink():
        binary.unlink(missing_ok=True)
    info = inspect_tool(tool_id)
    return {"message": f"{tool['name']} uninstalled successfully.", "tool": info}


OPERATIONS = {
    "list_tools": list_tools,
    "install_tool": install_tool,
    "uninstall_tool": uninstall_tool,
}


def main() -> None:
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
    except Exception:
        data = {}
    operation = str(data.get("operation") or "list_tools")
    handler = OPERATIONS.get(operation)
    if handler is None:
        fail(f"Unsupported tool operation: {operation}")
    print(json.dumps({"ok": True, "result": handler(data)}))


if __name__ == "__main__":
    main()
