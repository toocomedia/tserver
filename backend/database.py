"""
database.py — Async SQLAlchemy engine + session factory + base model
"""
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from config import DATABASE_URL


class Base(DeclarativeBase):
    pass


engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    connect_args={"check_same_thread": False},
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)


async def get_db():
    """FastAPI dependency — yields an async DB session."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db():
    """Create all tables on startup if they do not exist."""
    # Import all models so Base knows about them
    import models.domain       # noqa: F401
    import models.dns_record   # noqa: F401
    import models.ssl_cert     # noqa: F401
    import models.proxy        # noqa: F401
    import models.error_event  # noqa: F401
    import models.user         # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
