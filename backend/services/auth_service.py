"""
services/auth_service.py — Password hashing and admin user CRUD.
"""
from datetime import datetime, timezone

from passlib.context import CryptContext
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from models.user import User

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Precomputed bcrypt hash so verify always runs (missing-user path).
# Hash of an arbitrary string — never a real account password.
_DUMMY_HASH = "$2b$12$EixZaYVK1fsbw1ZfbX3OXePaWxn96p36WQoeG6Lruj3vjPGga31lW"


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, password_hash: str) -> bool:
    try:
        return pwd_context.verify(plain, password_hash)
    except Exception:
        return False


async def get_by_id(db: AsyncSession, user_id: int) -> User | None:
    return await db.get(User, user_id)


async def get_by_username(db: AsyncSession, username: str) -> User | None:
    result = await db.execute(
        select(User).where(User.username == username.strip())
    )
    return result.scalar_one_or_none()


async def count_users(db: AsyncSession) -> int:
    return int(await db.scalar(select(func.count()).select_from(User)) or 0)


async def authenticate(
    db: AsyncSession, username: str, password: str
) -> User | None:
    """Return user if credentials match; always runs a bcrypt verify."""
    user = await get_by_username(db, username)
    if user is None:
        verify_password(password, _DUMMY_HASH)
        return None
    if not verify_password(password, user.password_hash):
        return None
    user.last_login_at = datetime.now(timezone.utc).replace(tzinfo=None)
    await db.flush()
    return user


async def create_user(
    db: AsyncSession, username: str, password: str
) -> User:
    username = username.strip()
    if not username:
        raise ValueError("Username is required")
    if len(username) > 64:
        raise ValueError("Username must be at most 64 characters")
    if len(password) < 8:
        raise ValueError("Password must be at least 8 characters")
    existing = await get_by_username(db, username)
    if existing is not None:
        raise ValueError(f"User '{username}' already exists")
    user = User(username=username, password_hash=hash_password(password))
    db.add(user)
    await db.flush()
    await db.refresh(user)
    return user


async def set_password(
    db: AsyncSession, username: str, password: str
) -> User:
    """Reset password for an existing user."""
    if len(password) < 8:
        raise ValueError("Password must be at least 8 characters")
    user = await get_by_username(db, username)
    if user is None:
        raise ValueError(f"User '{username}' not found")
    user.password_hash = hash_password(password)
    await db.flush()
    await db.refresh(user)
    return user


async def create_or_reset_admin(
    db: AsyncSession,
    username: str,
    password: str,
    *,
    force: bool = False,
) -> tuple[User, str]:
    """
    Create admin or reset password if force=True.
    Returns (user, action) where action is 'created' or 'reset'.
    """
    username = username.strip() or "admin"
    existing = await get_by_username(db, username)
    if existing is None:
        user = await create_user(db, username, password)
        return user, "created"
    if not force:
        raise ValueError(
            f"User '{username}' already exists. Use --force to reset password."
        )
    user = await set_password(db, username, password)
    return user, "reset"
