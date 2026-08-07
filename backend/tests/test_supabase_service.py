import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from models.supabase_project import SupabaseProject
from plugins.supabase import service


def _project(region: str | None = "eu-central-1") -> SupabaseProject:
    return SupabaseProject(
        name="Demo",
        project_ref="uwhexjgccucvvqcwixrh",
        db_host="db.uwhexjgccucvvqcwixrh.supabase.co",
        db_port=5432,
        db_name="postgres",
        db_user="postgres",
        db_password_enc="encrypted",
        region=region,
    )


class SupabaseConnectionTests(unittest.TestCase):
    def test_pooler_host_keeps_management_api_aws_prefix(self):
        host = service._pooler_host("aws-0-eu-central-1")
        self.assertEqual(host, "aws-0-eu-central-1.pooler.supabase.com")

    def test_pooler_dsn_uses_session_mode_and_project_user(self):
        project = _project()
        with patch.object(service, "_decrypt_password", return_value="secret"):
            dsn = service._dsn(project, use_pooler=True)
        self.assertIn("postgres.uwhexjgccucvvqcwixrh:secret@", dsn)
        self.assertIn("aws-0-eu-central-1.pooler.supabase.com:5432", dsn)

    def test_pooler_settings_use_assigned_aws_one_host(self):
        settings = service._pooler_settings([{
            "connectionString": (
                "postgresql://postgres.uwhexjgccucvvqcwixrh:[PASSWORD]"
                "@aws-1-us-east-2.pooler.supabase.com:5432/postgres"
            ),
        }])
        self.assertEqual(settings["host"], "aws-1-us-east-2.pooler.supabase.com")
        self.assertEqual(settings["port"], 5432)
        self.assertEqual(settings["user"], "postgres.uwhexjgccucvvqcwixrh")

    def test_pooler_settings_prefer_api_fields_over_connection_template(self):
        settings = service._pooler_settings([{
            "db_host": "aws-1-eu-west-1.pooler.supabase.com",
            "db_port": 5432,
            "db_user": "postgres.uwhexjgccucvvqcwixrh",
            "db_name": "postgres",
            "connectionString": "postgresql://invalid@[aws-1-eu-west-1.pooler.supabase.com]",
        }])
        self.assertEqual(settings["host"], "aws-1-eu-west-1.pooler.supabase.com")

    def test_direct_network_failure_uses_pooler(self):
        error = OSError(101, "Network is unreachable")
        self.assertTrue(service._should_use_pooler(_project(), error))

    def test_direct_timeout_uses_pooler(self):
        self.assertTrue(service._should_use_pooler(_project(), TimeoutError()))

    def test_pooler_project_does_not_fallback_to_another_pooler(self):
        project = _project()
        project.db_host = "aws-0-eu-central-1.pooler.supabase.com"
        error = OSError(101, "Network is unreachable")
        self.assertFalse(service._should_use_pooler(project, error))

    def test_pooler_connections_disable_asyncpg_statement_cache(self):
        project = _project()
        project.db_host = "aws-1-eu-west-1.pooler.supabase.com"
        self.assertEqual(service._connect_options(project)["statement_cache_size"], 0)


class SupabaseProvisionTests(unittest.IsolatedAsyncioTestCase):
    async def test_provision_uses_escaped_literals_for_ddl(self):
        connection = AsyncMock()
        app_connection = AsyncMock()
        with (
            patch.object(service, "get_project", new=AsyncMock(return_value=_project())),
            patch.object(service, "_pg_connect", new=AsyncMock(
                side_effect=[connection, app_connection]
            )),
        ):
            await service.provision_app_database(
                1, "app1", "app1", "pass'word", None
            )
        self.assertEqual(
            'CREATE USER "app1" WITH PASSWORD \'pass\'\'word\' LOGIN',
            connection.execute.await_args_list[0].args[0],
        )
        self.assertEqual(
            'CREATE DATABASE "app1"',
            connection.execute.await_args_list[1].args[0],
        )
        self.assertEqual(
            'GRANT CONNECT ON DATABASE "app1" TO "app1"',
            app_connection.execute.await_args_list[0].args[0],
        )
        self.assertEqual(
            'GRANT USAGE, CREATE ON SCHEMA public TO "app1"',
            app_connection.execute.await_args_list[1].args[0],
        )
        connection.close.assert_awaited_once()
        app_connection.close.assert_awaited_once()

    def test_pooler_app_url_includes_project_ref(self):
        project = _project()
        project.db_host = "aws-0-eu-central-1.pooler.supabase.com"

        dsn = service._app_database_dsn(project, "app1", "app1", "secret")

        self.assertIn("app1.uwhexjgccucvvqcwixrh:secret@", dsn)
        self.assertIn("aws-0-eu-central-1.pooler.supabase.com:5432/app1", dsn)

    async def test_repair_adds_tenant_to_older_pooler_url(self):
        project = _project()
        project.db_host = "aws-0-eu-central-1.pooler.supabase.com"
        with tempfile.TemporaryDirectory() as directory:
            env_path = Path(directory) / "app.env"
            env_path.write_text(
                "DATABASE_URL=postgresql+asyncpg://app1:secret@"
                "aws-0-eu-central-1.pooler.supabase.com:5432/app1\n",
                encoding="utf-8",
            )
            app = type("App", (), {
                "postgres_mode": "supabase", "supabase_project_id": 1,
                "env_path": str(env_path),
            })()
            with patch.object(service, "get_project", new=AsyncMock(return_value=project)):
                changed = await service.repair_app_database_url(app, None)
            repaired = env_path.read_text(encoding="utf-8")
        self.assertTrue(changed)
        self.assertIn("app1.uwhexjgccucvvqcwixrh:secret@", repaired)

    async def test_deprovision_terminates_sessions_before_drop(self):
        connection = AsyncMock()
        with (
            patch.object(service, "get_project", new=AsyncMock(return_value=_project())),
            patch.object(service, "_pg_connect", new=AsyncMock(return_value=connection)),
        ):
            await service.deprovision_app_database(1, "app1", "app1", None)
        self.assertIn("pid <> pg_backend_pid()", connection.execute.await_args_list[0].args[0])
        self.assertEqual('DROP DATABASE IF EXISTS "app1"', connection.execute.await_args_list[1].args[0])
        self.assertEqual('DROP USER IF EXISTS "app1"', connection.execute.await_args_list[2].args[0])
        connection.close.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
