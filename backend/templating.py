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
}


def get_plugin_sidebar_items():
    """Jinja helper to list active plugin sidebar links."""
    from plugins import plugin_manager
    return plugin_manager.get_sidebar_items()


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


def csrf_token(request: Request) -> str:
    """Jinja helper: {{ csrf_token(request) }} for hidden fields / meta tags."""
    return ensure_csrf_token(request)


from jinja2 import select_autoescape
import config

templates = Jinja2Templates(directory="templates")
templates.env.autoescape = select_autoescape(["html", "xml"])
templates.env.globals["path"] = app_path
templates.env.globals["PATHS"] = PATHS
templates.env.globals["public_url"] = public_url
templates.env.globals["csrf_token"] = csrf_token
templates.env.globals["get_plugin_sidebar_items"] = get_plugin_sidebar_items
templates.env.globals["PANEL_NAME"] = config.PANEL_NAME
templates.env.globals["PANEL_SHORT_NAME"] = config.PANEL_SHORT_NAME
templates.env.globals["PANEL_LOGO_PATH"] = config.PANEL_LOGO_PATH

import os

def get_app_version():
    try:
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
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
        
    try:
        return str(int(os.path.getmtime(os.path.abspath(__file__))))
    except Exception:
        import time
        return str(int(time.time()))

templates.env.globals["APP_VERSION"] = get_app_version()

# Aliases for Python imports
path = app_path
