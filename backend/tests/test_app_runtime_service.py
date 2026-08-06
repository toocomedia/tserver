import tempfile
import unittest
from pathlib import Path

from models.hosted_app import HostedApp
from services import app_runtime_service


class AppRuntimeServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_supabase_url_uses_detected_async_driver(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            (source / "main.py").write_text(
                "from sqlalchemy.ext.asyncio import create_async_engine\n",
                encoding="utf-8",
            )
            env_path = root / "app.env"
            env_path.write_text(
                "DATABASE_URL=postgresql://app:secret@db.example/app\n",
                encoding="utf-8",
            )
            app = HostedApp(
                id=1, postgres_mode="supabase", env_path=str(env_path),
                work_dir=str(root / "work"), port=9100,
            )
            await app_runtime_service.prepare_environment(app, source)
            self.assertIn(
                "DATABASE_URL=postgresql+asyncpg://app:secret@db.example/app",
                env_path.read_text(encoding="utf-8"),
            )
