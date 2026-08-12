from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from services.i18n_service import i18n_service

class LocaleMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Validate cookie against loaded locales
        lang = request.cookies.get("panel_lang", "en")
        if lang not in i18n_service.locales:
            lang = "en"
        
        # Store result in request.state.lang. Never mutate shared language state.
        request.state.lang = lang
        
        response = await call_next(request)
        return response
