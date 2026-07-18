"""
services/nginx_service.py — Nginx config file management and reload.
Generates, writes, enables, disables, and removes nginx site configs.
All config tests run before any reload.
"""
import logging
from pathlib import Path

import config
from utils import shell
from utils import nginx_templates

logger = logging.getLogger(__name__)


def _available_path(name: str) -> Path:
    return Path(config.NGINX_SITES_AVAILABLE) / name


def _enabled_path(name: str) -> Path:
    return Path(config.NGINX_SITES_ENABLED) / name


def _conf_name(domain: str) -> str:
    """Config file name for a domain, e.g. example.com.conf"""
    return f"{domain}.conf"


def _webroot_path(domain: str) -> str:
    return str(Path(config.NGINX_WEBROOT) / domain / "public")


def _acme_root() -> str:
    return str(Path(config.NGINX_WEBROOT) / "acme-challenge")


# ---------------------------------------------------------------
# WEBROOT MANAGEMENT
# ---------------------------------------------------------------
def ensure_acme_root() -> None:
    """Create the shared acme-challenge webroot if not present."""
    path = Path(_acme_root())
    path.mkdir(parents=True, exist_ok=True)
    logger.info("Acme root ensured: %s", path)


def create_webroot(domain: str, html_content: str | None = None) -> str:
    """
    Create /var/www/domain/public/ and write default index.html.
    Returns the webroot path.
    """
    webroot = Path(_webroot_path(domain))
    webroot.mkdir(parents=True, exist_ok=True)

    index = webroot / "index.html"
    content = html_content or nginx_templates.default_index_html(domain)
    index.write_text(content, encoding="utf-8")

    logger.info("Webroot created: %s", webroot)
    return str(webroot)


def read_index_html(domain: str) -> str:
    """Read the current index.html for a domain."""
    index = Path(_webroot_path(domain)) / "index.html"
    if index.exists():
        return index.read_text(encoding="utf-8")
    return nginx_templates.default_index_html(domain)


def write_index_html(domain: str, content: str) -> None:
    """Overwrite index.html for a domain."""
    index = Path(_webroot_path(domain)) / "index.html"
    index.write_text(content, encoding="utf-8")
    logger.info("index.html updated: %s", domain)


def remove_webroot(domain: str) -> None:
    """Remove the webroot directory for a domain."""
    import shutil
    webroot = Path(config.NGINX_WEBROOT) / domain
    if webroot.exists():
        shutil.rmtree(webroot)
        logger.info("Webroot removed: %s", webroot)


# ---------------------------------------------------------------
# CONFIG MANAGEMENT
# ---------------------------------------------------------------
async def _write_config(name: str, content: str) -> Path:
    """Write config to sites-available and symlink to sites-enabled."""
    avail = _available_path(name)
    enabled = _enabled_path(name)

    await shell.write_file(avail, content)
    await shell.symlink(avail, enabled)
    logger.info("Nginx config written and enabled: %s", name)
    return avail


async def _remove_config(name: str) -> None:
    """Remove config from sites-available and sites-enabled."""
    await shell.remove_path(_enabled_path(name))
    await shell.remove_path(_available_path(name))
    logger.info("Nginx config removed: %s", name)


async def create_static_site(domain: str) -> str:
    """
    Write HTTP static site nginx config.
    Returns path to config file. Raises on nginx -t failure.
    """
    webroot = _webroot_path(domain)
    name = _conf_name(domain)
    content = nginx_templates.static_site_config(domain, webroot)
    config_path = str(await _write_config(name, content))

    result = await shell.nginx_test()
    if not result.success:
        await _remove_config(name)
        raise ValueError(f"Nginx config test failed: {result.stderr}")

    return config_path


async def update_static_site_ssl(
    domain: str, cert_path: str, key_path: str
) -> str:
    """
    Replace HTTP config with HTTP+HTTPS config after SSL cert is issued.
    Returns new config path.
    """
    webroot = _webroot_path(domain)
    name = _conf_name(domain)
    content = nginx_templates.static_site_ssl_config(domain, webroot, cert_path, key_path)
    config_path = str(await _write_config(name, content))

    result = await shell.nginx_test()
    if not result.success:
        http_content = nginx_templates.static_site_config(domain, webroot)
        await _write_config(name, http_content)
        raise ValueError(f"Nginx SSL config test failed: {result.stderr}")

    return config_path


async def create_proxy(
    full_domain: str, target_ip: str, target_port: int, protocol: str
) -> str:
    """Write HTTP reverse proxy nginx config. Returns config path."""
    name = _conf_name(full_domain)
    content = nginx_templates.reverse_proxy_config(
        full_domain, target_ip, target_port, protocol
    )
    config_path = str(await _write_config(name, content))

    result = await shell.nginx_test()
    if not result.success:
        await _remove_config(name)
        raise ValueError(f"Nginx config test failed: {result.stderr}")

    return config_path


async def update_proxy_ssl(
    full_domain: str, target_ip: str, target_port: int,
    protocol: str, cert_path: str, key_path: str
) -> str:
    """Replace proxy HTTP config with SSL config."""
    name = _conf_name(full_domain)
    content = nginx_templates.reverse_proxy_ssl_config(
        full_domain, target_ip, target_port, protocol, cert_path, key_path
    )
    config_path = str(await _write_config(name, content))

    result = await shell.nginx_test()
    if not result.success:
        raise ValueError(f"Nginx SSL config test failed: {result.stderr}")

    return config_path


async def remove_site(domain: str) -> None:
    """Remove nginx config for a domain."""
    await _remove_config(_conf_name(domain))


async def reload() -> None:
    """Reload nginx. Raises on failure."""
    result = await shell.nginx_reload()
    if not result.success:
        raise ValueError(f"Nginx reload failed: {result.stderr}")


def config_exists(domain: str) -> bool:
    """Return True if nginx config exists for this domain."""
    return _enabled_path(_conf_name(domain)).exists()


def server_name_in_use(domain: str) -> bool:
    """
    Scan all enabled nginx configs for an existing server_name matching domain.
    Prevents duplicate server_name conflicts.
    """
    enabled_dir = Path(config.NGINX_SITES_ENABLED)
    if not enabled_dir.exists():
        return False
    needle = f"server_name {domain}"
    for conf in enabled_dir.iterdir():
        try:
            text = conf.read_text(encoding="utf-8", errors="ignore")
            if needle in text:
                return True
        except OSError:
            continue
    return False
