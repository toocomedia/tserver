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


SCRIPTS_DIR = Path(__file__).resolve().parent

TOOLS = {
    "composer": {
        "name": "Composer",
        "binary": Path("/usr/local/bin/composer"),
        "description": "Dependency manager for modern PHP applications, Laravel, and Filament",
        "category": "Package Management",
        "install_script": SCRIPTS_DIR / "install_composer.sh",
        "version_cmd": ["/usr/local/bin/composer", "--version"],
        "version_regex": r"Composer\s+version\s+([0-9.]+)",
    },
    "wp": {
        "name": "WP-CLI",
        "binary": Path("/usr/local/bin/wp"),
        "description": "Command-line interface for WordPress management and automation",
        "category": "WordPress CLI",
        "install_script": SCRIPTS_DIR / "install_wp_cli.sh",
        "version_cmd": ["/usr/local/bin/wp", "--allow-root", "--version"],
        "version_regex": r"WP-CLI\s+([0-9.]+)",
    },
}


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
            out = res.stdout or res.stderr
            match = re.search(tool["version_regex"], out)
            version = match.group(1) if match else out.strip().split("\n")[0]
        except Exception:
            version = "Installed (version unknown)"

    return {
        "id": tool_id,
        "name": tool["name"],
        "description": tool["description"],
        "category": tool["category"],
        "installed": installed,
        "path": str(binary),
        "version": version,
    }


def list_tools(_: dict[str, Any]) -> dict[str, Any]:
    return {"tools": [inspect_tool(tid) for tid in TOOLS]}


def install_tool(data: dict[str, Any]) -> dict[str, Any]:
    tool_id = str(data.get("tool") or "").strip().lower()
    tool = TOOLS.get(tool_id)
    if not tool:
        fail(f"Unknown tool: {tool_id}")
    script: Path = tool["install_script"]
    if not script.is_file():
        fail(f"Installer script not found: {script.name}")
    run(["bash", str(script)], timeout=300)
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
