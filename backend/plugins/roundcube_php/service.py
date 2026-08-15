"""Native PHP-FPM Roundcube Webmail lifecycle, settings, and launch-token service."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import config

logger = logging.getLogger(__name__)

_VERSION_KEY = lambda value: tuple(int(part) for part in value.split("."))  # noqa: E731
PHP_STATE_PATH = Path("/var/lib/srv-panel/php-runtime/managed-versions.json")


class RoundcubePhpService:
    plugin_id = "roundcube_php"
    config_version = "1"
    unit_name = "srv-panel-roundcube-php"
    host = "127.0.0.1"
    default_port = 8089
    launch_ttl_seconds = 60
    command_timeout = 15

    def __init__(self) -> None:
        self._state_lock = threading.RLock()

    @property
    def data_dir(self) -> Path:
        configured = os.getenv("ROUNDCUBE_PHP_DATA_DIR")
        if configured:
            return Path(configured)
        if os.name == "nt":
            return Path(os.getenv("TEMP", "C:/tmp")) / "srv-panel-roundcube-php"
        return Path("/opt/srv-panel/data/roundcube_php")

    @property
    def htdocs(self) -> Path:
        return self.data_dir / "htdocs"

    @property
    def state_path(self) -> Path:
        return self.data_dir / "state.json"

    @property
    def secret_path(self) -> Path:
        return self.data_dir / "launch.secret"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "db" / "roundcube.db"

    @property
    def tmp_dir(self) -> Path:
        return self.data_dir / "tmp"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def marker_path(self) -> Path:
        return self.data_dir / "config_version"

    @property
    def port(self) -> int:
        env_port = os.getenv("ROUNDCUBE_PHP_PORT")
        if env_port:
            try:
                return int(env_port)
            except ValueError:
                pass
        stored = self.read_state().get("port")
        if isinstance(stored, int) and 1024 <= stored <= 65535:
            return stored
        if os.name != "nt":
            unit_file = Path(f"/etc/systemd/system/{self.unit_name}.service")
            if unit_file.is_file():
                try:
                    content = unit_file.read_text(encoding="utf-8")
                    match = re.search(r"-S\s+(?:127\.0\.0\.1|localhost):(\d+)", content)
                    if match:
                        return int(match.group(1))
                except Exception:
                    pass
        return self.default_port

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

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
        self, command: list[str], *, timeout: int | None = None
    ) -> subprocess.CompletedProcess[str]:
        full_command = [*self._command_prefix(), *command]
        logger.info("Shell: %s", " ".join(full_command))
        return subprocess.run(
            full_command,
            capture_output=True,
            text=True,
            timeout=timeout or self.command_timeout,
            check=False,
            shell=False,
        )

    # ---------------------------------------------------------------
    # PHP runtime discovery
    # ---------------------------------------------------------------
    def php_version(self) -> str | None:
        """Highest panel-managed PHP version, else highest installed directory."""
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

    def php_binary(self) -> str | None:
        version = self.php_version()
        if not version:
            return None
        candidates = [
            f"/usr/bin/php{version}",
            "/usr/bin/php",
        ]
        for candidate in candidates:
            if os.path.isfile(candidate):
                return candidate
        return None

    # ---------------------------------------------------------------
    # Status & Health
    # ---------------------------------------------------------------
    def is_installed(self) -> bool:
        if os.name == "nt":
            return (self.htdocs / "index.php").is_file()
        unit_file = Path(f"/etc/systemd/system/{self.unit_name}.service")
        return unit_file.is_file() and (self.htdocs / "index.php").is_file()

    def needs_reconcile(self) -> bool:
        if not self.is_installed():
            return False
        try:
            marker = self.marker_path.read_text(encoding="utf-8").strip()
        except OSError:
            return True
        return marker != self.config_version

    @staticmethod
    def _port_open(host: str, port: int) -> bool:
        try:
            with socket.create_connection((host, port), timeout=1.5) as sock:
                try:
                    sock.sendall(b"HEAD / HTTP/1.0\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")
                    sock.recv(64)
                except OSError:
                    pass
                return True
        except OSError:
            return False

    def _unit_state(self) -> tuple[bool, str]:
        if os.name == "nt":
            return False, "unknown"
        result = self._run(["systemctl", "is-active", self.unit_name], timeout=10)
        state = (result.stdout or "").strip().lower() or "unknown"
        exists = result.returncode in (0, 3) or state in (
            "active", "inactive", "failed", "activating", "deactivating",
        )
        return exists, state

    def unit_logs(self, lines: int = 12) -> str:
        if os.name == "nt":
            return ""
        result = self._run(
            ["journalctl", "-u", self.unit_name, "-n", str(lines), "--no-pager", "-o", "cat"],
            timeout=10,
        )
        return (result.stdout or result.stderr or "").strip()

    def get_status(self) -> dict[str, Any]:
        status = {
            "installed": False,
            "running": False,
            "healthy": False,
            "state": "missing",
            "error": None,
            "php_version": self.php_version(),
            "port": self.port,
            "unit_exists": False,
            "unit_state": "unknown",
            "unit_logs": "",
            "db_size": self._db_size_formatted(),
            "sites_count": len(self.get_sites()),
            "pid": None,
        }
        if not self.is_installed():
            return status
        status["installed"] = True
        if not self.php_binary():
            status["state"] = "error"
            status["error"] = "No panel-managed PHP CLI is installed."
            return status
        unit_exists, unit_state = self._unit_state()
        running = self._port_open(self.host, self.port)
        healthy = running and unit_state == "active"
        if unit_exists and not running:
            status["unit_logs"] = self.unit_logs()
        status.update(
            {
                "running": running,
                "healthy": healthy,
                "state": "healthy" if healthy else ("running" if running else "stopped"),
                "unit_exists": unit_exists,
                "unit_state": unit_state,
                "error": (
                    None
                    if healthy
                    else (
                        f"Roundcube PHP service is not active (systemd: {unit_state})."
                        if not running
                        else None
                    )
                ),
            }
        )
        return status

    def get_usage(self) -> dict[str, Any]:
        status = self.get_status()
        row = {
            "cpu": 0.0,
            "mem": 0.0,
            "memory": "0 MB",
            "count": 0,
            "status": status["state"],
        }
        if not status["installed"]:
            return row
        try:
            result = self._run(
                ["pgrep", "-f", f"php .*-S {self.host}:{self.port}"],
                timeout=5,
            )
            row["count"] = len(result.stdout.split()) if result.returncode == 0 else 0
            row["status"] = "running" if status["healthy"] else "unhealthy"
        except (OSError, subprocess.TimeoutExpired):
            row["status"] = "unhealthy"
        return row

    def pause(self) -> None:
        if not self.is_installed() or os.name == "nt":
            return
        result = self._run(["systemctl", "stop", self.unit_name], timeout=30)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or f"Could not stop {self.unit_name}.")

    def patch_php_compatibility(self) -> bool:
        """Patches Roundcube 1.6.x for PHP 8.4+ / 8.5 compatibility (native array_first redeclaration fix)."""
        bootstrap_path = self.htdocs / "program" / "lib" / "Roundcube" / "bootstrap.php"
        if not bootstrap_path.is_file():
            return False
        try:
            code = bootstrap_path.read_text(encoding="utf-8", errors="ignore")
            modified = False
            for fn in ["array_first", "array_last", "array_find", "array_find_key", "array_any", "array_all"]:
                search_target = f"function {fn}("
                idx = code.find(search_target)
                if idx == -1:
                    continue
                if f"function_exists('{fn}')" in code or f'function_exists("{fn}")' in code:
                    continue
                brace_start = code.find("{", idx)
                if brace_start == -1:
                    continue
                depth = 1
                i = brace_start + 1
                while i < len(code) and depth > 0:
                    if code[i] == "{":
                        depth += 1
                    elif code[i] == "}":
                        depth -= 1
                    i += 1
                func_block = code[idx:i]
                replacement = f"if (!function_exists('{fn}')) {{\n{func_block}\n}}"
                code = code[:idx] + replacement + code[i:]
                modified = True

            if modified:
                bootstrap_path.write_text(code, encoding="utf-8")
                return True
        except (OSError, PermissionError):
            pass
        return False

    def resume(self) -> None:
        if not self.is_installed() or os.name == "nt":
            raise RuntimeError("Roundcube PHP service is not installed.")
        self.patch_php_compatibility()
        result = self._run(["systemctl", "restart", self.unit_name], timeout=30)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or f"Could not start {self.unit_name}.")

    # ---------------------------------------------------------------
    # Maddy Mail Connection Diagnostics
    # ---------------------------------------------------------------
    def diagnose_mail_connection(self) -> dict[str, Any]:
        """Direct TCP socket probe for Maddy IMAP and SMTP listeners."""
        import socket
        import ssl

        def probe_tcp(host: str, port: int, use_ssl: bool = False, timeout: float = 3.0) -> dict[str, Any]:
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(timeout)
                if use_ssl:
                    ctx = ssl.create_default_context()
                    ctx.check_hostname = False
                    ctx.verify_mode = ssl.CERT_NONE
                    sock = ctx.wrap_socket(sock)
                sock.connect((host, port))
                banner = ""
                try:
                    data = sock.recv(512)
                    banner = data.decode("utf-8", errors="ignore").strip()
                except Exception:
                    pass
                sock.close()
                return {"ok": True, "host": host, "port": port, "banner": banner}
            except Exception as exc:
                return {"ok": False, "host": host, "port": port, "error": str(exc)}

        imap = probe_tcp("127.0.0.1", 143, False)
        if not imap["ok"]:
            imap = probe_tcp("127.0.0.1", 993, True)

        smtp = probe_tcp("127.0.0.1", 587, False)
        if not smtp["ok"]:
            smtp = probe_tcp("127.0.0.1", 465, True)

        return {
            "ok": bool(imap["ok"] and smtp["ok"]),
            "imap": imap,
            "smtp": smtp,
            "transport": "local",
        }

    # ---------------------------------------------------------------
    # Multi-Domain Sites State
    # ---------------------------------------------------------------
    def read_state(self) -> dict[str, Any]:
        with self._state_lock:
            try:
                data = json.loads(self.state_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return {"schema_version": 2, "sites": {}, "settings": self.default_settings()}
            if not isinstance(data, dict):
                return {"schema_version": 2, "sites": {}, "settings": self.default_settings()}
            if "settings" not in data or not isinstance(data["settings"], dict):
                data["settings"] = self.default_settings()
            return data

    def write_state(self, state: dict[str, Any]) -> None:
        with self._state_lock:
            normalized = dict(state)
            normalized["schema_version"] = 2
            if not isinstance(normalized.get("sites"), dict):
                normalized["sites"] = {}
            if not isinstance(normalized.get("settings"), dict):
                normalized["settings"] = self.default_settings()
            self.data_dir.mkdir(parents=True, exist_ok=True)
            temp = self.state_path.with_suffix(".tmp")
            temp.write_text(json.dumps(normalized, indent=2), encoding="utf-8")
            try:
                temp.chmod(0o664)
            except OSError:
                pass
            temp.replace(self.state_path)
            try:
                self.state_path.chmod(0o664)
            except OSError:
                pass

    def update_state(self, **changes: Any) -> dict[str, Any]:
        with self._state_lock:
            state = self.read_state()
            state.update(changes)
            self.write_state(state)
            return state

    def get_sites(self) -> dict[str, dict[str, Any]]:
        sites = self.read_state().get("sites", {})
        return {
            str(domain): dict(site)
            for domain, site in sites.items()
            if isinstance(domain, str) and isinstance(site, dict)
        }

    def get_site(self, domain: str) -> dict[str, Any] | None:
        site = self.get_sites().get(domain.strip().lower())
        return dict(site) if site else None

    def save_site(self, domain: str, site: dict[str, Any]) -> dict[str, Any]:
        domain = domain.strip().lower()
        with self._state_lock:
            state = self.read_state()
            sites = dict(state.get("sites", {}))
            sites[domain] = dict(site)
            state["sites"] = sites
            self.write_state(state)
        return dict(site)

    def update_site(self, domain: str, **changes: Any) -> dict[str, Any]:
        current = self.get_site(domain)
        if current is None:
            raise KeyError(domain)
        current.update(changes)
        return self.save_site(domain, current)

    def delete_site(self, domain: str) -> dict[str, Any] | None:
        domain = domain.strip().lower()
        with self._state_lock:
            state = self.read_state()
            sites = dict(state.get("sites", {}))
            removed = sites.pop(domain, None)
            state["sites"] = sites
            self.write_state(state)
        return dict(removed) if isinstance(removed, dict) else None

    def get_public_url(self, domain: str | None = None) -> str | None:
        if domain is not None:
            site = self.get_site(domain)
            if not site or site.get("ssl_status") != "ready":
                return None
            host = site.get("public_host")
            return f"https://{host}/" if isinstance(host, str) and host else None
        for site_domain in self.get_sites():
            url = self.get_public_url(site_domain)
            if url:
                return url
        return None

    def get_configured_url(self, domain: str) -> str | None:
        site = self.get_site(domain)
        if not site:
            return None
        host = site.get("public_host")
        if not isinstance(host, str) or not host:
            return None
        scheme = "https" if site.get("ssl_status") == "ready" else "http"
        return f"{scheme}://{host}/"

    # ---------------------------------------------------------------
    # Custom Settings & Options
    # ---------------------------------------------------------------
    @staticmethod
    def default_settings() -> dict[str, Any]:
        return {
            "skin": "elastic",
            "product_name": "SRV Webmail",
            "max_message_size": "32M",
            "session_lifetime": 30,
            "plugins": ["archive", "zipdownload", "markasjunk", "srvpanel_launch"],
        }

    def get_settings(self) -> dict[str, Any]:
        settings = self.read_state().get("settings", {})
        merged = self.default_settings()
        if isinstance(settings, dict):
            merged.update(settings)
        return merged

    def sync_config_file(self) -> None:
        """Generates and writes /opt/srv-panel/data/roundcube_php/htdocs/config/config.inc.php directly."""
        config_dir = self.htdocs / "config"
        if not config_dir.is_dir():
            return
        config_file = config_dir / "config.inc.php"

        settings = self.get_settings()
        requested_skin = settings.get("skin") or "elastic"
        # Validate that skin directory exists in htdocs/skins to avoid Roundcube 404
        skin_dir = self.htdocs / "skins" / requested_skin
        skin = requested_skin if skin_dir.is_dir() else "elastic"
        raw_title = settings.get("product_name") or "SRV Webmail"
        product_name = raw_title.replace("'", "\\'")
        max_message_size = settings.get("max_message_size") or "32M"
        session_lifetime = int(settings.get("session_lifetime") or 30)
        plugins_list = list(settings.get("plugins") or ["archive", "zipdownload", "markasjunk", "srvpanel_launch"])
        if "srvpanel_launch" not in plugins_list:
            plugins_list.append("srvpanel_launch")
        plugins_repr = ", ".join(f"'{p}'" for p in plugins_list)

        php_content = f"""<?php

// Database connection — SQLite for lightweight native execution
$db_path = '{self.db_path}';
$config['db_dsnw'] = 'sqlite:///' . $db_path . '?mode=0646';

// DES Encryption key for session cookies (must be exactly 24 chars)
$des_file = dirname($db_path) . '/des_key.secret';
if (file_exists($des_file) && ($key = trim(file_get_contents($des_file))) && strlen($key) >= 24) {{
    $config['des_key'] = substr($key, 0, 24);
}} else {{
    $config['des_key'] = 'rcmail-!srv-panel-key24!';
}}

// Mail server transport settings — direct localhost connection to Maddy
$maddy_host = '127.0.0.1';
$config['imap_host'] = $maddy_host . ':143';
$config['smtp_host'] = $maddy_host . ':587';
$config['smtp_user'] = '%u';
$config['smtp_pass'] = '%p';

$local_tls = [
    'verify_peer' => false,
    'verify_peer_name' => false,
    'allow_self_signed' => true,
];
$config['imap_conn_options'] = ['ssl' => $local_tls];
$config['smtp_conn_options'] = ['ssl' => $local_tls];

// Authentication & Session settings
$config['auto_create_user'] = true;
$config['login_lc'] = 2;
$config['login_autocomplete'] = 1;
$config['session_lifetime'] = {session_lifetime};

// UI & Presentation
$config['skin'] = '{skin}';
$config['product_name'] = '{product_name}';
$config['dont_override'] = ['skin'];
$config['use_https'] = true;
$config['request_path'] = '/';
$config['remote_resources'] = false;
$config['max_message_size'] = '{max_message_size}';

// Active Roundcube Plugins
$config['plugins'] = [{plugins_repr}];
"""
        written = False
        try:
            config_file.write_text(php_content, encoding="utf-8")
            try:
                config_file.chmod(0o664)
            except OSError:
                pass
            written = True
        except (OSError, PermissionError) as exc:
            logger.warning("Direct write to %s failed (%s), attempting privileged write fallback", config_file, exc)
            if os.name != "nt":
                try:
                    import tempfile
                    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as tf:
                        tf.write(php_content)
                        temp_name = tf.name
                    self._run(["cp", "-f", temp_name, str(config_file)])
                    self._run(["chmod", "0664", str(config_file)])
                    self._run(["chown", "www-data:www-data", str(config_file)])
                    try:
                        os.unlink(temp_name)
                    except OSError:
                        pass
                    written = True
                except Exception as f_exc:
                    logger.error("Privileged write fallback failed for %s: %s", config_file, f_exc)

    def _sync_db_skin(self, new_skin: str) -> None:
        if not self.db_path.is_file():
            return
        try:
            import sqlite3
            con = sqlite3.connect(str(self.db_path))
            cur = con.cursor()
            cur.execute("SELECT user_id, preferences FROM users WHERE preferences IS NOT NULL AND preferences != ''")
            rows = cur.fetchall()
            for uid, prefs in rows:
                if isinstance(prefs, str) and "skin" in prefs:
                    updated_prefs = re.sub(
                        r's:4:"skin";s:\d+:"[^"]+"',
                        f's:4:"skin";s:{len(new_skin)}:"{new_skin}"',
                        prefs
                    )
                    if updated_prefs != prefs:
                        cur.execute("UPDATE users SET preferences = ? WHERE user_id = ?", (updated_prefs, uid))
            con.commit()
            con.close()
        except Exception as exc:
            logger.warning("Could not sync skin in Roundcube database: %s", exc)

    def update_settings(self, **new_settings: Any) -> dict[str, Any]:
        with self._state_lock:
            state = self.read_state()
            settings = dict(state.get("settings", self.default_settings()))
            settings.update(new_settings)
            state["settings"] = settings
            self.write_state(state)
            self.sync_config_file()
            if "skin" in new_settings:
                self._sync_db_skin(str(new_settings["skin"]))
            if self.is_installed() and os.name != "nt":
                try:
                    self.resume()
                except Exception as exc:
                    logger.warning("Could not auto-restart Roundcube service after settings update: %s", exc)
            return settings

    # ---------------------------------------------------------------
    # Database Stats & Maintenance
    # ---------------------------------------------------------------
    def _db_size_formatted(self) -> str:
        try:
            if self.db_path.is_file():
                size = self.db_path.stat().st_size
                for unit in ["B", "KB", "MB", "GB"]:
                    if size < 1024:
                        return f"{size:.1f} {unit}"
                    size /= 1024
        except (OSError, PermissionError):
            pass
        return "0 KB"

    def get_db_stats(self) -> dict[str, Any]:
        stats = {
            "db_type": "SQLite",
            "db_path": str(self.db_path),
            "db_size": self._db_size_formatted(),
            "users_count": 0,
            "contacts_count": 0,
        }
        if os.name == "nt":
            return stats
        try:
            if not self.db_path.is_file():
                return stats
            query = "SELECT (SELECT count(*) FROM users), (SELECT count(*) FROM contacts);"
            result = self._run(["sqlite3", str(self.db_path), query], timeout=5)
            if result.returncode == 0 and "|" in result.stdout:
                users, contacts = result.stdout.strip().split("|", 1)
                stats["users_count"] = int(users)
                stats["contacts_count"] = int(contacts)
        except (OSError, PermissionError, Exception):
            pass
        return stats

    def optimize_db(self) -> None:
        if os.name == "nt":
            return
        try:
            if not self.db_path.is_file():
                return
        except (OSError, PermissionError):
            pass
        result = self._run(["sqlite3", str(self.db_path), "VACUUM; ANALYZE;"], timeout=15)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "Database optimization failed.")

    def purge_cache(self) -> int:
        """Purge temp files and session files older than 24 hours."""
        purged = 0
        now = time.time()
        for directory in [self.tmp_dir, self.data_dir / "sessions"]:
            if not directory.is_dir():
                continue
            for item in directory.glob("*"):
                try:
                    if item.is_file() and (now - item.stat().st_mtime) > 86400:
                        item.unlink()
                        purged += 1
                except OSError:
                    pass
        return purged

    # ---------------------------------------------------------------
    # Launch Tokens
    # ---------------------------------------------------------------
    @staticmethod
    def _b64encode(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")

    @staticmethod
    def _b64decode(value: str) -> bytes:
        padding = "=" * (-len(value) % 4)
        return base64.urlsafe_b64decode(value + padding)

    @staticmethod
    def _valid_email(email: str) -> bool:
        return bool(
            re.fullmatch(
                r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
                r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
                r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+",
                email,
            )
        )

    def create_launch_token(self, email: str, *, now: int | None = None) -> str:
        email = email.strip().lower()
        if not self._valid_email(email):
            raise ValueError("Invalid mailbox address.")
        try:
            secret = self.secret_path.read_bytes().strip()
        except OSError as exc:
            raise RuntimeError("Roundcube launch secret is unavailable.") from exc
        if len(secret) < 32:
            raise RuntimeError("Roundcube launch secret is invalid.")
        issued = int(time.time() if now is None else now)
        payload = json.dumps(
            {"email": email, "exp": issued + self.launch_ttl_seconds},
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        encoded = self._b64encode(payload)
        signature = hmac.new(secret, encoded.encode("ascii"), hashlib.sha256).digest()
        return f"{encoded}.{self._b64encode(signature)}"

    def purge_data(self) -> None:
        if self.is_installed():
            raise RuntimeError("Uninstall Roundcube PHP service before purging its data.")
        for path in (self.secret_path, self.state_path, self.marker_path):
            try:
                path.unlink()
            except FileNotFoundError:
                pass


roundcube_php_service = RoundcubePhpService()
