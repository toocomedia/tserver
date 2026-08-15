"""Dedicated Roundcube Configuration Manager for PHP Webmail."""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class RoundcubeConfigManager:
    """Handles reading, writing, and synchronizing Roundcube PHP configuration."""

    def __init__(self, data_dir: Path | None = None) -> None:
        self._custom_data_dir = data_dir

    @property
    def data_dir(self) -> Path:
        if self._custom_data_dir:
            return self._custom_data_dir
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
    def config_dir(self) -> Path:
        return self.htdocs / "config"

    @property
    def config_file(self) -> Path:
        return self.config_dir / "config.inc.php"

    @property
    def state_file(self) -> Path:
        return self.data_dir / "state.json"

    @property
    def db_file(self) -> Path:
        return self.data_dir / "db" / "roundcube.db"

    @staticmethod
    def default_settings() -> dict[str, Any]:
        return {
            "skin": "elastic",
            "product_name": "SRV Webmail",
            "max_message_size": "32M",
            "session_lifetime": 30,
            "plugins": ["archive", "zipdownload", "markasjunk", "srvpanel_launch"],
        }

    def _maybe_sudo(self, args: list[str]) -> list[str]:
        try:
            from utils.shell import _maybe_sudo
            return _maybe_sudo(args)
        except Exception:
            if hasattr(os, "geteuid") and os.geteuid() != 0:
                return ["sudo", "-n", *args]
            return args

    def read_config_file(self) -> dict[str, Any]:
        """Parses the active config.inc.php file on disk if it exists."""
        if not self.config_file.is_file():
            return {}
        try:
            content = self.config_file.read_text(encoding="utf-8")
        except Exception as exc:
            logger.warning("Could not read %s: %s", self.config_file, exc)
            return {}

        parsed: dict[str, Any] = {}
        m = re.search(r"\$config\['skin'\]\s*=\s*'([^']+)'", content)
        if m:
            parsed["skin"] = m.group(1)

        m = re.search(r"\$config\['product_name'\]\s*=\s*'((?:\\'|[^'])*)'", content)
        if m:
            parsed["product_name"] = m.group(1).replace("\\'", "'")

        m = re.search(r"\$config\['max_message_size'\]\s*=\s*'([^']+)'", content)
        if m:
            parsed["max_message_size"] = m.group(1)

        m = re.search(r"\$config\['session_lifetime'\]\s*=\s*(\d+)", content)
        if m:
            parsed["session_lifetime"] = int(m.group(1))

        m = re.search(r"\$config\['plugins'\]\s*=\s*\[(.*?)\];", content, re.DOTALL)
        if m:
            plugins = re.findall(r"'([^']+)'", m.group(1))
            if plugins:
                parsed["plugins"] = plugins
        return parsed

    def read_state_settings(self) -> dict[str, Any]:
        """Reads settings from state.json if present."""
        if not self.state_file.is_file():
            return {}
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("settings"), dict):
                return data["settings"]
        except Exception as exc:
            logger.warning("Could not read state.json settings: %s", exc)
        return {}

    def get_settings(self) -> dict[str, Any]:
        """Returns the current merged configuration settings."""
        settings = self.default_settings()
        settings.update(self.read_config_file())
        settings.update(self.read_state_settings())
        return settings

    def generate_config_php(self, settings: dict[str, Any]) -> str:
        """Generates the full config.inc.php content from settings dictionary."""
        skin = str(settings.get("skin") or "elastic").strip().lower()
        # Fallback to elastic if skin directory is missing
        skin_dir = self.htdocs / "skins" / skin
        if not skin_dir.is_dir() and (self.htdocs / "skins" / "elastic").is_dir():
            skin = "elastic"

        raw_title = str(settings.get("product_name") or "SRV Webmail").strip()
        product_name = raw_title.replace("'", "\\'")
        max_message_size = str(settings.get("max_message_size") or "32M").strip()
        session_lifetime = int(settings.get("session_lifetime") or 30)

        raw_plugins = settings.get("plugins") or ["archive", "zipdownload", "markasjunk", "srvpanel_launch"]
        plugins_list = list(raw_plugins) if isinstance(raw_plugins, list) else ["archive", "zipdownload", "markasjunk", "srvpanel_launch"]
        if "srvpanel_launch" not in plugins_list:
            plugins_list.append("srvpanel_launch")
        plugins_repr = ", ".join(f"'{p}'" for p in plugins_list)

        return f"""<?php

// Database connection — SQLite for lightweight native execution
$db_path = '{self.db_file}';
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

    def write_config_file(self, content: str) -> bool:
        """Writes content to config.inc.php with proper permissions and sudo tee fallback."""
        self.config_dir.mkdir(parents=True, exist_ok=True)
        data = content.encode("utf-8")
        try:
            self.config_file.write_bytes(data)
            try:
                self.config_file.chmod(0o664)
            except OSError:
                pass
            return True
        except (OSError, PermissionError):
            pass

        if os.name != "nt":
            try:
                args = self._maybe_sudo(["tee", str(self.config_file)])
                subprocess.run(
                    args,
                    input=data,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    timeout=10,
                    check=True,
                )
                subprocess.run(self._maybe_sudo(["chmod", "0664", str(self.config_file)]), check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                subprocess.run(self._maybe_sudo(["chown", "www-data:www-data", str(self.config_file)]), check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return True
            except Exception as exc:
                logger.error("Failed to write %s via sudo tee: %s", self.config_file, exc)
        return False

    def write_state(self, settings: dict[str, Any]) -> None:
        """Persists settings into state.json."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        current_state: dict[str, Any] = {}
        if self.state_file.is_file():
            try:
                current_state = json.loads(self.state_file.read_text(encoding="utf-8"))
            except Exception:
                current_state = {}
        if not isinstance(current_state, dict):
            current_state = {}

        current_state["settings"] = settings
        data = json.dumps(current_state, indent=2).encode("utf-8")
        try:
            self.state_file.write_bytes(data)
            try:
                self.state_file.chmod(0o664)
            except OSError:
                pass
            return
        except (OSError, PermissionError):
            pass

        if os.name != "nt":
            try:
                args = self._maybe_sudo(["tee", str(self.state_file)])
                subprocess.run(args, input=data, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=10, check=True)
                subprocess.run(self._maybe_sudo(["chmod", "0664", str(self.state_file)]), check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception as exc:
                logger.error("Failed to write %s via sudo tee: %s", self.state_file, exc)

    def sync_db_skin(self, new_skin: str) -> None:
        """Updates user preferences in SQLite DB so existing users get the new skin."""
        if not self.db_file.is_file():
            return
        try:
            con = sqlite3.connect(str(self.db_file))
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
            logger.warning("Could not update skin in SQLite DB: %s", exc)

    def restart_service(self) -> None:
        """Restarts the systemd service so PHP workers reload the new config."""
        if os.name != "nt":
            try:
                args = self._maybe_sudo(["systemctl", "restart", "srv-panel-roundcube-php"])
                subprocess.run(
                    args,
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=10,
                )
            except Exception as exc:
                logger.warning("Could not restart srv-panel-roundcube-php: %s", exc)

    def save_settings(self, **new_settings: Any) -> dict[str, Any]:
        """Main entrypoint to update, write, sync DB, and restart service."""
        settings = self.get_settings()
        settings.update(new_settings)

        # 1. Write state.json
        self.write_state(settings)

        # 2. Write config.inc.php (using direct write or sudo tee)
        php_code = self.generate_config_php(settings)
        self.write_config_file(php_code)

        # 3. Update DB preferences if skin changed
        if "skin" in new_settings:
            self.sync_db_skin(str(new_settings["skin"]))

        # 4. Restart service
        self.restart_service()

        return settings


roundcube_config_manager = RoundcubeConfigManager()
