"""Secrets are bound by the approved worker, never by wizard-plan creation."""
from pathlib import Path
import sys
import unittest

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import config
from database import Base
from models.container_app import ContainerApp
from models.domain import Domain
from services.apps_engine import secret_vault, snapshots
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


class TestDeferredSnapshotSecrets(unittest.IsolatedAsyncioTestCase):
    async def test_secret_is_not_created_until_worker_binds_candidate(self):
        old_key, old_ephemeral = config.SECRET_KEY, getattr(config, "_SECRET_KEY_EPHEMERAL", False)
        config.SECRET_KEY, config._SECRET_KEY_EPHEMERAL = "test-deferred-secret-key", False
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            Session = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
            async with Session() as db:
                domain = Domain(name="secrets.example.test", server_ip="127.0.0.1")
                db.add(domain)
                await db.flush()
                app = ContainerApp(
                    domain_id=domain.id, source_type="image", build_mode="image", image_reference="nginx:1.27",
                    container_name="deferred-secret-test", internal_port=80, host_port=32080, env_path="/tmp/deferred-secret.env",
                )
                db.add(app)
                await db.flush()
                snapshot, statuses = await snapshots.create_snapshot(
                    db, app, secret_requirements=[{"key": "APP_SECRET", "purpose": "test", "generator": "urlsafe64"}],
                )
                self.assertEqual(statuses[0]["status"], "pending_approval")
                self.assertEqual(snapshot.secret_versions_json, "{}")
                self.assertNotIn("APP_SECRET", secret_vault.decrypt(snapshot.environment_encrypted))
                await snapshots.bind_deferred_secrets(db, app, snapshot)
                first_versions = snapshot.secret_versions_json
                await snapshots.bind_deferred_secrets(db, app, snapshot)
                self.assertIn("APP_SECRET", secret_vault.decrypt(snapshot.environment_encrypted))
                self.assertIn("APP_SECRET", first_versions)
                self.assertEqual(snapshot.secret_versions_json, first_versions)
        finally:
            await engine.dispose()
            config.SECRET_KEY, config._SECRET_KEY_EPHEMERAL = old_key, old_ephemeral


if __name__ == "__main__":
    unittest.main()
