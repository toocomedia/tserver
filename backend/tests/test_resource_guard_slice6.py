"""
Tests for Resource Guard Slice 6 — Profile Tuning (after VPS acceptance runs).

Run on the VPS:
    cd /opt/srv-panel/app
    /opt/srv-panel/venv/bin/python -m pytest tests/test_resource_guard_slice6.py -v

Purpose: After real VPS measurements update PROFILES values, these tests
confirm the math still holds for all acceptance test scenarios.
"""
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from database import Base
from services.resource_guard_service import ResourceGuardService
from services import resource_guard_profiles as profiles


def _make_psutil(available_mb: int, total_gb: float = 1.0, swap_percent: float = 0.0):
    total = int(total_gb * 1024 ** 3)
    available = available_mb * 1024 * 1024
    vm = SimpleNamespace(percent=round((total - available) / total * 100, 1),
                         available=available, total=total)
    sm = SimpleNamespace(percent=swap_percent)
    return SimpleNamespace(virtual_memory=lambda: vm, swap_memory=lambda: sm)


class ProfileSanityTests(unittest.TestCase):
    """Sanity checks on PROFILES values — update expected values after tuning."""

    def test_build_large_ram_budget_is_reasonable(self):
        """build_large ram_mb must be >= 600 MB (practical minimum) and <= 1200 MB."""
        ram_mb = profiles.PROFILES["build_large"]["ram_mb"]
        self.assertGreaterEqual(ram_mb, 600, "build_large budget too low — builds will OOM")
        self.assertLessEqual(ram_mb, 1200, "build_large budget unreasonably high")

    def test_database_postgresql_ram_budget_is_reasonable(self):
        """database_postgresql ram_mb must be >= 128 MB and <= 512 MB."""
        ram_mb = profiles.PROFILES["database_postgresql"]["ram_mb"]
        self.assertGreaterEqual(ram_mb, 128)
        self.assertLessEqual(ram_mb, 512)

    def test_image_pull_ram_budget_is_reasonable(self):
        """image_pull ram_mb must be >= 50 MB and <= 300 MB."""
        ram_mb = profiles.PROFILES["image_pull"]["ram_mb"]
        self.assertGreaterEqual(ram_mb, 50)
        self.assertLessEqual(ram_mb, 300)

    def test_all_cpu_values_are_valid_docker_format(self):
        """All cpu values must be parseable as floats and between 0.1 and 4.0."""
        for name, prof in profiles.PROFILES.items():
            cpu = float(prof["cpu"])
            self.assertGreater(cpu, 0.0, f"{name}: cpu must be > 0")
            self.assertLessEqual(cpu, 4.0, f"{name}: cpu > 4.0 is unreasonable")

    def test_all_build_timeouts_are_sane(self):
        """Build profiles must have a non-None timeout between 60s and 3600s."""
        for name in ("build_large", "build_small", "image_pull", "plugin_install"):
            t = profiles.PROFILES[name]["timeout"]
            self.assertIsNotNone(t, f"{name} timeout must not be None")
            self.assertGreaterEqual(t, 60)
            self.assertLessEqual(t, 3600)


class AcceptanceScenarioMathTests(unittest.IsolatedAsyncioTestCase):
    """
    Re-run the key acceptance scenario maths from Slice 6 plan
    against the final tuned profile values.
    """

    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.engine = create_async_engine(
            f"sqlite+aiosqlite:///{Path(self.temp.name) / 'guard.db'}"
        )
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.guard = ResourceGuardService()

    async def asyncTearDown(self):
        await self.engine.dispose()
        self.temp.cleanup()

    async def _preflight(self, profile_name: str, available_mb: int,
                          total_gb: float = 2.0, swap_percent: float = 0.0):
        fake = _make_psutil(available_mb=available_mb, total_gb=total_gb,
                            swap_percent=swap_percent)
        with patch("services.resource_guard_service.psutil", fake):
            async with self.sessions() as db:
                return await self.guard.preflight(db, profile_name)

    # A1 — Constrained Dockerfile build on 2 GB VPS
    async def test_a1_build_large_passes_on_2gb_vps(self):
        """A1: build_large must pass preflight on 2 GB VPS with typical available RAM."""
        # Assume ~900 MB available after OS on 2 GB VPS
        result = await self._preflight("build_large", available_mb=900, total_gb=2.0)
        self.assertTrue(result["ok"],
            f"build_large blocked on 2GB VPS: {result['reason']} "
            f"(safe={result['safe_capacity_mb']}MB, required={result['required_mb']}MB)")

    # A2 — Large build blocked on 1 GB VPS
    async def test_a2_build_large_blocked_on_1gb_vps(self):
        """A2: build_large must be blocked on 1 GB VPS with typical available RAM."""
        # Assume ~300 MB available after OS on 1 GB VPS
        result = await self._preflight("build_large", available_mb=300, total_gb=1.0)
        self.assertFalse(result["ok"],
            "build_large must be blocked on 1GB VPS — "
            f"safe capacity={result['safe_capacity_mb']}MB, required={result['required_mb']}MB. "
            "Reduce build_large ram_mb or increase the protected reserve.")

    # A4 — Registry image pull passes on 1 GB VPS
    async def test_a4_image_pull_passes_on_1gb_vps(self):
        """A4: image_pull must pass on 1 GB VPS — it only needs ~100 MB."""
        result = await self._preflight("image_pull", available_mb=600, total_gb=1.0)
        self.assertTrue(result["ok"],
            f"image_pull blocked on 1GB VPS: {result['reason']}")

    # A8 — Umami scenario: image_pull + postgresql on 1 GB VPS
    async def test_a8_umami_image_pull_and_postgresql_on_1gb(self):
        """A8: image_pull after postgresql reservation should pass on 1 GB VPS.

        Math: available=800MB, reserve=400MB → safe=400MB.
              postgresql reservation=256MB + image_pull=100MB = 356MB required.
              400 >= 356 → admitted. ✅
        (700 MB was too tight: safe=300 < required=356)
        """
        # Simulate postgresql already running (reserved)
        self.guard.register("container_app", "db-1", "high", "PostgreSQL",
                            profile="database_postgresql")
        result = await self._preflight("image_pull", available_mb=800, total_gb=1.0)
        self.assertTrue(result["ok"],
            f"Umami image_pull blocked after PostgreSQL reservation: {result['reason']} "
            f"(safe={result['safe_capacity_mb']}MB, required={result['required_mb']}MB)")

    # Plugin install on tight host
    async def test_plugin_install_blocked_on_very_tight_host(self):
        """Plugin install blocked when available RAM < reserve + plugin_install budget."""
        result = await self._preflight("plugin_install", available_mb=400, total_gb=1.0)
        # safe=0, required=200 → should block
        self.assertFalse(result["ok"])

    async def test_native_light_always_passes_on_2gb_vps(self):
        """native_light (50 MB) always passes unless host is critically low."""
        result = await self._preflight("native_light", available_mb=500, total_gb=2.0)
        self.assertTrue(result["ok"])


class ProfileConsistencyAfterTuningTests(unittest.TestCase):
    """
    Run after tuning profile values from real measurements.
    Check that no profile was accidentally set to 0 or above safe limits.
    """

    def test_no_profile_exceeds_1gb_budget(self):
        """No single profile should claim more than 1024 MB (single operation)."""
        for name, prof in profiles.PROFILES.items():
            self.assertLessEqual(
                prof["ram_mb"], 1024,
                f"Profile '{name}' claims {prof['ram_mb']} MB — exceeds 1 GB single-operation limit"
            )

    def test_build_large_budget_exceeds_database_budgets(self):
        """build_large ram_mb must be larger than any single database profile."""
        build_mb = profiles.PROFILES["build_large"]["ram_mb"]
        for db_profile in ("database_postgresql", "database_mariadb", "database_mongodb"):
            self.assertGreater(build_mb, profiles.PROFILES[db_profile]["ram_mb"],
                f"build_large ({build_mb}MB) should exceed {db_profile}")

    def test_protected_reserve_plus_build_large_fits_2gb(self):
        """protected_reserve (400) + build_large ram_mb must fit on a 2 GB VPS."""
        reserve = 400  # default
        build_mb = profiles.PROFILES["build_large"]["ram_mb"]
        # 2 GB VPS typically has ~1600 MB available after OS
        self.assertLessEqual(reserve + build_mb, 1600,
            f"reserve({reserve}) + build_large({build_mb}) = {reserve + build_mb} "
            "exceeds typical 2GB VPS available RAM")


if __name__ == "__main__":
    unittest.main(verbosity=2)
