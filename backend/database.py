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

    # --- domains: project_type ---
    if "domains" in tables:
        cols = _column_names(sync_conn, "domains")
        if "project_type" not in cols:
            logger.info("Migrating domains: add project_type")
            sync_conn.execute(text(
                "ALTER TABLE domains ADD COLUMN project_type VARCHAR(32) DEFAULT 'static' NOT NULL"
            ))

    # --- postgres_remote_domains: v2 remote-access fields ---
    # This table existed before the v2 model. create_all() never adds columns
    # to an existing SQLite table, so upgrade each field explicitly.
    if "postgres_remote_domains" in tables:
        cols = _column_names(sync_conn, "postgres_remote_domains")
        remote_columns = {
            "encryption_enabled": "BOOLEAN DEFAULT 1 NOT NULL",
            "certificate_name": "VARCHAR(255)",
            "certificate_expiry": "DATETIME",
            "allowed_cidrs": "TEXT DEFAULT '0.0.0.0/0' NOT NULL",
            "dns_status": "VARCHAR(16) DEFAULT 'ready' NOT NULL",
            "tls_status": "VARCHAR(16) DEFAULT 'pending' NOT NULL",
            "postgres_status": "VARCHAR(16) DEFAULT 'pending' NOT NULL",
            "enabled": "BOOLEAN DEFAULT 0 NOT NULL",
            "last_error": "TEXT",
        }
        for col, ddl in remote_columns.items():
            if col not in cols:
                logger.info("Migrating postgres_remote_domains: add %s", col)
                sync_conn.execute(text(
                    f"ALTER TABLE postgres_remote_domains ADD COLUMN {col} {ddl}"
                ))

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

    if "hosted_apps" in tables:
        cols = _column_names(sync_conn, "hosted_apps")
        app_columns = {
            "paused_by_dependency": "VARCHAR(64)",
            "deployed_revision": "VARCHAR(64)",
            "deployed_at": "DATETIME",
            "available_revision": "VARCHAR(64)",
            "available_revision_message": "VARCHAR(512)",
            "available_revision_at": "DATETIME",
            "source_checked_at": "DATETIME",
            "active_release": "VARCHAR(128)",
            "previous_release": "VARCHAR(128)",
        }
        for col, ddl in app_columns.items():
            if col not in cols:
                logger.info("Migrating hosted_apps: add %s", col)
                sync_conn.execute(text(
                    f"ALTER TABLE hosted_apps ADD COLUMN {col} {ddl}"
                ))

    if "app_deployments" in tables:
        cols = _column_names(sync_conn, "app_deployments")
        deployment_columns = {
            "action": "VARCHAR(16) DEFAULT 'deploy' NOT NULL",
            "source_revision": "VARCHAR(64)",
            "previous_revision": "VARCHAR(64)",
            "rollback_status": "VARCHAR(24)",
        }
        for col, ddl in deployment_columns.items():
            if col not in cols:
                logger.info("Migrating app_deployments: add %s", col)
                sync_conn.execute(text(
                    f"ALTER TABLE app_deployments ADD COLUMN {col} {ddl}"
                ))

    if "container_apps" in tables:
        cols = _column_names(sync_conn, "container_apps")
        container_columns = {
            "preset": "VARCHAR(24)",
            "wordpress_content_volume": "VARCHAR(128)",
            "wordpress_site_title": "VARCHAR(255)",
            "wordpress_admin_user": "VARCHAR(64)",
            "wordpress_admin_email": "VARCHAR(255)",
            "wordpress_pending_secret_path": "VARCHAR(512)",
        }
        for col, ddl in container_columns.items():
            if col not in cols:
                logger.info("Migrating container_apps: add %s", col)
                sync_conn.execute(text(f"ALTER TABLE container_apps ADD COLUMN {col} {ddl}"))

    if "container_apps" in tables and "container_app_databases" in tables:
        existing = sync_conn.execute(text("SELECT COUNT(*) FROM container_app_databases")).scalar() or 0
        if not existing:
            rows = sync_conn.execute(text(
                "SELECT id, database_mode, database_provider, database_name, database_user "
                "FROM container_apps WHERE database_mode != 'none'"
            )).mappings()
            for row in rows:
                provider = "panel_postgres" if row["database_mode"] == "panel_postgres" else "external"
                sync_conn.execute(text(
                    "INSERT INTO container_app_databases "
                    "(app_id, kind, provider, environment_key, database_name, username, status) "
                    "VALUES (:app_id, 'postgresql', :provider, 'DATABASE_URL', :database_name, :username, 'ready')"
                ), {"app_id": row["id"], "provider": provider,
                    "database_name": row["database_name"], "username": row["database_user"]})

    if "container_app_backups" in tables and "database_backup_id" not in _column_names(sync_conn, "container_app_backups"):
        sync_conn.execute(text("ALTER TABLE container_app_backups ADD COLUMN database_backup_id INTEGER"))

    if "users" in tables:
        cols = _column_names(sync_conn, "users")
        user_columns = {
            "totp_secret": "VARCHAR(32)",
            "is_2fa_enabled": "BOOLEAN DEFAULT 0 NOT NULL",
        }
        for col, ddl in user_columns.items():
            if col not in cols:
                logger.info("Migrating users: add %s", col)
                sync_conn.execute(text(f"ALTER TABLE users ADD COLUMN {col} {ddl}"))

    # --- resource_guard_settings: Slice 1 additions ---
    if "resource_guard_settings" in tables:
        cols = _column_names(sync_conn, "resource_guard_settings")
        for col, ddl in {
            "protected_reserve_mb": "INTEGER DEFAULT 400 NOT NULL",
            "build_concurrency":    "INTEGER DEFAULT 1 NOT NULL",
        }.items():
            if col not in cols:
                logger.info("Migrating resource_guard_settings: add %s", col)
                sync_conn.execute(text(f"ALTER TABLE resource_guard_settings ADD COLUMN {col} {ddl}"))

    # --- container_app_deployments: Slice 1 additions ---
    if "container_app_deployments" in tables:
        cols = _column_names(sync_conn, "container_app_deployments")
        for col, ddl in {
            "profile":              "VARCHAR(32)",
            "peak_ram_mb":          "INTEGER",
            "guard_blocked_reason": "TEXT",
        }.items():
            if col not in cols:
                logger.info("Migrating container_app_deployments: add %s", col)
                sync_conn.execute(text(f"ALTER TABLE container_app_deployments ADD COLUMN {col} {ddl}"))

    # --- container_apps: Slice 1 additions ---
    if "container_apps" in tables:
        cols = _column_names(sync_conn, "container_apps")
        if "pending_database_specs" not in cols:
            logger.info("Migrating container_apps: add pending_database_specs")
            sync_conn.execute(text("ALTER TABLE container_apps ADD COLUMN pending_database_specs TEXT"))

    # --- safe_install_runs: Slice 3 ---
    if "safe_install_runs" not in tables:
        logger.info("Creating safe_install_runs table")
        sync_conn.execute(text("""
            CREATE TABLE safe_install_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                operation_id INTEGER NOT NULL REFERENCES guard_operations(id),
                candidate_snapshot TEXT NOT NULL DEFAULT '[]',
                approved_ids TEXT NOT NULL DEFAULT '[]',
                services_stopped TEXT NOT NULL DEFAULT '[]',
                before_ram_mb INTEGER,
                after_ram_mb INTEGER,
                outcome VARCHAR(16) NOT NULL DEFAULT 'pending',
                restore_state VARCHAR(24) NOT NULL DEFAULT 'pending',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                finished_at DATETIME
            )
        """))
        sync_conn.execute(text("CREATE INDEX ix_safe_install_runs_operation_id ON safe_install_runs (operation_id)"))
        sync_conn.execute(text("CREATE INDEX ix_safe_install_runs_outcome ON safe_install_runs (outcome)"))


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
    import models.mail_domain  # noqa: F401
    import models.component_state  # noqa: F401
    import models.postgres_remote  # noqa: F401
    import models.hosted_app       # noqa: F401
    import models.app_deployment   # noqa: F401
    import models.app_environment  # noqa: F401
    import models.container_app  # noqa: F401
    import models.container_app_deployment  # noqa: F401
    import models.container_app_database  # noqa: F401
    import models.container_app_backup  # noqa: F401
    import models.resource_guard  # noqa: F401
    import models.guard_operation  # noqa: F401
    import models.safe_install_run  # noqa: F401
    import models.supabase_project  # noqa: F401
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_migrate_sync)
