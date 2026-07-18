"""
config.py — Application settings loaded from environment / .env file
All service URLs, paths, and secrets are defined here only.
"""
import os
import secrets
from pathlib import Path
from dotenv import load_dotenv

# Load .env from parent directory (the panel install root)
_env_path = Path(__file__).parent.parent / ".env"
load_dotenv(_env_path)


def _env_str(name: str, default: str = "") -> str:
    val = os.getenv(name, default)
    if val is None:
        return default
    return str(val).strip()


def _env_int(name: str, default: int) -> int:
    raw = _env_str(name, str(default))
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    raw = _env_str(name, "true" if default else "false").lower()
    return raw in ("1", "true", "yes", "on")


# ---------------------------------------------------------
# Server
# ---------------------------------------------------------
SERVER_IP: str = _env_str("SERVER_IP", "127.0.0.1")
PANEL_DOMAIN: str = _env_str("PANEL_DOMAIN", "localhost")
DEBUG: bool = _env_bool("DEBUG", False)

# ---------------------------------------------------------
# Auth / sessions
# ---------------------------------------------------------
# install.sh / update.sh / create_admin.sh normally set this.
# If still empty, generate an ephemeral key so the service can start
# (sessions reset on restart until SECRET_KEY is persisted in .env).
SECRET_KEY: str = _env_str("SECRET_KEY", "")
if not SECRET_KEY:
    SECRET_KEY = secrets.token_hex(32)
    _SECRET_KEY_EPHEMERAL = True
else:
    _SECRET_KEY_EPHEMERAL = False

SESSION_HTTPS_ONLY: bool = _env_bool("SESSION_HTTPS_ONLY", False)
SESSION_MAX_AGE: int = _env_int("SESSION_MAX_AGE", 604800)  # 7 days

# ---------------------------------------------------------
# Database
# ---------------------------------------------------------
BASE_DIR = Path(__file__).parent
DB_PATH: Path = Path(os.getenv("DB_PATH", str(BASE_DIR / "panel.db")))
DATABASE_URL: str = f"sqlite+aiosqlite:///{DB_PATH}"

# ---------------------------------------------------------
# PowerDNS
# ---------------------------------------------------------
PDNS_URL: str = os.getenv("PDNS_URL", "http://127.0.0.1:8081")
PDNS_API_KEY: str = os.getenv("PDNS_API_KEY", "")
PDNS_SERVER_ID: str = "localhost"

# ---------------------------------------------------------
# Nginx
# ---------------------------------------------------------
NGINX_SITES_AVAILABLE: str = os.getenv(
    "NGINX_SITES_AVAILABLE", "/etc/nginx/sites-available"
)
NGINX_SITES_ENABLED: str = os.getenv(
    "NGINX_SITES_ENABLED", "/etc/nginx/sites-enabled"
)
NGINX_WEBROOT: str = os.getenv("NGINX_WEBROOT", "/var/www")

# ---------------------------------------------------------
# Certbot
# ---------------------------------------------------------
CERTBOT_EMAIL: str = os.getenv("CERTBOT_EMAIL", "admin@example.com")
LETSENCRYPT_DIR: str = "/etc/letsencrypt/live"

# ---------------------------------------------------------
# Privileges
# ---------------------------------------------------------
# When true and process is not root, shell.py prefixes privileged
# commands with `sudo -n` (install.sh installs /etc/sudoers.d/srv-panel).
PRIVILEGED_SUDO: bool = os.getenv("PRIVILEGED_SUDO", "true").lower() == "true"

# ---------------------------------------------------------
# DNS Record Templates
# ---------------------------------------------------------
DNS_TEMPLATES: dict = {
    "basic_web": {
        "label": "Basic Web (A + www)",
        "records": [
            {"name": "@", "type": "A",     "content": "{server_ip}", "ttl": 3600},
            {"name": "www", "type": "CNAME", "content": "{domain}.", "ttl": 3600},
        ],
    },
    "email_mx": {
        "label": "Email (MX + SPF)",
        "records": [
            {"name": "@", "type": "MX",  "content": "10 mail.{domain}.", "ttl": 3600},
            {"name": "@", "type": "TXT", "content": "v=spf1 mx ~all",    "ttl": 3600},
        ],
    },
    "full": {
        "label": "Full (Web + Email + DMARC)",
        "records": [
            {"name": "@",      "type": "A",     "content": "{server_ip}",              "ttl": 3600},
            {"name": "www",    "type": "CNAME", "content": "{domain}.",                "ttl": 3600},
            {"name": "@",      "type": "MX",    "content": "10 mail.{domain}.",        "ttl": 3600},
            {"name": "@",      "type": "TXT",   "content": "v=spf1 mx ~all",           "ttl": 3600},
            {"name": "_dmarc", "type": "TXT",   "content": "v=DMARC1; p=none;",       "ttl": 3600},
        ],
    },
}
