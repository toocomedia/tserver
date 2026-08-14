"""Panel client for the isolated root-owned Laravel site helper."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import config
from models.php_website import PhpWebsite


HELPER_PATH = Path("/usr/local/lib/srv-panel/php-site-laravel-manager")


def _command() -> list[str]:
    command = [str(HELPER_PATH)]
    if os.name != "nt" and hasattr(os, "geteuid") and os.geteuid() != 0 and config.PRIVILEGED_SUDO:
        return ["sudo", "-n", *command]
    return command


def call(operation: str, *, timeout: int = 180, **values: Any) -> dict[str, Any]:
    if os.name == "nt":
        raise RuntimeError("Laravel website runtime management is available only on Linux.")
    if not HELPER_PATH.is_file():
        raise RuntimeError("Laravel installer is missing. Run the SRV Panel updater first.")
    try:
        result = subprocess.run(
            _command(), input=json.dumps({"operation": operation, **values}),
            capture_output=True, text=True, timeout=timeout, check=False, shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Laravel website runtime operation timed out.") from exc
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "Laravel website runtime operation failed.").strip()[-2000:])
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Laravel installer returned an invalid response.") from exc
    if not payload.get("ok") or not isinstance(payload.get("result"), dict):
        raise RuntimeError("Laravel website runtime operation failed.")
    return dict(payload["result"])


def site_values(site: PhpWebsite, domain: str) -> dict[str, Any]:
    return {
        "site_id": site.id,
        "domain": domain,
        "version": site.php_version,
        "document_root": site.document_root,
    }


def status(version: str) -> dict[str, Any]:
    return call("status", version=version, timeout=90)


def install_extensions(version: str) -> dict[str, Any]:
    return call("install_extensions", version=version, timeout=900)


def install(
    site: PhpWebsite, domain: str, database: dict[str, str], *, https: bool,
) -> dict[str, Any]:
    return call(
        "install", **site_values(site, domain), database=database,
        scheme="https" if https else "http", timeout=1200,
    )


def update_url(site: PhpWebsite, domain: str, *, https: bool) -> dict[str, Any]:
    return call(
        "update_url", **site_values(site, domain),
        scheme="https" if https else "http", timeout=180,
    )


def update_database_password(
    site: PhpWebsite, domain: str, database: dict[str, str],
) -> dict[str, Any]:
    return call(
        "update_database_password", **site_values(site, domain), database=database, timeout=180,
    )
