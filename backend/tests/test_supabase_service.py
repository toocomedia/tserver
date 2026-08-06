import sys
import unittest
from pathlib import Path
from unittest.mock import patch

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


if __name__ == "__main__":
    unittest.main()
