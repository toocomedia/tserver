"""
test_postgres_manager.py — Unit tests for the PostgreSQL Manager plugin.

All subprocess, psutil, and filesystem calls are fully mocked.
No real PostgreSQL installation required. Safe to run on Windows or Linux.

Run:
    python -m unittest backend/tests/test_postgres_manager.py -v
"""
import re
import time
import unittest
from unittest.mock import MagicMock, patch, PropertyMock


# ---------------------------------------------------------------------------
# Helper: build a fake subprocess result
# ---------------------------------------------------------------------------
def _proc(returncode=0, stdout="", stderr=""):
    m = MagicMock()
    m.returncode = returncode
    m.stdout = stdout
    m.stderr = stderr
    return m


# ---------------------------------------------------------------------------
# service.py tests
# ---------------------------------------------------------------------------
class TestPostgresService(unittest.TestCase):

    def setUp(self):
        # Re-import fresh for each test to reset the singleton state
        import importlib
        import plugins.postgres_manager.service as svc_mod
        importlib.reload(svc_mod)
        self.svc_mod = svc_mod
        self.svc = svc_mod.PostgresService()

    @patch("shutil.which", return_value="/usr/bin/psql")
    def test_is_installed_true(self, _):
        self.assertTrue(self.svc.is_installed())

    @patch("shutil.which", return_value=None)
    @patch("os.path.exists", return_value=False)
    def test_is_installed_false(self, _, __):
        self.assertFalse(self.svc.is_installed())

    @patch("subprocess.run")
    @patch("shutil.which", return_value="/usr/bin/psql")
    def test_get_status_returns_dict(self, _, mock_run):
        mock_run.side_effect = [
            _proc(stdout="active\n"),           # systemctl is-active
            _proc(stdout="1234\n"),              # pgrep
            _proc(stdout="psql 15.6\n"),         # psql --version
        ]
        with patch("os.name", "posix"), \
             patch.object(self.svc, "_get_ram_mb", return_value=42.0), \
             patch.object(self.svc, "_check_port", return_value=True):
            status = self.svc.get_status()
        self.assertTrue(status["running"])
        self.assertEqual(status["mode"], "local")
        self.assertEqual(status["ram_mb"], 42.0)

    @patch("subprocess.run")
    @patch("shutil.which", return_value="/usr/bin/psql")
    def test_status_cache_ttl(self, _, mock_run):
        """get_status() must not call subprocess twice within 30 seconds."""
        mock_run.side_effect = [_proc(stdout="active\n"), _proc(stdout="1234\n"), _proc(stdout="psql 15\n")]
        with patch("os.name", "posix"), \
             patch.object(self.svc, "_get_ram_mb", return_value=10.0), \
             patch.object(self.svc, "_check_port", return_value=True):
            self.svc.get_status()
            call_count_after_first = mock_run.call_count
            self.svc.get_status()  # should hit cache
        self.assertEqual(mock_run.call_count, call_count_after_first, "Cache not used on second call")

    def test_pause_clears_cache(self):
        self.svc._status_cache = {"running": True}
        self.svc._cache_ts = time.monotonic()
        self.svc.pause()
        self.assertEqual(self.svc._status_cache, {})
        self.assertEqual(self.svc._cache_ts, 0.0)

    @patch("subprocess.run")
    @patch("shutil.which", return_value="/usr/bin/psql")
    def test_resume_invalidates_cache(self, _, mock_run):
        """resume() must force a fresh fetch on next get_status() call."""
        self.svc._status_cache = {"running": True}
        self.svc._cache_ts = time.monotonic()
        self.svc.resume()
        self.assertEqual(self.svc._cache_ts, 0.0)

    def test_get_usage_shape(self):
        with patch.object(self.svc, "get_status", return_value={
            "running": True, "ram_mb": 55.3, "pid": 999
        }), patch.object(self.svc, "_get_cpu_percent", return_value=1.2):
            usage = self.svc.get_usage()
        self.assertIn("cpu", usage)
        self.assertIn("mem", usage)
        self.assertIn("memory", usage)
        self.assertIn("count", usage)
        self.assertIn("status", usage)
        self.assertEqual(usage["status"], "active")
        self.assertEqual(usage["count"], 1)


# ---------------------------------------------------------------------------
# queries.py tests
# ---------------------------------------------------------------------------
class TestPostgresQueries(unittest.TestCase):

    def setUp(self):
        import importlib
        import plugins.postgres_manager.queries as q_mod
        importlib.reload(q_mod)
        self.q = q_mod

    def test_validate_ident_valid(self):
        # Should not raise
        self.q._validate_ident("my_db", "database")
        self.q._validate_ident("user-01", "username")

    def test_validate_ident_rejects_spaces(self):
        with self.assertRaises(ValueError):
            self.q._validate_ident("my db", "database")

    def test_validate_ident_rejects_semicolon(self):
        with self.assertRaises(ValueError):
            self.q._validate_ident("db;DROP", "database")

    def test_validate_ident_rejects_empty(self):
        with self.assertRaises(ValueError):
            self.q._validate_ident("", "name")

    def test_run_query_rejects_non_select(self):
        with self.assertRaises(ValueError, msg="Should reject DROP"):
            self.q.run_query("mydb", "DROP TABLE users;")

    def test_run_query_rejects_insert(self):
        with self.assertRaises(ValueError):
            self.q.run_query("mydb", "INSERT INTO t VALUES (1);")

    def test_run_query_rejects_long_sql(self):
        with self.assertRaises(ValueError):
            self.q.run_query("mydb", "SELECT " + "x" * 4001)

    def test_run_query_allows_select(self):
        with patch.object(self.q, "_run_psql", return_value="1|alice\n2|bob\n"):
            rows = self.q.run_query("mydb", "SELECT id, name FROM users;")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["row"], "1|alice")

    def test_create_database_valid(self):
        with patch.object(self.q, "_run_psql", return_value="") as mock_psql:
            self.q.create_database("testdb", "postgres")
        mock_psql.assert_called_once()
        call_args = mock_psql.call_args[0][0]
        self.assertIn("testdb", " ".join(call_args))

    def test_create_database_rejects_invalid_name(self):
        with self.assertRaises(ValueError):
            self.q.create_database("bad name!", "postgres")

    def test_create_user_rejects_short_password(self):
        with self.assertRaises(ValueError):
            self.q.create_user("alice", "short")

    def test_list_databases_parses_output(self):
        fake_out = "mydb|postgres|UTF8|8192 bytes\nappdb|appuser|UTF8|1488 kB\n"
        with patch.object(self.q, "_run_psql", return_value=fake_out):
            dbs = self.q.list_databases()
        self.assertEqual(len(dbs), 2)
        self.assertEqual(dbs[0]["name"], "mydb")
        self.assertEqual(dbs[1]["owner"], "appuser")

    def test_list_system_roles(self):
        fake_out = "pg_monitor|f|f\npg_read_all_data|f|f\n"
        with patch.object(self.q, "_run_psql", return_value=fake_out):
            roles = self.q.list_system_roles()
        self.assertEqual(len(roles), 2)
        self.assertEqual(roles[0]["name"], "pg_monitor")

    def test_escape_literal_escapes_single_quote(self):

        result = self.q._escape_literal("pass'word")
        self.assertEqual(result, "pass''word")


# ---------------------------------------------------------------------------
# schemas.py tests
# ---------------------------------------------------------------------------
class TestSchemas(unittest.TestCase):

    def setUp(self):
        from plugins.postgres_manager.schemas import (
            DatabaseCreate, UserCreate, PasswordChange, QueryRequest,
        )
        self.DatabaseCreate = DatabaseCreate
        self.UserCreate = UserCreate
        self.PasswordChange = PasswordChange
        self.QueryRequest = QueryRequest

    def test_database_create_valid(self):
        db = self.DatabaseCreate(name="mydb", owner="postgres")
        self.assertEqual(db.name, "mydb")

    def test_database_create_invalid_name(self):
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            self.DatabaseCreate(name="bad name")

    def test_user_create_short_password(self):
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            self.UserCreate(name="alice", password="short")

    def test_query_request_rejects_non_select(self):
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            self.QueryRequest(db="mydb", sql="DELETE FROM users;")

    def test_query_request_allows_select(self):
        q = self.QueryRequest(db="mydb", sql="SELECT * FROM users;")
        self.assertEqual(q.db, "mydb")

    def test_remote_config_request_valid(self):
        from plugins.postgres_manager.schemas import RemoteConfigRequest
        req = RemoteConfigRequest(mode="managed", domain="example.com", subdomain="db")
        self.assertEqual(req.mode, "managed")

    def test_remote_config_request_invalid_mode(self):
        from plugins.postgres_manager.schemas import RemoteConfigRequest
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            RemoteConfigRequest(mode="invalid_mode")

    def test_remote_config_request_rejects_invalid_client_cidr(self):
        from plugins.postgres_manager.schemas import RemoteConfigRequest
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            RemoteConfigRequest(mode="external", hostname="db.example.com", allowed_cidrs=["not-an-ip"])

class TestPostgresRemoteService(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
        from database import Base
        import models  # loads all models including PostgresRemoteDomain
        from plugins.postgres_manager.service import PostgresService

        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        self.async_session = async_sessionmaker(self.engine, expire_on_commit=False, class_=AsyncSession)
        self.svc = PostgresService()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_multi_domain_remote_lifecycle(self):
        async with self.async_session() as db:
            from unittest.mock import AsyncMock, patch
            from models.domain import Domain
            db.add(Domain(name="example.com", server_ip="203.0.113.10"))
            await db.commit()
            # Add first domain
            with patch("plugins.postgres_manager.native_tls.resolve_host", new=AsyncMock(return_value=["203.0.113.10"])), \
                 patch("plugins.postgres_manager.native_tls.issue_shared_certificate", new=AsyncMock(return_value=("postgres-remote", None))), \
                 patch("plugins.postgres_manager.native_tls.configure_postgres", new=AsyncMock()), \
                 patch("plugins.postgres_manager.native_tls.firewall_allow", new=AsyncMock()), \
                 patch("services.dns_service.add_a_record", new=AsyncMock()):
                entry1 = await self.svc.add_remote_domain(
                    db, mode="managed", domain="example.com", subdomain="db1",
                    hostname=None, issue_ssl=True, allowed_cidrs=["203.0.113.10/32"]
                )
            self.assertEqual(entry1["domain"], "db1.example.com")

            # Add second domain
            with patch("plugins.postgres_manager.native_tls.resolve_host", new=AsyncMock(return_value=["203.0.113.10"])), \
                 patch("plugins.postgres_manager.native_tls.issue_shared_certificate", new=AsyncMock(return_value=("postgres-remote", None))), \
                 patch("plugins.postgres_manager.native_tls.configure_postgres", new=AsyncMock()), \
                 patch("plugins.postgres_manager.native_tls.firewall_allow", new=AsyncMock()):
                entry2 = await self.svc.add_remote_domain(
                    db, mode="external", domain=None, subdomain=None,
                    hostname="pg.otherdomain.com", issue_ssl=True,
                    allowed_cidrs=["203.0.113.10/32"]
                )
            self.assertEqual(entry2["domain"], "pg.otherdomain.com")

            # List domains
            domains = await self.svc.list_remote_domains(db)
            self.assertEqual(len(domains), 2)
            domain_names = [d["domain"] for d in domains]
            self.assertIn("db1.example.com", domain_names)
            self.assertIn("pg.otherdomain.com", domain_names)

            # Re-issue SSL
            with patch("plugins.postgres_manager.native_tls.resolve_host", new=AsyncMock(return_value=["203.0.113.10"])), \
                 patch("plugins.postgres_manager.native_tls.issue_shared_certificate", new=AsyncMock(return_value=("postgres-remote", None))), \
                 patch("plugins.postgres_manager.native_tls.configure_postgres", new=AsyncMock()), \
                 patch("plugins.postgres_manager.native_tls.firewall_allow", new=AsyncMock()):
                reissued = await self.svc.reissue_remote_ssl(db, "db1.example.com")
            self.assertEqual(reissued["domain"], "db1.example.com")

            # Delete domain
            with patch("plugins.postgres_manager.native_tls.firewall_remove", new=AsyncMock()), \
                 patch("plugins.postgres_manager.native_tls.configure_postgres", new=AsyncMock()):
                deleted = await self.svc.delete_remote_domain(db, "db1.example.com")
            self.assertTrue(deleted)

            domains_after = await self.svc.list_remote_domains(db)
            self.assertEqual(len(domains_after), 1)
            self.assertEqual(domains_after[0]["domain"], "pg.otherdomain.com")



if __name__ == "__main__":
    unittest.main()
