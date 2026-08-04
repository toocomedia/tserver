"""
routers/two_factor.py — 2FA management endpoints for logged-in users.
"""
import logging
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.user import User
from services import auth_service, totp_service
from templating import templates

logger = logging.getLogger(__name__)
router = APIRouter(tags=["two_factor"])


async def get_current_user(
    request: Request, db: AsyncSession = Depends(get_db)
) -> User:
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    user = await auth_service.get_by_id(db, user_id)
    if not user:
        request.session.clear()
        raise HTTPException(status_code=401, detail="User not found")
    return user


@router.get("/settings/2fa", response_class=HTMLResponse)
async def get_2fa_settings(
    request: Request,
    user: User = Depends(get_current_user),
):
    return templates.TemplateResponse(
        "pages/settings/2fa.html",
        {
            "request": request,
            "active_page": "settings",
            "user": user,
            "error": None,
            "success": None,
        },
    )


@router.post("/settings/2fa/setup")
async def setup_2fa_initiate(
    request: Request,
    user: User = Depends(get_current_user),
):
    """Generate temporary TOTP secret and SVG QR code for user setup."""
    if user.totp_enabled:
        return JSONResponse({"detail": "2FA is already enabled"}, status_code=400)

    secret = totp_service.generate_secret()
    # Store temporary secret in session until confirmed
    request.session["pending_totp_secret"] = secret

    provisioning_uri = totp_service.get_provisioning_uri(
        username=user.username,
        secret=secret,
        issuer_name="Control Panel",
    )
    qr_svg = totp_service.generate_qr_code_svg(provisioning_uri)

    return JSONResponse({
        "secret": secret,
        "qr_svg": qr_svg,
    })


@router.post("/settings/2fa/confirm")
async def confirm_2fa_setup(
    request: Request,
    code: str = Form(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Verify test 6-digit code, generate recovery codes, and enable 2FA."""
    pending_secret = request.session.get("pending_totp_secret")
    if not pending_secret:
        return JSONResponse(
            {"detail": "No 2FA setup session found. Please start setup again."},
            status_code=400,
        )

    if not totp_service.verify_totp(pending_secret, code):
        return JSONResponse(
            {"detail": "Invalid verification code. Please check your authenticator app."},
            status_code=400,
        )

    # Generate emergency recovery codes
    plain_codes, hashed_codes_json = totp_service.generate_recovery_codes(count=8)

    # Enable 2FA on user account
    await auth_service.enable_2fa(db, user, pending_secret, hashed_codes_json)

    # Clear pending secret from session
    request.session.pop("pending_totp_secret", None)
    logger.info("2FA successfully enabled for user '%s'", user.username)

    return JSONResponse({
        "success": True,
        "message": "Two-Factor Authentication is now enabled!",
        "recovery_codes": plain_codes,
    })


@router.post("/settings/2fa/disable")
async def disable_2fa_action(
    request: Request,
    password: str = Form(...),
    code: str = Form(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Disable 2FA after verifying current password and 6-digit code/recovery code."""
    if not user.totp_enabled:
        return JSONResponse({"detail": "2FA is not enabled"}, status_code=400)

    # Verify password first
    if not auth_service.verify_password(password, user.password_hash):
        return JSONResponse({"detail": "Incorrect password"}, status_code=400)

    # Verify 2FA code or recovery code
    valid_2fa = await auth_service.verify_user_2fa(db, user, code)
    if not valid_2fa:
        return JSONResponse({"detail": "Invalid 2FA code or recovery key"}, status_code=400)

    await auth_service.disable_2fa(db, user)
    logger.info("2FA disabled for user '%s'", user.username)

    return JSONResponse({
        "success": True,
        "message": "Two-Factor Authentication has been disabled.",
    })
