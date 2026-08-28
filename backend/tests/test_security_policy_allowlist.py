from __future__ import annotations

import copy
import unittest
from types import SimpleNamespace
from pathlib import Path
import sys

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from services.apps_engine.security_policy import RESTART_POLICY, SECURITY_OPTIONS, validate_app_spec
from services.official_stacks.compose_runtime import render_compose
from tests.app_spec_fixtures import canonical_app_spec


class AppSpecSecurityPolicyTests(unittest.TestCase):
    def test_safe_spec_renders_fixed_runtime_hardening(self):
        spec = validate_app_spec(canonical_app_spec())
        rendered = render_compose(SimpleNamespace(id=7, host_port=32007), spec, {"db": {}, "web": {}})
        self.assertEqual(rendered["services"]["web"]["restart"], RESTART_POLICY)
        self.assertEqual(rendered["services"]["web"]["security_opt"], list(SECURITY_OPTIONS))

    def test_unknown_capability_and_host_mount_are_rejected(self):
        privileged = copy.deepcopy(canonical_app_spec())
        privileged["services"]["web"]["privileged"] = True
        with self.assertRaises(ValueError):
            validate_app_spec(privileged)
        mounted = copy.deepcopy(canonical_app_spec())
        mounted["services"]["web"]["volumes"] = [{"name_suffix": "host", "container_mount_path": "/etc"}]
        with self.assertRaises(ValueError):
            validate_app_spec(mounted)

    def test_missing_generator_is_rejected(self):
        raw = copy.deepcopy(canonical_app_spec())
        raw["required_secrets"][0].pop("generator")
        with self.assertRaises(ValueError):
            validate_app_spec(raw)

    def test_supplied_digest_must_be_immutable_sha256(self):
        raw = copy.deepcopy(canonical_app_spec())
        raw["services"]["web"]["pinned_digest"] = "example/web:latest"
        with self.assertRaises(ValueError):
            validate_app_spec(raw)


if __name__ == "__main__":
    unittest.main()
