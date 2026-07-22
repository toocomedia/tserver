"""
database.py — Async SQLAlchemy engine + session factory + base model
"""
import logging
from sqlalchemy import text, inspect
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from config import DATABASE_URL

logger = logging.getLogger(__name__)


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


def _column_names(sync_conn, table: str) -> set[str]:
    insp = inspect(sync_conn)
    if table not in insp.get_table_names():
        return set()
    return {c["name"] for c in insp.get_columns(table)}


def _column_nullable(sync_conn, table: str, column: str) -> bool | None:
    insp = inspect(sync_conn)
    if table not in insp.get_table_names():
        return None
    for c in insp.get_columns(table):
        if c["name"] == column:
            return bool(c.get("nullable"))
    return None


def _migrate_sync(sync_conn) -> None:
    """Idempotent SQLite migrations for existing panel DBs."""
    tables = set(inspect(sync_conn).get_table_names())

    # --- reverse_proxies: dns_managed + nullable domain_id ---
    if "reverse_proxies" in tables:
        cols = _column_names(sync_conn, "reverse_proxies")
        if "dns_managed" not in cols:
            logger.info("Migrating reverse_proxies: add dns_managed")
            sync_conn.execute(text(
                "ALTER TABLE reverse_proxies "
                "ADD COLUMN dns_managed BOOLEAN DEFAULT 1 NOT NULL"
            ))
            cols.add("dns_managed")

        # --- cache columns (added in performance update) ---
        cache_cols = {
            "cache_enabled":          "BOOLEAN DEFAULT 0 NOT NULL",
            "cache_ttl_minutes":      "INTEGER DEFAULT 10 NOT NULL",
            "cache_auto_clear_hours": "INTEGER DEFAULT 0 NOT NULL",
            "last_cache_cleared":     "DATETIME",
        }
        for col, ddl in cache_cols.items():
            if col not in cols:
                logger.info("Migrating reverse_proxies: add %s", col)
                sync_conn.execute(text(
                    f"ALTER TABLE reverse_proxies ADD COLUMN {col} {ddl}"
                ))

        domain_nullable = _column_nullable(sync_conn, "reverse_proxies", "domain_id")
        if domain_nullable is False:
            logger.info("Migrating reverse_proxies: allow NULL domain_id (table rebuild)")
            sync_conn.execute(text("""
                CREATE TABLE reverse_proxies_new (
                    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                    domain_id INTEGER,
                    subdomain VARCHAR(255) NOT NULL DEFAULT '',
                    full_domain VARCHAR(255) NOT NULL UNIQUE,
                    target_ip VARCHAR(64) NOT NULL,
                    target_port INTEGER NOT NULL,
                    protocol VARCHAR(8) NOT NULL DEFAULT 'http',
                    ssl_enabled BOOLEAN NOT NULL DEFAULT 0,
                    ssl_cert_id INTEGER,
                    nginx_config_path VARCHAR(512),
                    dns_managed BOOLEAN NOT NULL DEFAULT 1,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                    FOREIGN KEY(domain_id) REFERENCES domains (id),
                    FOREIGN KEY(ssl_cert_id) REFERENCES ssl_certs (id)
                )
            """))
            # Copy rows (dns_managed may already exist)
            if "dns_managed" in cols:
                sync_conn.execute(text("""
                    INSERT INTO reverse_proxies_new (
                        id, domain_id, subdomain, full_domain, target_ip, target_port,
                        protocol, ssl_enabled, ssl_cert_id, nginx_config_path,
                        dns_managed, created_at
                    )
                    SELECT
                        id, domain_id, subdomain, full_domain, target_ip, target_port,
                        protocol, ssl_enabled, ssl_cert_id, nginx_config_path,
                        COALESCE(dns_managed, 1), created_at
                    FROM reverse_proxies
                """))
            else:
                sync_conn.execute(text("""
                    INSERT INTO reverse_proxies_new (
                        id, domain_id, subdomain, full_domain, target_ip, target_port,
                        protocol, ssl_enabled, ssl_cert_id, nginx_config_path,
                        dns_managed, created_at
                    )
                    SELECT
                        id, domain_id, subdomain, full_domain, target_ip, target_port,
                        protocol, ssl_enabled, ssl_cert_id, nginx_config_path,
                        1, created_at
                    FROM reverse_proxies
                """))
            sync_conn.execute(text("DROP TABLE reverse_proxies"))
            sync_conn.execute(text(
                "ALTER TABLE reverse_proxies_new RENAME TO reverse_proxies"
            ))

    # --- ssl_certs: nullable domain_id ---
    if "ssl_certs" in tables:
        domain_nullable = _column_nullable(sync_conn, "ssl_certs", "domain_id")
        if domain_nullable is False:
            logger.info("Migrating ssl_certs: allow NULL domain_id (table rebuild)")
            sync_conn.execute(text("""
                CREATE TABLE ssl_certs_new (
                    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                    domain_id INTEGER,
                    full_domain VARCHAR(255) NOT NULL UNIQUE,
                    cert_path VARCHAR(512),
                    expiry_date DATETIME,
                    auto_renew BOOLEAN NOT NULL DEFAULT 1,
                    issued_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                    FOREIGN KEY(domain_id) REFERENCES domains (id)
                )
            """))
            sync_conn.execute(text("""
                INSERT INTO ssl_certs_new (
                    id, domain_id, full_domain, cert_path, expiry_date, auto_renew, issued_at
                )
                SELECT id, domain_id, full_domain, cert_path, expiry_date, auto_renew, issued_at
                FROM ssl_certs
            """))
            sync_conn.execute(text("DROP TABLE ssl_certs"))
            sync_conn.execute(text("ALTER TABLE ssl_certs_new RENAME TO ssl_certs"))


async def init_db():
    """Create all tables on startup if they do not exist, then migrate."""
    # Import all models so Base knows about them
    import models.domain       # noqa: F401
    import models.dns_record   # noqa: F401
    import models.ssl_cert     # noqa: F401
    import models.proxy        # noqa: F401
    import models.error_event  # noqa: F401
    import models.user         # noqa: F401
    import models.notification # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_migrate_sync)
