from __future__ import annotations

import unittest
from pathlib import Path
import sys

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from services.apps_engine.app_spec import AppSpec, SecretRequirement
from services.apps_engine.app_spec_codec import app_spec_from_dict, app_spec_to_dict
from services.official_stacks.schema import OfficialStackDefinition
from tests.app_spec_fixtures import canonical_app_spec


class AppSpecSchemaTests(unittest.TestCase):
    def test_canonical_round_trip_and_compatibility_alias(self):
        spec = app_spec_from_dict(canonical_app_spec())
        self.assertIsInstance(spec, AppSpec)
        self.assertIs(OfficialStackDefinition, AppSpec)
        encoded = app_spec_to_dict(spec)
        self.assertEqual(app_spec_to_dict(app_spec_from_dict(encoded)), encoded)
        self.assertEqual(spec.web_health_path, "/ready")

    def test_secret_generator_has_no_constructor_default(self):
        with self.assertRaises(TypeError):
            SecretRequirement("APP_SECRET", "Signing secret")


if __name__ == "__main__":
    unittest.main()
