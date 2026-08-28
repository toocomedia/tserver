from __future__ import annotations

import unittest
from pathlib import Path
import sys

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import config
from database import Base
from models.container_app import ContainerApp
from models.domain import Domain
from services.apps_engine import app_spec_snapshots, snapshots
from services.apps_engine.security_policy import validate_app_spec
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from tests.app_spec_fixtures import canonical_app_spec


class VersionedSnapshotEnvelopeTests(unittest.IsolatedAsyncioTestCase):
    async def test_app_spec_snapshot_uses_v2_envelope(self):
        old_key, old_ephemeral = config.SECRET_KEY, getattr(config, "_SECRET_KEY_EPHEMERAL", False)
        config.SECRET_KEY, config._SECRET_KEY_EPHEMERAL = "test-app-spec-envelope", False
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            async with Session() as db:
                domain = Domain(name="spec.example.test", server_ip="127.0.0.1")
                db.add(domain)
                await db.flush()
                app = ContainerApp(
                    domain_id=domain.id, source_type="image", build_mode="image", deploy_type="app_spec",
                    image_reference="example.test/web:v1", container_name="pending", internal_port=8080,
                    host_port=32008, env_path="/tmp/spec.env",
                )
                db.add(app)
                await db.flush()
                snapshot, _ = await snapshots.create_snapshot(
                    db, app, app_spec=validate_app_spec(canonical_app_spec()), plan_id="plan_envelope",
                )
                self.assertEqual(app_spec_snapshots.runtime_kind(snapshot), "compose")
                self.assertEqual(app_spec_snapshots.app_spec_for(snapshot).name, "evidence_app")
                self.assertEqual(snapshot.state, "pending")
        finally:
            await engine.dispose()
            config.SECRET_KEY, config._SECRET_KEY_EPHEMERAL = old_key, old_ephemeral


if __name__ == "__main__":
    unittest.main()
