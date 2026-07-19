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

# Public panel access (nginx). Uvicorn stays on 127.0.0.1:PANEL_APP_PORT.
PANEL_APP_PORT: int = _env_int("PANEL_PORT", 8000)
PANEL_ALLOW_IP: bool = _env_bool("PANEL_ALLOW_IP", True)
PANEL_IP_PORT: int = _env_int("PANEL_IP_PORT", 80)
# How panel hostname is chosen: none | custom | subdomain
PANEL_URL_MODE: str = _env_str("PANEL_URL_MODE", "none").lower() or "none"
PANEL_PARENT_DOMAIN: str = _env_str("PANEL_PARENT_DOMAIN", "")
PANEL_SUBDOMAIN_LABEL: str = _env_str("PANEL_SUBDOMAIN_LABEL", "panel")

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
# Security (panel browser hardening)
# ---------------------------------------------------------
SECURITY_HEADERS: bool = _env_bool("SECURITY_HEADERS", True)
HSTS_ENABLED: bool = _env_bool("HSTS_ENABLED", False)

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
NGINX_CACHE_DIR: str = os.getenv("NGINX_CACHE_DIR", "/var/cache/nginx")

# ---------------------------------------------------------
# Performance (nginx optimizations)
# ---------------------------------------------------------
NGINX_PERF_GZIP: bool = _env_bool("NGINX_PERF_GZIP", False)
NGINX_PERF_STATIC_CACHE: bool = _env_bool("NGINX_PERF_STATIC_CACHE", False)

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
# content may be a string or list[str] (multi-value RRset, e.g. two NS).
DNS_TEMPLATES: dict = {
    "basic_web": {
        "label": "Basic Web (A + www)",
        "records": [
            {"name": "@", "type": "A", "content": "{server_ip}", "ttl": 3600},
            {"name": "www", "type": "CNAME", "content": "{domain}.", "ttl": 3600},
        ],
    },
    "child_ns": {
        "label": "Child NS (ns1 + ns2)",
        "records": [
            {"name": "ns1", "type": "A", "content": "{server_ip}", "ttl": 3600},
            {"name": "ns2", "type": "A", "content": "{server_ip}", "ttl": 3600},
            {
                "name": "@",
                "type": "NS",
                "content": ["ns1.{domain}.", "ns2.{domain}."],
                "ttl": 3600,
            },
        ],
    },
}
