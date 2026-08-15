"""Native PHP-FPM lifecycle and access helpers for phpMyAdmin."""
from __future__ import annotations

import json
import logging
import os
import re
import socket
import subprocess
import threading
from pathlib import Path
from typing import Any

import config

logger = logging.getLogger(__name__)

# Highest-first ordering of a X.Y PHP version tuple.
_VERSION_KEY = lambda value: tuple(int(part) for part in value.split("."))  # noqa: E731
HOST_RE = re.compile(
    r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9-]{2,63}$"
)
PHP_STATE_PATH = Path("/var/lib/srv-panel/php-runtime/managed-versions.json")


class PhpMyAdminService:
    plugin_id = "phpmyadmin"
    config_version = "1"
    fpm_pool = "srv-panel-phpmyadmin"
    command_timeout = 15

    def __init__(self) -> None:
        self._state_lock = threading.RLock()

    @property
    def data_dir(self) -> Path:
        configured = os.getenv("PHPMYADMIN_DATA_DIR")
        if configured:
            return Path(configured)
        if os.name == "nt":
            return Path(os.getenv("TEMP", "C:/tmp")) / "srv-panel-phpmyadmin"
        return Path("/opt/srv-panel/data/phpmyadmin")

    @property
    def htdocs(self) -> Path:
        return self.data_dir / "htdocs"

    @property
    def sessions_dir(self) -> Path:
        return self.data_dir / "sessions"

    @property
    def tmp_dir(self) -> Path:
        return self.data_dir / "tmp"

    @property
    def state_path(self) -> Path:
        return self.data_dir / "state.json"

    @property
    def secret_path(self) -> Path:
        return self.data_dir / "pma.secret"

    @property
    def marker_path(self) -> Path:
        return self.data_dir / "config_version"

    # ---------------------------------------------------------------
    # Shell helpers
    # ---------------------------------------------------------------
    @staticmethod
    def _command_prefix() -> list[str]:
        if os.name == "nt":
            return []
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            return []
        return ["sudo", "-n"] if config.PRIVILEGED_SUDO else []

    def _run(
        self, command: list[str], *, timeout: int | None = None, input_text: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        full_command = [*self._command_prefix(), *command]
        logger.info("Shell: %s", " ".join(full_command))
        return subprocess.run(
            full_command,
            input=input_text,
            capture_output=True,
            text=True,
            timeout=timeout or self.command_timeout,
            check=False,
            shell=False,
        )

    def _write_owned_file(self, path: Path, content: str) -> None:
        """Write a root-owned file through the privileged prefix."""
        result = self._run(
            ["bash", "-c", f"cat > {shlex_quote(str(path))}"],
            input_text=content,
            timeout=15,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or f"Could not write {path}.")

    # ---------------------------------------------------------------
    # PHP runtime discovery
    # ---------------------------------------------------------------
    def php_version(self) -> str | None:
        """Return the highest panel-managed PHP-FPM version, if any."""
        try:
            data = json.loads(PHP_STATE_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        versions = [
            str(version)
            for version in data
            if isinstance(version, str) and re.fullmatch(r"\d+\.\d+", version)
        ]
        if not versions:
            fpm_dir = Path("/etc/php")
            if fpm_dir.is_dir():
                versions = sorted(
                    (entry.name for entry in fpm_dir.iterdir() if entry.is_dir()),
                    key=_VERSION_KEY,
                )
        if not versions:
            return None
        return sorted(versions, key=_VERSION_KEY)[-1]

    def socket_path(self) -> Path | None:
        version = self.php_version()
        if not version:
            return None
        return Path(f"/run/php/srv-panel-phpmyadmin-{version}.sock")

    def pool_conf_path(self) -> Path | None:
        version = self.php_version()
        if not version:
            return None
        return Path(f"/etc/php/{version}/fpm/pool.d/srv-panel-phpmyadmin.conf")

    # ---------------------------------------------------------------
    # Status
    # ---------------------------------------------------------------
    def is_installed(self) -> bool:
        return (self.htdocs / "index.php").is_file()

    def needs_reconcile(self) -> bool:
        """Return true when installed files were built by older plugin code."""
        if not self.is_installed():
            return False
        try:
            marker = self.marker_path.read_text(encoding="utf-8").strip()
        except OSError:
            return True
        return marker != self.config_version

    @staticmethod
    def mariadb_reachable() -> bool:
        if os.name == "nt":
            return False
        try:
            with socket.create_connection(("127.0.0.1", 3306), timeout=1.0):
                return True
        except OSError:
            return False

    def get_status(self) -> dict[str, Any]:
        status = {
            "installed": False,
            "running": False,
            "healthy": False,
            "state": "missing",
            "error": None,
            "php_version": self.php_version(),
            "pid": None,
        }
        if not self.is_installed():
            return status
        status["installed"] = True
        version = self.php_version()
        if not version:
            status["state"] = "error"
            status["error"] = "No panel-managed PHP-FPM version is installed."
            return status
        socket_path = self.socket_path()
        running = bool(socket_path and socket_path.exists())
        mariadb = self.mariadb_reachable()
        healthy = running and mariadb
        status.update(
            {
                "running": running,
                "healthy": healthy,
                "state": "healthy" if healthy else ("running" if running else "stopped"),
                "error": (
                    None
                    if healthy
                    else (
                        "PHP-FPM socket is not ready."
                        if not running
                        else "MariaDB is not reachable on 127.0.0.1:3306."
                    )
                ),
            }
        )
        return status

    def get_usage(self) -> dict[str, Any]:
        """Report the plugin's PHP-FPM worker count to the Usage page."""
        status = self.get_status()
        row = {
            "cpu": 0.0,
            "mem": 0.0,
            "memory": "0 MB",
            "count": 0,
            "status": status["state"],
        }
        if not status["installed"] or not status["running"]:
            return row
        try:
            result = self._run(
                ["pgrep", "-f", f"pool: {self.fpm_pool}"], timeout=5
            )
            row["count"] = len(result.stdout.split()) if result.returncode == 0 else 0
            row["status"] = "running" if status["healthy"] else "unhealthy"
        except (OSError, subprocess.TimeoutExpired):
            row["status"] = "unhealthy"
        return row

    def pause(self) -> None:
        """Hide phpMyAdmin behind a 503 without touching shared PHP-FPM."""
        site = self.get_site()
        if site and site.get("public_host"):
            self._write_offline_site(site["public_host"], site)
        self.update_state(paused=True)

    def resume(self) -> None:
        """Restore the public phpMyAdmin site after being re-enabled."""
        site = self.get_site()
        if site and site.get("public_host"):
            self._write_live_site(site["public_host"], site)
        self.update_state(paused=False)

    # ---------------------------------------------------------------
    # State
    # ---------------------------------------------------------------
    def read_state(self) -> dict[str, Any]:
        with self._state_lock:
            try:
                data = json.loads(self.state_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return {"schema_version": 1}
            return data if isinstance(data, dict) else {"schema_version": 1}

    def write_state(self, state: dict[str, Any]) -> None:
        with self._state_lock:
            normalized = dict(state)
            normalized["schema_version"] = 1
            self.data_dir.mkdir(parents=True, exist_ok=True)
            temp = self.state_path.with_suffix(".tmp")
            temp.write_text(json.dumps(normalized, indent=2), encoding="utf-8")
            temp.replace(self.state_path)

    def update_state(self, **changes: Any) -> dict[str, Any]:
        with self._state_lock:
            state = self.read_state()
            state.update(changes)
            self.write_state(state)
            return state

    def get_site(self) -> dict[str, Any] | None:
        site = self.read_state().get("site")
        return dict(site) if isinstance(site, dict) else None

    def save_site(self, site: dict[str, Any]) -> dict[str, Any]:
        with self._state_lock:
            state = self.read_state()
            state["site"] = dict(site)
            self.write_state(state)
        return dict(site)

    def delete_site(self) -> dict[str, Any] | None:
        with self._state_lock:
            state = self.read_state()
            removed = state.pop("site", None)
            self.write_state(state)
        return dict(removed) if isinstance(removed, dict) else None

    def is_paused(self) -> bool:
        return bool(self.read_state().get("paused"))

    def get_public_url(self) -> str | None:
        site = self.get_site()
        if not site or site.get("ssl_status") != "ready":
            return None
        host = site.get("public_host")
        return f"https://{host}/" if isinstance(host, str) and host else None

    def get_configured_url(self) -> str | None:
        site = self.get_site()
        if not site:
            return None
        host = site.get("public_host")
        if not isinstance(host, str) or not host:
            return None
        scheme = "https" if site.get("ssl_status") == "ready" else "http"
        return f"{scheme}://{host}/"

    def purge_data(self) -> None:
        """Remove panel-side state and secrets after uninstall."""
        if self.is_installed():
            raise RuntimeError("Uninstall phpMyAdmin before purging its data.")
        for path in (self.state_path, self.secret_path, self.marker_path):
            try:
                path.unlink()
            except FileNotFoundError:
                pass

    # ---------------------------------------------------------------
    # Nginx helpers (shared by pause/resume, subprocess-based)
    # ---------------------------------------------------------------
    @staticmethod
    def _nginx_conf_name(host: str) -> str:
        return f"{host}.conf"

    def _nginx_write(self, host: str, content: str) -> None:
        from utils import nginx_templates

        name = self._nginx_conf_name(host)
        available = Path(config.NGINX_SITES_AVAILABLE) / name
        enabled = Path(config.NGINX_SITES_ENABLED) / name
        self._write_owned_file(available, content)
        result = self._run(
            ["bash", "-c", f"ln -sf {shlex_quote(str(available))} {shlex_quote(str(enabled))}"],
            timeout=10,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "Could not enable nginx config.")
        test = self._run(["nginx", "-t"], timeout=15)
        if test.returncode != 0:
            raise RuntimeError(test.stderr.strip() or "Nginx config test failed.")
        reload_result = self._run(["systemctl", "reload", "nginx"], timeout=30)
        if reload_result.returncode != 0:
            raise RuntimeError(
                reload_result.stderr.strip() or "Nginx reload failed."
            )

    def _site_nginx_content(self, host: str, site: dict[str, Any]) -> str:
        from utils import nginx_templates

        socket_path = self.socket_path()
        if not socket_path:
            raise RuntimeError("PHP-FPM socket is unavailable.")
        logs_dir = Path("/var/log/nginx")
        access_log = logs_dir / "phpmyadmin.access.log"
        error_log = logs_dir / "phpmyadmin.error.log"
        if site.get("ssl_status") == "ready":
            cert = f"/etc/letsencrypt/live/{host}/fullchain.pem"
            key = f"/etc/letsencrypt/live/{host}/privkey.pem"
            return nginx_templates.php_site_ssl_config(
                host, str(self.htdocs), str(socket_path),
                str(access_log), str(error_log), cert, key,
            )
        return nginx_templates.php_site_config(
            host, str(self.htdocs), str(socket_path),
            str(access_log), str(error_log),
        )

    def _write_live_site(self, host: str, site: dict[str, Any]) -> None:
        self._nginx_write(host, self._site_nginx_content(host, site))

    def _write_offline_site(self, host: str, site: dict[str, Any]) -> None:
        from utils import nginx_templates

        cert = (
            f"/etc/letsencrypt/live/{host}/fullchain.pem"
            if site.get("ssl_status") == "ready"
            else None
        )
        key = (
            f"/etc/letsencrypt/live/{host}/privkey.pem"
            if site.get("ssl_status") == "ready"
            else None
        )
        self._nginx_write(host, nginx_templates.php_site_offline_config(host, cert_path=cert, key_path=key))


def shlex_quote(value: str) -> str:
    """Minimal single-quote shell escaping for fixed internal paths."""
    return "'" + value.replace("'", "'\\''") + "'"


phpmyadmin_service = PhpMyAdminService()
