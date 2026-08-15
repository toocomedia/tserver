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

# Headers forwarded verbatim to phpMyAdmin.
_PASS_REQUEST_HEADERS = frozenset(
    {
        "accept",
        "accept-language",
        "content-type",
        "cookie",
        "origin",
        "referer",
        "user-agent",
        "x-requested-with",
    }
)
_PASS_RESPONSE_HEADERS = frozenset(
    {
        "content-type",
        "content-disposition",
        "content-language",
        "cache-control",
        "expires",
        "last-modified",
        "etag",
        "location",
        "set-cookie",
        "www-authenticate",
        "x-powered-by",
    }
)


@control_router.get("/", response_class=HTMLResponse)
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
    "/{path:path}",
    methods=["GET", "POST", "HEAD", "PUT", "PATCH", "DELETE"],
    include_in_schema=False,
)
async def proxy_phpmyadmin(request: Request, path: str):
    """Stream requests to the local phpMyAdmin server, keeping panel auth."""
    if not phpmyadmin_service.is_installed():
        return RedirectResponse("/plugins/phpmyadmin/", status_code=303)

    upstream_path = f"/{path}" if path else "/"
    url = f"{phpmyadmin_service.base_url}{upstream_path}"
    if request.url.query:
        url = f"{url}?{request.url.query}"

    headers = {
        key: value
        for key, value in request.headers.items()
        if key.lower() in _PASS_REQUEST_HEADERS
    }
    # phpMyAdmin detects HTTPS from X-Forwarded-Proto; pass the panel's real
    # scheme so cookie security and the login https-mismatch check match the
    # browser's connection (http://IP or https://domain both work).
    headers["x-forwarded-proto"] = request.url.scheme
    body = await request.body()

    client = httpx.AsyncClient(timeout=None)
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
            {"detail": "phpMyAdmin is not running. Install it from the plugin page."},
            status_code=502,
        )
    finally:
        await client.aclose()

    response_headers = [
        (name, value)
        for name, value in upstream.headers.items()
        if name.lower() in _PASS_RESPONSE_HEADERS
    ]
    # phpMyAdmin redirects to absolute paths; keep them inside /phpmyadmin.
    for index, (name, value) in enumerate(response_headers):
        if name.lower() == "location" and value.startswith("/"):
            value = (
                f"/phpmyadmin{value}"
                if not value.startswith("/phpmyadmin")
                else value
            )
            response_headers[index] = (name, value)

    return Response(
        content=content,
        status_code=upstream.status_code,
        headers=dict(response_headers),
    )


# The manager mounts `router`; it carries both the control page (under
# /plugins/phpmyadmin) and the app proxy (under /phpmyadmin).
router = APIRouter()
router.include_router(control_router)
router.include_router(app_router)
