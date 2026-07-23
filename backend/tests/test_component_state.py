import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base
from models.component_state import ComponentState  # noqa: F401
from services.component_state import ComponentStateStore


class ComponentStatePersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_desired_state_survives_store_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            db_path = Path(temp) / "state.db"
            engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
            session_factory = async_sessionmaker(engine, expire_on_commit=False)
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)

            with patch("services.component_state.AsyncSessionLocal", session_factory):
                first = ComponentStateStore()
                await first.initialize([("plugin", "sample", True, "uploaded")])
                await first.set("plugin", "sample", desired_enabled=False)

                second = ComponentStateStore()
                await second.initialize([("plugin", "sample", True, "uploaded")])
                self.assertFalse(second.get("plugin", "sample").desired_enabled)

            await engine.dispose()


if __name__ == "__main__":
    unittest.main()
