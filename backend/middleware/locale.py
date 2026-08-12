from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

class LocaleMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Validate cookie against explicit en/fr allowlist
        lang = request.cookies.get("panel_lang", "en")
        if lang not in ("en", "fr"):
            lang = "en"
        
        # Store result in request.state.lang. Never mutate shared language state.
        request.state.lang = lang
        
        response = await call_next(request)
        return response
