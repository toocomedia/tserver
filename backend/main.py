"""
main.py — FastAPI application entry point
Mounts routers, static files, and templates. Initializes DB on startup.
No routes defined here — all routes live in routers/.
"""
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

import config
from database import init_db
from routers import system, domains, dns, ssl, proxy, errors, auth, settings
from middleware.error_capture import RequestIdMiddleware, register_error_handlers
from middleware.auth import AuthMiddleware
from middleware.csrf import CsrfMiddleware
from middleware.security_headers import SecurityHeadersMiddleware

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize DB tables on startup."""
    if getattr(config, "_SECRET_KEY_EPHEMERAL", False):
        logger.warning(
            "SECRET_KEY was missing — using ephemeral key. "
            "Add SECRET_KEY to .env (or re-run update/create_admin) "
            "so login sessions survive restarts."
        )
    logger.info("Initializing database...")
    await init_db()
    logger.info("Panel ready.")
    yield
    logger.info("Panel shutting down.")


app = FastAPI(
    title="VPS Control Panel",
    version="1.0.0",
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
)

# Middleware order: last added runs first on the request.
# Session → CSRF → SecurityHeaders → RequestId → Auth → app
app.add_middleware(AuthMiddleware)
app.add_middleware(RequestIdMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(CsrfMiddleware)
app.add_middleware(
    SessionMiddleware,
    secret_key=config.SECRET_KEY,
    session_cookie="srv_panel_session",
    max_age=config.SESSION_MAX_AGE,
    same_site="lax",
    https_only=config.SESSION_HTTPS_ONLY,
)
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
