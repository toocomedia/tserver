"""
config.py — Application settings loaded from environment / .env file
All service URLs, paths, and secrets are defined here only.
"""
import os
import secrets
from pathlib import Path
from dotenv import load_dotenv

# Load .env from backend directory and parent directory (the panel install root)
load_dotenv(Path(__file__).parent / ".env")
load_dotenv(Path(__file__).parent.parent / ".env")


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
# Pagination
# ---------------------------------------------------------
DEFAULT_PAGE_LIMIT: int = _env_int("DEFAULT_PAGE_LIMIT", 8)

# ---------------------------------------------------------
# Server
# ---------------------------------------------------------
SERVER_IP: str = _env_str("SERVER_IP", "127.0.0.1")
PANEL_DOMAIN: str = _env_str("PANEL_DOMAIN", "localhost")
PANEL_NAME: str = _env_str("PANEL_NAME", "Barq Panel")
PANEL_SHORT_NAME: str = _env_str("PANEL_SHORT_NAME", "Barq")
PANEL_LOGO_PATH: str = _env_str("PANEL_LOGO_PATH", "/static/images/logo.svg")
DEBUG: bool = _env_bool("DEBUG", False)
TRUSTED_PROXY_IPS: str = _env_str("TRUSTED_PROXY_IPS", "127.0.0.1")

# Public panel access (nginx). Uvicorn stays on 127.0.0.1:PANEL_APP_PORT.
PANEL_APP_PORT: int = _env_int("PANEL_PORT", 8000)
PANEL_ALLOW_IP: bool = _env_bool("PANEL_ALLOW_IP", True)
PANEL_IP_PORT: int = _env_int("PANEL_IP_PORT", 80)
# How panel hostname is chosen: none | custom | subdomain
PANEL_URL_MODE: str = _env_str("PANEL_URL_MODE", "none").lower() or "none"
PANEL_PARENT_DOMAIN: str = _env_str("PANEL_PARENT_DOMAIN", "")
PANEL_SUBDOMAIN_LABEL: str = _env_str("PANEL_SUBDOMAIN_LABEL", "panel")
AUTO_UPDATE_ENABLED: bool = _env_bool("AUTO_UPDATE_ENABLED", False)
PANEL_SSL_AUTO_RENEW_ENABLED: bool = _env_bool("PANEL_SSL_AUTO_RENEW_ENABLED", True)

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

# Login brute-force limits (in-memory; per process). Works with or without nginx.
# Keep SESSION_HTTPS_ONLY false for plain http://IP login.
LOGIN_RATE_LIMIT: str = _env_str("LOGIN_RATE_LIMIT", "5/minute") or "5/minute"
LOGIN_MAX_FAILURES: int = _env_int("LOGIN_MAX_FAILURES", 5)
LOGIN_LOCKOUT_SECONDS: int = _env_int("LOGIN_LOCKOUT_SECONDS", 900)

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
DEFAULT_NS1: str = os.getenv("DEFAULT_NS1", "")
DEFAULT_NS2: str = os.getenv("DEFAULT_NS2", "")
DEFAULT_NS3: str = os.getenv("DEFAULT_NS3", "")
DEFAULT_NS_MODE: str = os.getenv("DEFAULT_NS_MODE", "panel_default")

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
APP_HOSTING_ROOT: str = os.getenv("APP_HOSTING_ROOT", "/var/lib/srv-panel/apps")
APP_HOSTING_ENV_ROOT: str = os.getenv("APP_HOSTING_ENV_ROOT", "/var/lib/srv-panel/app-env")
APP_ERROR_PAGES_ROOT: str = os.getenv("APP_ERROR_PAGES_ROOT", str(Path(NGINX_WEBROOT) / "srv-error-pages"))
APP_HOSTING_USER: str = os.getenv("APP_HOSTING_USER", "panel")
APP_HOSTING_PORT_START: int = _env_int("APP_HOSTING_PORT_START", 9100)
CONTAINER_APP_ROOT: str = os.getenv("CONTAINER_APP_ROOT", "/var/lib/srv-panel/container-apps")
PHP_SITE_STATE_ROOT: str = os.getenv("PHP_SITE_STATE_ROOT", "/var/lib/srv-panel/php-sites")
PHP_SITE_LOG_ROOT: str = os.getenv("PHP_SITE_LOG_ROOT", "/var/log/srv-panel/php-sites")
CONTAINER_APP_ENV_ROOT: str = os.getenv("CONTAINER_APP_ENV_ROOT", "/var/lib/srv-panel/container-app-env")
CONTAINER_APP_PORT_START: int = _env_int("CONTAINER_APP_PORT_START", 31000)
CONTAINER_APP_BUILD_TIMEOUT: int = _env_int("CONTAINER_APP_BUILD_TIMEOUT", 1200)
CONTAINER_APP_BACKUP_ROOT: str = os.getenv("CONTAINER_APP_BACKUP_ROOT", "/var/lib/srv-panel/container-app-backups")
DEPLOY_KEY_ROOT: str = os.getenv("DEPLOY_KEY_ROOT", "/var/lib/srv-panel/deploy-keys")
KNOWN_HOSTS_PATH: str = os.getenv("KNOWN_HOSTS_PATH", "/var/lib/srv-panel/known_hosts")
BUILDX_BUILDER_NAME: str = _env_str("BUILDX_BUILDER_NAME", "srv-panel-builder")
FILE_MANAGER_MAX_TEXT_BYTES: int = _env_int("FILE_MANAGER_MAX_TEXT_BYTES", 2 * 1024 * 1024)
FILE_MANAGER_MAX_TRANSFER_BYTES: int = _env_int("FILE_MANAGER_MAX_TRANSFER_BYTES", 100 * 1024 * 1024)
FILE_MANAGER_MAX_ENTRIES: int = _env_int("FILE_MANAGER_MAX_ENTRIES", 500)
GUARD_PROTECTED_RESERVE_MB: int = _env_int("GUARD_PROTECTED_RESERVE_MB", 400)

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
