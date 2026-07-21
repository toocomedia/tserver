"""
main.py — FastAPI application entry point
Mounts routers, static files, and templates. Initializes DB on startup.
No routes defined here — all routes live in routers/.
"""
import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi.errors import RateLimitExceeded
from starlette.middleware.sessions import SessionMiddleware
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

import config
from database import init_db
from routers import system, domains, dns, ssl, proxy, errors, auth, settings, dev
from middleware.error_capture import RequestIdMiddleware, register_error_handlers
from middleware.auth import AuthMiddleware
from middleware.csrf import CSRFMiddleware
from middleware.limiter import limiter
from middleware.security_headers import SecurityHeadersMiddleware
from services import login_guard
from templating import templates

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def _auto_purge_loop() -> None:
    """Background task: check and clear expired proxy caches every hour."""
    from database import AsyncSessionLocal
    from services import cache_service

    while True:
        await asyncio.sleep(3600)  # run every hour
        try:
            async with AsyncSessionLocal() as db:
                count = await cache_service.run_auto_purge_all(db)
                if count:
                    logger.info("Auto-purge: %d proxy cache(s) cleared", count)
        except Exception as exc:
            logger.warning("Auto-purge loop error: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize DB tables on startup and start background tasks."""
    if getattr(config, "_SECRET_KEY_EPHEMERAL", False):
        logger.warning(
            "SECRET_KEY was missing — using ephemeral key. "
            "Add SECRET_KEY to .env (or re-run update/create_admin) "
            "so login sessions survive restarts."
        )
    logger.info("Initializing database...")
    await init_db()
    purge_task = asyncio.create_task(_auto_purge_loop())
    logger.info("Panel ready.")
    yield
    purge_task.cancel()
    logger.info("Panel shutting down.")


async def _rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded):
    """HTML login page on /login; JSON elsewhere. Form stays disabled when locked."""
    detail = login_guard.LOCKOUT_MESSAGE
    path = request.url.path
    if path == "/login" or path.rstrip("/") == "/login":
        return templates.TemplateResponse(
            "pages/auth/login.html",
            {
                "request": request,
                "error": detail,
                "username": "",
                "next": "/",
                "locked": True,
            },
            status_code=429,
        )
    return JSONResponse({"detail": detail}, status_code=429)


app = FastAPI(
    title="VPS Control Panel",
    version="1.0.0",
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
)

# slowapi: in-memory rate limits (login). No Redis.
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Middleware order: last added runs first on the request.
# ProxyHeaders → Session → SecurityHeaders → RequestId → CSRF → Auth → app
# ProxyHeaders: honor X-Forwarded-* so redirects keep :8080 when behind nginx
app.add_middleware(AuthMiddleware)
app.add_middleware(CSRFMiddleware)
app.add_middleware(RequestIdMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(
    SessionMiddleware,
    secret_key=config.SECRET_KEY,
    session_cookie="srv_panel_session",
    max_age=config.SESSION_MAX_AGE,
    same_site="lax",
    # Never force Secure cookies here if SESSION_HTTPS_ONLY was flipped by mistake —
    # that locks HTTP IP login. Prefer explicit HTTPS only after panel SSL works.
    https_only=bool(config.SESSION_HTTPS_ONLY),
)
trusted_proxies = [ip.strip() for ip in config.TRUSTED_PROXY_IPS.split(",") if ip.strip()]
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=trusted_proxies or "127.0.0.1")
register_error_handlers(app)

# Static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# Routers
app.include_router(auth.router)
app.include_router(system.router)
app.include_router(settings.router)
app.include_router(domains.router)   # Phase 2
app.include_router(dns.router)       # Phase 3
app.include_router(ssl.router)       # Phase 4
app.include_router(proxy.router)     # Phase 5
app.include_router(errors.router)    # Phase 6
if getattr(config, "DEBUG", False):
    app.include_router(dev.router)       # Testing tools (DEBUG mode only)
