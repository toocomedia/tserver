"""Panel client for root-owned Filament setup on Laravel PHP websites."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import config
from models.php_website import PhpWebsite


HELPER_PATH = Path("/usr/local/lib/srv-panel/php-site-filament-manager")


def _command() -> list[str]:
    command = [str(HELPER_PATH)]
    if os.name != "nt" and hasattr(os, "geteuid") and os.geteuid() != 0 and config.PRIVILEGED_SUDO:
        return ["sudo", "-n", *command]
    return command


def call(operation: str, *, timeout: int = 180, **values: Any) -> dict[str, Any]:
    if os.name == "nt":
        raise RuntimeError("Filament website setup is available only on Linux.")
    if not HELPER_PATH.is_file():
        raise RuntimeError("Filament installer is missing. Run the SRV Panel updater first.")
    try:
        result = subprocess.run(_command(), input=json.dumps({"operation": operation, **values}), capture_output=True, text=True, timeout=timeout, check=False, shell=False)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Filament setup timed out.") from exc
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout or "Filament setup failed.").strip()[-2000:])
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Filament installer returned an invalid response.") from exc
    if not payload.get("ok") or not isinstance(payload.get("result"), dict):
        raise RuntimeError("Filament setup failed.")
    return dict(payload["result"])


def status() -> dict[str, Any]:
    return call("status", timeout=90)


def install(site: PhpWebsite, domain: str, filament: dict[str, str]) -> dict[str, Any]:
    return call(
        "install", site_id=site.id, domain=domain, version=site.php_version,
        document_root=site.document_root, filament=filament, timeout=1200,
    )
