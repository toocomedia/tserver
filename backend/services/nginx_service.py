"""
services/nginx_service.py — Nginx config file management and reload.
Generates, writes, enables, disables, and removes nginx site configs.
All config tests run before any reload.
"""
import logging
import re
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
    """Create the shared acme-challenge webroot if not present (best-effort sync)."""
    path = Path(_acme_root())
    try:
        path.mkdir(parents=True, exist_ok=True)
        (path / ".well-known" / "acme-challenge").mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.warning("ensure_acme_root mkdir failed (will try privileged later): %s", exc)
    logger.info("Acme root ensured: %s", path)


async def ensure_acme_root_privileged() -> str:
    """
    Ensure ACME webroot exists with permissions certbot + nginx can use.
    Returns the webroot path used with certbot --webroot-path.
    """
    root = _acme_root()
    challenge = str(Path(root) / ".well-known" / "acme-challenge")
    r = await shell.run(["mkdir", "-p", challenge], timeout=15)
    if not r.success:
        logger.warning("privileged acme mkdir: %s", r.stderr)
    # World-readable so nginx worker can serve challenge files
    await shell.run(["chmod", "-R", "a+rX", root], timeout=10)
    return root


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


def server_name_in_use(domain: str, *, ignore_names: set[str] | None = None) -> bool:
    """
    Scan all enabled nginx configs for an existing server_name matching domain.
    Prevents duplicate server_name conflicts.
    ignore_names: config basenames to skip (e.g. {"panel"} when updating panel).
    """
    enabled_dir = Path(config.NGINX_SITES_ENABLED)
    if not enabled_dir.exists():
        return False
    ignore = {n.lower() for n in (ignore_names or set())}
    domain = domain.strip().lower().rstrip(".")
    # Word-boundary style: server_name tokens separated by spaces / ending with ;
    token_re = re.compile(
        rf"server_name\s+([^;]+);",
        re.IGNORECASE,
    )
    for conf in enabled_dir.iterdir():
        base = conf.name.lower()
        if base in ignore or base.replace(".conf", "") in ignore:
            continue
        try:
            text = conf.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for m in token_re.finditer(text):
            names = m.group(1).split()
            for name in names:
                n = name.strip().lower().rstrip(";")
                if n == domain or n == f"www.{domain}":
                    return True
    return False


# ---------------------------------------------------------------
# PANEL SITE (special config name: "panel")
# ---------------------------------------------------------------
PANEL_SITE_NAME = "panel"


async def apply_panel_config(
    content: str,
    *,
    previous_content: str | None = None,
) -> str:
    """
    Write /etc/nginx/sites-available/panel, enable it, nginx -t, reload.
    On test failure restores previous_content when provided.
    """
    name = PANEL_SITE_NAME
    avail = _available_path(name)
    old = previous_content
    if old is None and avail.exists():
        try:
            old = avail.read_text(encoding="utf-8", errors="replace")
        except OSError:
            old = None

    config_path = str(await _write_config(name, content))
    result = await shell.nginx_test()
    if not result.success:
        if old is not None:
            await _write_config(name, old)
            await shell.nginx_test()
        raise ValueError(f"Nginx config test failed: {result.stderr}")

    await reload()
    return config_path


def read_panel_config() -> str | None:
    """Return current panel nginx config text, or None."""
    path = _available_path(PANEL_SITE_NAME)
    if not path.exists():
        return None
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def panel_config_has_ssl() -> bool:
    text = read_panel_config() or ""
    return "listen 443" in text and "ssl_certificate" in text
