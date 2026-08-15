"""phpMyAdmin: control page under /plugins/phpmyadmin, app proxy under /phpmyadmin."""
from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from plugins.phpmyadmin.service import phpmyadmin_service
from templating import templates

logger = logging.getLogger(__name__)

# Control page (matches the panel's /plugins/<id>/ convention).
control_router = APIRouter(prefix="/plugins/phpmyadmin", tags=["phpmyadmin"])
# phpMyAdmin app itself, served same-origin behind panel auth.
app_router = APIRouter(prefix="/phpmyadmin", tags=["phpmyadmin_app"])

import re

_HOP_BY_HOP_HEADERS = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
        "host",
    }
)

_proxy_client: httpx.AsyncClient | None = None


def _get_proxy_client() -> httpx.AsyncClient:
    global _proxy_client
    if _proxy_client is None or _proxy_client.is_closed:
        _proxy_client = httpx.AsyncClient(
            limits=httpx.Limits(
                max_keepalive_connections=50,
                max_connections=100,
                keepalive_expiry=30.0,
            ),
            timeout=httpx.Timeout(120.0, connect=10.0),
            follow_redirects=False,
        )
    return _proxy_client


@control_router.get("/", response_class=HTMLResponse)
@control_router.get("", response_class=HTMLResponse)
async def index(request: Request):
    from plugins.manager import plugin_manager

    plugin = plugin_manager.plugins.get("phpmyadmin")
    return templates.TemplateResponse(
        "phpmyadmin.html",
        {
            "request": request,
            "active_page": "plugins",
            "plugin_version": (plugin or {}).get("version", "1.0.0"),
            "status": phpmyadmin_service.get_status(),
        },
    )


@control_router.post("/api/install")
async def install_phpmyadmin(request: Request):
    from plugins.manager import plugin_manager

    success, message = await plugin_manager.run_plugin_script(
        "phpmyadmin", "install"
    )
    return JSONResponse(
        {"status": "ok" if success else "error", "message": message},
        status_code=200 if success else 500,
    )


@control_router.post("/api/start")
async def start_phpmyadmin(request: Request):
    try:
        phpmyadmin_service.resume()
    except RuntimeError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=500)
    return JSONResponse(
        {
            "status": "ok",
            "message": "phpMyAdmin server started.",
            "server": phpmyadmin_service.get_status(),
        }
    )


@control_router.post("/api/restart")
async def restart_phpmyadmin(request: Request):
    try:
        if phpmyadmin_service.is_installed():
            phpmyadmin_service.resume()
    except RuntimeError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=500)
    return JSONResponse(
        {
            "status": "ok",
            "message": "phpMyAdmin server restarted.",
            "server": phpmyadmin_service.get_status(),
        }
    )


@control_router.get("/api/status")
async def status_phpmyadmin(request: Request):
    return JSONResponse(phpmyadmin_service.get_status())


@control_router.post("/api/uninstall")
async def uninstall_phpmyadmin(request: Request):
    from plugins.manager import plugin_manager

    success, message = await plugin_manager.run_plugin_script(
        "phpmyadmin", "uninstall"
    )
    return JSONResponse(
        {"status": "ok" if success else "error", "message": message},
        status_code=200 if success else 500,
    )


@app_router.api_route(
    "",
    methods=["GET", "POST", "HEAD", "PUT", "PATCH", "DELETE"],
    include_in_schema=False,
)
@app_router.api_route(
    "/{path:path}",
    methods=["GET", "POST", "HEAD", "PUT", "PATCH", "DELETE"],
    include_in_schema=False,
)
async def proxy_phpmyadmin(request: Request, path: str = ""):
    """Stream requests to the local phpMyAdmin server, keeping panel auth."""
    if not phpmyadmin_service.is_installed():
        return RedirectResponse("/plugins/phpmyadmin/", status_code=303)

    if not path and not request.url.path.endswith("/"):
        q = f"?{request.url.query}" if request.url.query else ""
        return RedirectResponse(f"/phpmyadmin/{q}", status_code=307)

    upstream_path = f"/{path}" if path else "/"
    url = f"{phpmyadmin_service.base_url}{upstream_path}"
    if request.url.query:
        url = f"{url}?{request.url.query}"

    headers = {
        key.lower(): value
        for key, value in request.headers.items()
        if key.lower() not in _HOP_BY_HOP_HEADERS
    }
    headers["host"] = f"{phpmyadmin_service.host}:{phpmyadmin_service.port}"
    headers["x-forwarded-proto"] = request.url.scheme
    headers["x-forwarded-host"] = request.headers.get("host", request.url.netloc)
    headers["x-forwarded-prefix"] = "/phpmyadmin"
    if request.client:
        headers["x-forwarded-for"] = request.client.host

    body = await request.body()
    client = _get_proxy_client()
    try:
        upstream = await client.request(
            request.method,
            url,
            headers=headers,
            content=body or None,
        )
        content = upstream.content
    except httpx.HTTPError as exc:
        logger.warning("phpMyAdmin proxy failed: %s", exc)
        return JSONResponse(
            {"detail": "phpMyAdmin is not running. Start or install it from the plugin page."},
            status_code=502,
        )

    response_headers: list[tuple[str, str]] = []
    for name, value in upstream.headers.multi_items():
        name_lower = name.lower()
        if name_lower in _HOP_BY_HOP_HEADERS:
            continue
        if name_lower == "location":
            match = re.match(
                r"^https?://(?:127\.0\.0\.1|localhost)(?::\d+)?(/.*)?$",
                value,
                re.IGNORECASE,
            )
            if match:
                path_part = match.group(1) or "/"
                value = (
                    f"/phpmyadmin{path_part}"
                    if not path_part.startswith("/phpmyadmin")
                    else path_part
                )
            elif value.startswith("/"):
                value = (
                    f"/phpmyadmin{value}"
                    if not value.startswith("/phpmyadmin")
                    else value
                )
            elif not value.startswith("http://") and not value.startswith("https://"):
                value = f"/phpmyadmin/{value.lstrip('/')}"
        response_headers.append((name, value))

    response = Response(
        content=content,
        status_code=upstream.status_code,
    )
    # Set raw_headers to preserve duplicate headers such as multiple Set-Cookie entries
    response.raw_headers = [
        (name.lower().encode("latin-1"), value.encode("latin-1"))
        for name, value in response_headers
    ]
    return response


# The manager mounts `router`; it carries both the control page (under
# /plugins/phpmyadmin) and the app proxy (under /phpmyadmin).
router = APIRouter()
router.include_router(control_router)
router.include_router(app_router)
