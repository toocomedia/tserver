from __future__ import annotations

import unittest
from pathlib import Path
import sys

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import config
from models.container_app import ContainerApp
from models.container_app_snapshot import ContainerAppSnapshot
from services.apps_engine import app_spec_snapshots, secret_vault, snapshot_envelope, snapshot_lifecycle, snapshots
from services.apps_engine.security_policy import validate_app_spec
from tests.app_spec_fixtures import canonical_app_spec


class _SnapshotLookup:
    def __init__(self, old):
        self.old = old

    async def get(self, _model, identifier):
        return self.old if self.old.id == identifier else None


class SnapshotSealingLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_prepared_promotes_and_failure_keeps_previous_active(self):
        old_key, old_ephemeral = config.SECRET_KEY, getattr(config, "_SECRET_KEY_EPHEMERAL", False)
        config.SECRET_KEY, config._SECRET_KEY_EPHEMERAL = "test-app-spec-sealing", False
        try:
            app = ContainerApp(
                id=12, domain_id=1, source_type="image", build_mode="image", deploy_type="app_spec",
                image_reference="example.test/web:v1", container_name="pending", internal_port=8080,
                host_port=32012, env_path="/tmp/spec.env", active_snapshot_id=4,
            )
            old = ContainerAppSnapshot(
                id=4, app_id=12, state="active", configuration_revision=1, source_identity="legacy",
                config_json="{}", environment_encrypted=secret_vault.encrypt("{}"), fingerprint="0" * 64,
            )
            spec = validate_app_spec(canonical_app_spec(pinned=True))
            candidate = ContainerAppSnapshot(
                id=5, app_id=12, state="pending", configuration_revision=2, source_identity="appspec:evidence_app",
                config_json=__import__("json").dumps(snapshot_envelope.compose_envelope(spec), sort_keys=True),
                environment_encrypted=secret_vault.encrypt("{}"), secret_versions_json="{}",
                secret_requirements_json="[]", fingerprint="1" * 64,
            )
            app_spec_snapshots.seal_prepared(candidate)
            self.assertEqual(candidate.state, "prepared")
            runtime = snapshots.runtime_app(app, candidate)
            await snapshot_lifecycle.promote_snapshot(_SnapshotLookup(old), app, candidate, runtime)
            self.assertEqual(candidate.state, "active")
            self.assertEqual(old.state, "superseded")
            self.assertEqual(app.active_snapshot_id, candidate.id)

            failed = ContainerAppSnapshot(
                id=6, app_id=12, state="prepared", configuration_revision=3, source_identity="appspec:evidence_app",
                config_json=candidate.config_json, environment_encrypted=secret_vault.encrypt("{}"), fingerprint="2" * 64,
            )
            await snapshot_lifecycle.mark_failed(failed, "health failed")
            self.assertEqual(failed.state, "failed")
            self.assertEqual(app.active_snapshot_id, candidate.id)
        finally:
            config.SECRET_KEY, config._SECRET_KEY_EPHEMERAL = old_key, old_ephemeral


if __name__ == "__main__":
    unittest.main()
