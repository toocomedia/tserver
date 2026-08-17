"""
templating.py — Shared Jinja2 env + one URL path system for the whole panel.

Rules:
  - App section indexes end with /
  - Detail pages: /section/id (no trailing slash)
  - API paths: /api/... (no trailing slash)
  - Public open URLs (IP / hostname): always trailing /
"""
from __future__ import annotations

from fastapi import Request
from fastapi.templating import Jinja2Templates

from middleware.csrf import ensure_csrf_token

# Canonical app paths (section indexes — trailing slash)
PATHS: dict[str, str] = {
    "home": "/",
    "dashboard": "/",
    "login": "/login",
    "logout": "/logout",
    "domains": "/domains/",
    "domains_create": "/domains/create",
    "proxy": "/proxy/",
    "proxy_create": "/proxy/create",
    "dns": "/dns/",
    "ssl": "/ssl/",
    "ssl_issue": "/ssl/issue",
    "settings": "/settings/",
    "errors": "/admin/errors/",
    "usage": "/usage",
    "health": "/api/health",
    "api_settings": "/api/settings",
    "plugins": "/plugins/",
    "dependencies": "/dependencies",
    "apps": "/apps/",
    "php_sites": "/php-sites/",
    "php_sites_create": "/php-sites/create",
}


def get_plugin_sidebar_items():
    """Jinja helper to list active plugin sidebar links."""
    from plugins import plugin_manager
    return plugin_manager.get_sidebar_items()


def is_plugin_active(plugin_id: str) -> bool:
    """Jinja helper: return True if a plugin is registered, installed, and actively enabled."""
    from plugins import plugin_manager
    plugin = plugin_manager.get_plugin(plugin_id, check_dependencies=False)
    return bool(plugin and plugin.get("effective_status") == "active")


def is_php_active() -> bool:
    """Jinja helper: return True if PHP dependency is installed and healthy."""
    from dependencies import dependency_manager
    return dependency_manager.is_healthy("php")


def app_path(name: str, *parts: str | int, query: str | None = None) -> str:
    """
    Build an internal panel path from a named route.
    app_path("domains") → /domains/
    app_path("domains", 3) → /domains/3
    app_path("dns", "example.com", "records") → /dns/example.com/records
    """
    base = PATHS.get(name)
    if base is None:
        base = name if str(name).startswith("/") else f"/{name}"
    if parts:
        extra = "/".join(str(p).strip("/") for p in parts if p is not None and str(p) != "")
        # Detail: strip trailing slash from section base then append
        root = base.rstrip("/")
        out = f"{root}/{extra}" if extra else base
    else:
        out = base
    if query:
        q = query if query.startswith("?") else f"?{query}"
        out = f"{out}{q}"
    return out


def public_url(
    host: str,
    *,
    https: bool = False,
    port: int | None = None,
) -> str:
    """
    Public open URL (browser). Always ends with /.
    port only added when non-default for the scheme (not 80/http, not 443/https).
    """
    host = (host or "").strip().rstrip("/")
    if not host:
        return "/"
    scheme = "https" if https else "http"
    if port is not None:
        p = int(port)
        if https and p == 443:
            return f"{scheme}://{host}/"
        if not https and p == 80:
            return f"{scheme}://{host}/"
        return f"{scheme}://{host}:{p}/"
    return f"{scheme}://{host}/"


def domain_url(
    domain: Any,
    *,
    https: bool = False,
    port: int | None = None,
) -> str:
    """
    Smart Jinja helper for generating a public URL from a Domain model, dict, or string.
    """
    if domain is None:
        return "/"
    if isinstance(domain, str):
        host = domain
    elif hasattr(domain, "name"):
        host = str(domain.name)
    elif isinstance(domain, dict) and "name" in domain:
        host = str(domain["name"])
    elif isinstance(domain, dict) and "domain" in domain:
        host = str(domain["domain"])
    else:
        host = str(domain)
    return public_url(host, https=https, port=port)


def csrf_token(request: Request) -> str:
    """Jinja helper: {{ csrf_token(request) }} for hidden fields / meta tags."""
    return ensure_csrf_token(request)


from jinja2 import select_autoescape, pass_context
import config
from services.i18n_service import i18n_service

def _extract_lang(context) -> str:
    request = context.get("request")
    return request.state.lang if request and hasattr(request, "state") and hasattr(request.state, "lang") else "en"

@pass_context
def get_lang(context) -> str:
    return _extract_lang(context)

@pass_context
def get_dir(context) -> str:
    return i18n_service.get_direction(_extract_lang(context))

@pass_context
def is_rtl(context) -> bool:
    return i18n_service.is_rtl(_extract_lang(context))

@pass_context
def _translate(context, key: str) -> str:
    return i18n_service.get_string(key, _extract_lang(context))

@pass_context
def _translate_plural(context, key: str, count: int) -> str:
    return i18n_service.get_plural_string(key, count, _extract_lang(context))

@pass_context
def get_js_translations(context) -> str:
    import json
    lang = _extract_lang(context)
    
    # Base dictionary from English
    js_strings = dict(i18n_service.en_strings)
    
    # Override with requested language if available
    target_strings = i18n_service.locales.get(lang, {})
    js_strings.update(target_strings)
        
    return json.dumps(js_strings)

templates = Jinja2Templates(directory="templates")
templates.env.autoescape = select_autoescape(["html", "xml"])
templates.env.globals["path"] = app_path
templates.env.globals["PATHS"] = PATHS
templates.env.globals["public_url"] = public_url
templates.env.globals["domain_url"] = domain_url
templates.env.globals["csrf_token"] = csrf_token
templates.env.globals["get_plugin_sidebar_items"] = get_plugin_sidebar_items
templates.env.globals["is_plugin_active"] = is_plugin_active
templates.env.globals["is_php_active"] = is_php_active
templates.env.globals["PANEL_NAME"] = config.PANEL_NAME
templates.env.globals["PANEL_SHORT_NAME"] = config.PANEL_SHORT_NAME
templates.env.globals["PANEL_LOGO_PATH"] = config.PANEL_LOGO_PATH
templates.env.globals["_"] = _translate
templates.env.globals["n_"] = _translate_plural
templates.env.globals["get_js_translations"] = get_js_translations
templates.env.globals["get_lang"] = get_lang
templates.env.globals["get_dir"] = get_dir
templates.env.globals["is_rtl"] = is_rtl
templates.env.globals["get_available_languages"] = i18n_service.get_available_languages

import os

def get_app_version():
    try:
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        
        # 1. Try checking COMMIT_HASH written by update.sh on VPS
        commit_file = os.path.join(base_dir, "backend", "COMMIT_HASH")
        if not os.path.exists(commit_file):
            commit_file = os.path.join(base_dir, "COMMIT_HASH")
            if not os.path.exists(commit_file):
                commit_file = "/opt/srv-panel/app/COMMIT_HASH"
                
        if os.path.exists(commit_file):
            with open(commit_file, "r") as f:
                sha = f.read().strip()
                if sha and len(sha) >= 7:
                    return sha[:7]
        
        # 2. Try to read Git Hash directly if .git exists (Local development)
        git_dir = os.path.join(base_dir, ".git")
        head_file = os.path.join(git_dir, "HEAD")
        
        if os.path.exists(head_file):
            with open(head_file, "r") as f:
                head_content = f.read().strip()
                
            if head_content.startswith("ref: "):
                ref_path = head_content.split(" ")[1].replace("/", os.sep)
                ref_file = os.path.join(git_dir, ref_path)
                if os.path.exists(ref_file):
                    with open(ref_file, "r") as f:
                        return f.read().strip()[:7]
                
                packed_refs = os.path.join(git_dir, "packed-refs")
                if os.path.exists(packed_refs):
                    with open(packed_refs, "r") as f:
                        for line in f:
                            if line.strip().endswith(head_content.split(" ")[1]):
                                return line.split(" ")[0][:7]
            else:
                return head_content[:7]
    except Exception:
        pass
        
    # 3. Fallback to file modified time
    try:
        return str(int(os.path.getmtime(os.path.abspath(__file__))))
    except Exception:
        import time
        return str(int(time.time()))

templates.env.globals["APP_VERSION"] = get_app_version()

# Aliases for Python imports
path = app_path
