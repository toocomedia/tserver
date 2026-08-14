"""Panel client for the fixed root-owned native PHP site helper."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import config
from models.php_website import PhpWebsite


HELPER_PATH = Path("/usr/local/lib/srv-panel/php-site-manager")


def socket_path(site_id: int, version: str) -> str:
    return f"/run/php/srv-site-{site_id}-{version}.sock"


def state_root(site_id: int) -> Path:
    return Path(config.PHP_SITE_STATE_ROOT) / str(site_id)


def credentials_path(site_id: int) -> Path:
    return state_root(site_id) / "database.env"


def _command() -> list[str]:
    command = [str(HELPER_PATH)]
    if os.name != "nt" and hasattr(os, "geteuid") and os.geteuid() != 0 and config.PRIVILEGED_SUDO:
        return ["sudo", "-n", *command]
    return command


def call(operation: str, *, timeout: int = 180, **values: Any) -> dict[str, Any]:
    if os.name == "nt":
        raise RuntimeError("PHP website runtime management is available only on Linux.")
    if not HELPER_PATH.is_file():
        raise RuntimeError("PHP site helper is missing. Run the SRV Panel updater first.")
    try:
        result = subprocess.run(
            _command(), input=json.dumps({"operation": operation, **values}),
            capture_output=True, text=True, timeout=timeout, check=False, shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("PHP website runtime operation timed out.") from exc
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "PHP website runtime operation failed.").strip()[-2000:])
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("PHP site helper returned an invalid response.") from exc
    if not payload.get("ok") or not isinstance(payload.get("result"), dict):
        raise RuntimeError("PHP website runtime operation failed.")
    return dict(payload["result"])


def site_values(site: PhpWebsite, domain: str) -> dict[str, Any]:
    return {
        "site_id": site.id,
        "domain": domain,
        "version": site.php_version,
        "document_root": site.document_root,
        "panel_user": config.APP_HOSTING_USER,
    }


def provision(site: PhpWebsite, domain: str, *, database: dict[str, str] | None = None) -> dict[str, Any]:
    return call("provision", **site_values(site, domain), database=database or {})


def prepare_version(site: PhpWebsite, domain: str, version: str, *, database: dict[str, str] | None = None) -> dict[str, Any]:
    return call(
        "prepare_version", **site_values(site, domain), new_version=version,
        database=database or {},
    )


def finalize_version(site: PhpWebsite, old_version: str) -> dict[str, Any]:
    return call("finalize_version", site_id=site.id, old_version=old_version)


def set_enabled(site: PhpWebsite, domain: str, enabled: bool, *, database: dict[str, str] | None = None) -> dict[str, Any]:
    return call(
        "enable" if enabled else "disable", **site_values(site, domain),
        database=database or {},
    )


def purge(site: PhpWebsite, domain: str) -> dict[str, Any]:
    return call("purge", **site_values(site, domain), timeout=300)


def read_logs(site: PhpWebsite, stream: str, lines: int) -> dict[str, Any]:
    return call("read_logs", site_id=site.id, stream=stream, lines=lines, timeout=20)


def install_wordpress(
    site: PhpWebsite, domain: str, database: dict[str, str], wordpress: dict[str, str], *, https: bool,
) -> dict[str, Any]:
    return call(
        "install_wordpress", **site_values(site, domain), database=database,
        wordpress=wordpress, scheme="https" if https else "http", timeout=600,
    )


def update_wordpress_url(site: PhpWebsite, domain: str, *, https: bool) -> dict[str, Any]:
    return call(
        "wordpress_url", **site_values(site, domain),
        scheme="https" if https else "http", timeout=120,
    )


def install_wordpress_extensions(version: str) -> dict[str, Any]:
    return call("install_wordpress_extensions", version=version, timeout=900)


def wordpress_extension_status(version: str) -> dict[str, Any]:
    return call("check_wordpress_extensions", version=version, timeout=90)


def database_extension_status(version: str) -> dict[str, Any]:
    return call("check_database_extension", version=version, timeout=30)


def install_database_extension(version: str) -> dict[str, Any]:
    return call("install_database_extension", version=version, timeout=900)


def update_wordpress_database_password(
    site: PhpWebsite, domain: str, database: dict[str, str],
) -> dict[str, Any]:
    return call(
        "wordpress_database_password", **site_values(site, domain),
        database=database, timeout=120,
    )


def clear_wordpress_cache(site: PhpWebsite, domain: str) -> dict[str, Any]:
    return call("clear_wordpress_cache", **site_values(site, domain), timeout=300)
