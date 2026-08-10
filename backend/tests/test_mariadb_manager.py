import re
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

from pydantic import ValidationError

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from plugins.mariadb_manager.schemas import DatabaseCreate
from plugins.mariadb_manager.service import MariaDBManagerService


class MariaDBManagerTests(unittest.TestCase):
    def test_database_schema_rejects_unsafe_identifier(self):
        with self.assertRaises(ValidationError):
            DatabaseCreate(database="site;drop", user="site_user")

    def test_password_is_safe_for_root_helper(self):
        password = MariaDBManagerService.new_password()
        self.assertRegex(password, r"^[A-Za-z0-9_-]{16,128}$")

    def test_create_database_returns_password_once(self):
        service = MariaDBManagerService()
        service._call = Mock(return_value={"database": "site_db", "user": "site_user"})
        result = service.create_database("site_db", "site_user")
        self.assertEqual("site_db", result["database"])
        self.assertEqual("site_user", result["user"])
        self.assertRegex(result["password"], r"^[A-Za-z0-9_-]{16,128}$")
        self.assertEqual("create_database", service._call.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
