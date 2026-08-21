"""Focused contracts for Apps Engine build boundaries."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import config
from services.apps_engine import build_secrets, build_workspace


class AppsEngineBuildTests(unittest.TestCase):
    def test_legacy_secret_selection_excludes_normal_runtime_values(self):
        values = {
            "DATABASE_URL": "postgres://secret",
            "APP_SECRET": "secret",
            "NODE_ENV": "production",
            "PORT": "3000",
        }
        self.assertEqual(
            build_secrets.select_names(values, None),
            ["DATABASE_URL", "APP_SECRET"],
        )

    def test_explicit_secret_selection_requires_existing_values(self):
        values = {"DATABASE_URL": "postgres://secret", "NODE_ENV": "production"}
        self.assertEqual(
            build_secrets.select_names(values, '["DATABASE_URL"]'),
            ["DATABASE_URL"],
        )
        with self.assertRaisesRegex(ValueError, "missing"):
            build_secrets.select_names(values, '["MISSING_KEY"]')

    def test_railpack_config_is_validated_and_merged_atomically(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "railpack.json").write_text(json.dumps({"phases": {}, "secrets": ["OLD_SECRET"]}))
            build_secrets.inject_railpack_secrets(root, ["DATABASE_URL"])
            data = json.loads((root / "railpack.json").read_text())
            self.assertEqual(data["secrets"], ["OLD_SECRET", "DATABASE_URL"])
            (root / "railpack.json").write_text("not json")
            with self.assertRaisesRegex(RuntimeError, "invalid JSON"):
                build_secrets.inject_railpack_secrets(root, ["DATABASE_URL"])

    def test_workspace_is_private_and_deployment_scoped(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(config, "CONTAINER_APP_ENV_ROOT", f"{temporary}/env"):
            workspace = build_workspace.prepare(12)
            self.assertEqual(workspace.root.name, "12")
            self.assertTrue(workspace.temporary.is_dir())
            self.assertTrue(workspace.cache.is_dir())


if __name__ == "__main__":
    unittest.main()
