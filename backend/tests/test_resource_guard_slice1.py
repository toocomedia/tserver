"""
Tests for Resource Guard Slice 1 changes.

Run on the VPS:
    cd /path/to/panel/backend
    python -m pytest tests/test_resource_guard_slice1.py -v

No Docker, no network, no live VPS needed — all external calls are mocked.
"""
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base
from models.resource_guard import ResourceGuardSettings
from services.resource_guard_service import ResourceGuardService
from services import resource_guard_profiles as profiles


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_psutil(available_mb: int, ram_percent: float = 50.0, swap_percent: float = 0.0, total_gb: float = 2.0):
    """Return a psutil-like namespace for patching."""
    total = int(total_gb * 1024 ** 3)
    available = available_mb * 1024 * 1024
    used = total - available
    vm = SimpleNamespace(
        percent=ram_percent,
        available=available,
        total=total,
        used=used,
    )
    sm = SimpleNamespace(percent=swap_percent)
    return SimpleNamespace(virtual_memory=lambda: vm, swap_memory=lambda: sm)


# ---------------------------------------------------------------------------
# 1. Resource profiles
# ---------------------------------------------------------------------------

class ProfileDefinitionTests(unittest.TestCase):

    def test_all_profiles_have_required_keys(self):
        required = {"ram_mb", "cpu", "timeout", "label"}
        for name, prof in profiles.PROFILES.items():
            missing = required - prof.keys()
            self.assertFalse(missing, f"Profile '{name}' is missing keys: {missing}")

    def test_ram_budgets_are_positive(self):
        for name, prof in profiles.PROFILES.items():
            self.assertGreater(prof["ram_mb"], 0, f"Profile '{name}' has non-positive ram_mb")

    def test_classify_deployment_git_returns_build_large(self):
        app = SimpleNamespace(source_type="git", build_mode="dockerfile", repository_url="https://github.com/org/nextjs-app", memory_limit_mb=512)
        self.assertEqual("build_large", profiles.classify_deployment(app))

    def test_classify_deployment_image_returns_image_pull(self):
        app = SimpleNamespace(source_type="image", build_mode="image", repository_url=None, memory_limit_mb=512)
        self.assertEqual("image_pull", profiles.classify_deployment(app))

    def test_classify_runtime_buckets(self):
        large = SimpleNamespace(memory_limit_mb=512)
        standard = SimpleNamespace(memory_limit_mb=256)
        small = SimpleNamespace(memory_limit_mb=128)
        self.assertEqual("container_large",    profiles.classify_runtime(large))
        self.assertEqual("container_standard", profiles.classify_runtime(standard))
        self.assertEqual("container_small",    profiles.classify_runtime(small))

    def test_classify_database_known_kinds(self):
        self.assertEqual("database_postgresql", profiles.classify_database("postgresql"))
        self.assertEqual("database_mariadb",    profiles.classify_database("mariadb"))
        self.assertEqual("database_redis",      profiles.classify_database("redis"))
        self.assertEqual("database_mongodb",    profiles.classify_database("mongodb"))

    def test_profile_helper_raises_for_unknown(self):
        with self.assertRaises(KeyError):
            profiles.profile("does_not_exist")


# ---------------------------------------------------------------------------
# 2. ResourceGuardService — sample()
# ---------------------------------------------------------------------------

class SampleTests(unittest.TestCase):

    def test_sample_returns_available_mb(self):
        fake = _make_psutil(available_mb=600, ram_percent=40.0, swap_percent=5.0, total_gb=1.0)
        with patch("services.resource_guard_service.psutil", fake):
            result = ResourceGuardService.sample()
        self.assertEqual(result["ram_available_mb"], 600)
        self.assertEqual(result["ram_percent"], 40.0)
        self.assertEqual(result["swap_percent"], 5.0)

    def test_sample_without_psutil_returns_safe_defaults(self):
        with patch("services.resource_guard_service.psutil", None):
            result = ResourceGuardService.sample()
        self.assertGreater(result["ram_available_mb"], 0)  # safe default
        self.assertEqual(result["ram_percent"], 0.0)

    def test_status_includes_ram_available_mb(self):
        """status() must expose ram_available_mb for the UI."""
        import asyncio
        guard = ResourceGuardService()
        fake = _make_psutil(available_mb=800, ram_percent=20.0, total_gb=1.0)

        async def _run():
            engine = create_async_engine("sqlite+aiosqlite:///:memory:")
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            with patch("services.resource_guard_service.psutil", fake):
                async with sessions() as db:
                    return await guard.status(db)

        status = asyncio.run(_run())
        self.assertIn("ram_available_mb", status)
        self.assertEqual(800, status["ram_available_mb"])
        self.assertIn("protected_reserve_mb", status)


# ---------------------------------------------------------------------------
# 3. Preflight admission
# ---------------------------------------------------------------------------

class PreflightTests(unittest.IsolatedAsyncioTestCase):

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
                          swap_percent: float = 0.0, total_gb: float = 1.0) -> dict:
        fake = _make_psutil(available_mb=available_mb, ram_percent=50.0,
                            swap_percent=swap_percent, total_gb=total_gb)
        with patch("services.resource_guard_service.psutil", fake):
            async with self.sessions() as db:
                return await self.guard.preflight(db, profile_name)

    # --- Capacity pass ---

    async def test_preflight_passes_when_enough_safe_capacity(self):
        # build_large needs 800 MB; available=1500, reserve=400 → safe=1100 ≥ 800
        result = await self._preflight("build_large", available_mb=1500)
        self.assertTrue(result["ok"], result["reason"])

    async def test_preflight_passes_for_image_pull_on_tight_host(self):
        # image_pull needs 100 MB; available=600, reserve=400 → safe=200 ≥ 100
        result = await self._preflight("image_pull", available_mb=600)
        self.assertTrue(result["ok"], result["reason"])

    # --- Capacity fail ---

    async def test_preflight_blocks_when_safe_capacity_insufficient(self):
        # build_large needs 800 MB; available=500, reserve=400 → safe=100 < 800
        result = await self._preflight("build_large", available_mb=500)
        self.assertFalse(result["ok"])
        self.assertIn("Not enough safe memory", result["reason"])
        self.assertIn("ram_available_mb", result)
        self.assertIn("safe_capacity_mb", result)
        self.assertIn("required_mb", result)

    async def test_preflight_blocks_on_1gb_vps_for_large_build(self):
        # 1 GB VPS: available ~300 MB after OS overhead, reserve=400 → safe<0
        result = await self._preflight("build_large", available_mb=300, total_gb=1.0)
        self.assertFalse(result["ok"])
        self.assertIn("short", result["reason"])

    # --- Swap pressure (per-profile threshold) ---

    async def test_preflight_blocks_build_on_critical_swap(self):
        """build_large threshold=80%: blocked at 82% swap."""
        result = await self._preflight("build_large", available_mb=2000, swap_percent=82.0)
        self.assertFalse(result["ok"])
        self.assertIn("Swap pressure", result["reason"])

    async def test_preflight_allows_plugin_install_on_moderate_swap(self):
        """plugin_install threshold=90%: passes at 82% swap, returns swap_warning."""
        result = await self._preflight("plugin_install", available_mb=2000, swap_percent=82.0)
        self.assertTrue(result["ok"])
        self.assertIsNotNone(result.get("swap_warning"),
            "swap_warning must be present when swap >= 60% but below threshold")

    async def test_preflight_allows_image_pull_on_high_swap(self):
        """image_pull threshold=95%: passes at 85% swap (was previously blocked at 80%)."""
        result = await self._preflight("image_pull", available_mb=2000, swap_percent=85.0)
        self.assertTrue(result["ok"])

    async def test_preflight_blocks_image_pull_above_its_threshold(self):
        """image_pull threshold=95%: blocked at 96% swap."""
        result = await self._preflight("image_pull", available_mb=2000, swap_percent=96.0)
        self.assertFalse(result["ok"])
        self.assertIn("Swap pressure", result["reason"])

    async def test_preflight_no_swap_warning_below_60(self):
        """swap_warning is None when swap < 60%."""
        result = await self._preflight("plugin_install", available_mb=2000, swap_percent=55.0)
        self.assertTrue(result["ok"])
        self.assertIsNone(result.get("swap_warning"))

    async def test_preflight_allows_high_but_not_critical_swap(self):
        """build_large passes at 70% swap (below its 80% threshold)."""
        result = await self._preflight("build_large", available_mb=2000, swap_percent=70.0)
        self.assertTrue(result["ok"])

    # --- Build concurrency ---

    async def test_preflight_blocks_second_build_when_one_active(self):
        self.guard.register("container_app", "1", "high", "Build 1",
                            profile="build_large")
        result = await self._preflight("build_large", available_mb=3000)
        self.assertFalse(result["ok"])
        self.assertIn("build is already running", result["reason"])

    async def test_preflight_allows_non_build_while_build_running(self):
        self.guard.register("container_app", "1", "high", "Build 1",
                            profile="build_large")
        # image_pull is not a build_ profile
        result = await self._preflight("image_pull", available_mb=3000)
        self.assertTrue(result["ok"])

    # --- Unknown profile ---

    async def test_preflight_rejects_unknown_profile_name(self):
        result = await self._preflight("totally_fake_profile", available_mb=2000)
        self.assertFalse(result["ok"])
        self.assertIn("Unknown resource profile", result["reason"])

    # --- Disabled mode ---

    async def test_preflight_always_passes_in_disabled_mode(self):
        async with self.sessions() as db:
            await self.guard.save_settings(db, "disabled", 90)
            await db.commit()
        # Even zero available memory
        result = await self._preflight("build_large", available_mb=0)
        self.assertTrue(result["ok"])

    # --- Reservation totals ---

    async def test_active_reservations_reduce_safe_capacity(self):
        # Register a running operation consuming 800 MB
        self.guard.register("container_app", "99", "high", "Existing op",
                            profile="build_large")  # build_large = 800 MB
        # Now try another build_large: available=2000, reserve=400 → safe=1600
        # but 800 already reserved → required = 800+800=1600 → exactly at limit
        # Add one more MB of deficit by using a smaller available
        result = await self._preflight("build_large", available_mb=1999)
        # safe=1599, required=800+800=1600 → should block
        # (1999 - 400 = 1599 < 1600)
        self.assertFalse(result["ok"])

    # --- Protected reserve in response ---

    async def test_preflight_response_includes_all_required_keys(self):
        result = await self._preflight("native_light", available_mb=2000)
        for key in ("ok", "reason", "safe_capacity_mb", "required_mb",
                    "ram_available_mb", "protected_reserve_mb", "profile"):
            self.assertIn(key, result, f"Missing key: {key}")


# ---------------------------------------------------------------------------
# 4. register() profile-aware
# ---------------------------------------------------------------------------

class RegisterTests(unittest.TestCase):

    def test_register_records_profile_and_reserved_mb(self):
        guard = ResourceGuardService()
        token = guard.register("container_app", "1", "high", "Test",
                               profile="build_large")
        op = guard._operations[token]
        self.assertEqual("build_large", op.profile)
        self.assertEqual(800, op.reserved_mb)  # from PROFILES["build_large"]

    def test_register_defaults_to_native_light(self):
        guard = ResourceGuardService()
        token = guard.register("container_app", "1", "high", "Test")
        op = guard._operations[token]
        self.assertEqual("native_light", op.profile)

    def test_active_reservation_total_sums_correctly(self):
        guard = ResourceGuardService()
        guard.register("container_app", "1", "high", "A", profile="build_large")   # 800
        guard.register("container_app", "2", "high", "B", profile="database_postgresql")  # 256
        self.assertEqual(1056, guard._active_reservation_mb())

    def test_unregister_removes_reservation(self):
        guard = ResourceGuardService()
        t1 = guard.register("container_app", "1", "high", "A", profile="build_large")
        t2 = guard.register("container_app", "2", "high", "B", profile="image_pull")
        guard.unregister(t1)
        self.assertEqual(100, guard._active_reservation_mb())  # only image_pull left

    def test_active_builds_counts_only_build_profiles(self):
        guard = ResourceGuardService()
        guard.register("container_app", "1", "high", "A", profile="build_large")
        guard.register("container_app", "2", "high", "B", profile="image_pull")
        guard.register("container_app", "3", "high", "C", profile="database_postgresql")
        self.assertEqual(1, guard._active_builds())


# ---------------------------------------------------------------------------
# 5. Credential safety — database env-file generation
# ---------------------------------------------------------------------------

class CredentialSafetyTests(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.temp.cleanup()

    def _make_db_item(self, kind: str) -> SimpleNamespace:
        cred_path = Path(self.temp.name) / f"{kind}.env"
        # Write base credentials file (as _write_credentials would)
        cred_path.write_text(
            "PASSWORD=secret123\n"
            "ROOT_PASSWORD=rootsecret\n"
            "USERNAME=app_1_pg\n"
            "DATABASE=app_1_pg\n",
            encoding="utf-8",
        )
        return SimpleNamespace(
            id=1,          # required by _write_credentials -> credentials_path(item.id)
            kind=kind,
            credentials_path=str(cred_path),
        )

    def _read_env(self, kind: str) -> dict[str, str]:
        path = Path(self.temp.name) / f"{kind}.env"
        return dict(
            line.split("=", 1)
            for line in path.read_text(encoding="utf-8").splitlines()
            if "=" in line
        )

    def _call_build_env_file(self, item):
        """Import and call _build_docker_env_file from the service."""
        from services.container_app_database_service import _build_docker_env_file
        _build_docker_env_file(item)

    def test_postgresql_env_file_contains_engine_native_vars(self):
        item = self._make_db_item("postgresql")
        self._call_build_env_file(item)
        env = self._read_env("postgresql")
        self.assertIn("POSTGRES_DB",       env, "POSTGRES_DB missing")
        self.assertIn("POSTGRES_USER",     env, "POSTGRES_USER missing")
        self.assertIn("POSTGRES_PASSWORD", env, "POSTGRES_PASSWORD missing")
        # Password value must match
        self.assertEqual("secret123", env["POSTGRES_PASSWORD"])

    def test_mariadb_env_file_contains_engine_native_vars(self):
        item = self._make_db_item("mariadb")
        self._call_build_env_file(item)
        env = self._read_env("mariadb")
        self.assertIn("MYSQL_DATABASE",     env)
        self.assertIn("MYSQL_USER",         env)
        self.assertIn("MYSQL_PASSWORD",     env)
        self.assertIn("MYSQL_ROOT_PASSWORD", env)
        self.assertEqual("secret123",   env["MYSQL_PASSWORD"])
        self.assertEqual("rootsecret",  env["MYSQL_ROOT_PASSWORD"])

    def test_mongodb_env_file_contains_engine_native_vars(self):
        item = self._make_db_item("mongodb")
        self._call_build_env_file(item)
        env = self._read_env("mongodb")
        self.assertIn("MONGO_INITDB_ROOT_USERNAME", env)
        self.assertIn("MONGO_INITDB_ROOT_PASSWORD", env)

    def test_redis_env_file_is_not_modified_extra(self):
        """Redis uses $PASSWORD directly — no extra MYSQL_/POSTGRES_ keys added."""
        item = self._make_db_item("redis")
        self._call_build_env_file(item)
        env = self._read_env("redis")
        # Must still have PASSWORD
        self.assertIn("PASSWORD", env)
        # Must NOT have any postgres or mysql keys
        self.assertNotIn("POSTGRES_PASSWORD", env)
        self.assertNotIn("MYSQL_PASSWORD",    env)

    def test_no_password_in_docker_run_command_for_postgresql(self):
        """
        Verify _provision_docker builds a command list that contains
        --env-file but does NOT contain any password/credential value as a
        -e argument.
        """
        from services import container_app_database_service as dbs

        item = self._make_db_item("postgresql")
        item.container_name = "srv-container-db-1-postgresql"
        item.volume_name = "srv-container-db-data-1"
        item.network_alias = "db-postgresql"
        item.database_name = None
        item.username = None
        item.app_id = 1
        # id is already set by _make_db_item (=1)

        app = SimpleNamespace(id=1)

        captured = []

        def fake_run(cmd, *, timeout):
            captured.append(list(cmd))
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with (
            patch("services.container_app_database_service.dependency_manager") as dm,
            patch("services.container_app_service._run", side_effect=fake_run),
            patch("services.container_app_database_service.container_app_service") as cas,
        ):
            dm.is_healthy.return_value = True
            cas.network_name.return_value = "srv-container-net-1"
            cas._run.side_effect = fake_run

            # Patch _network to no-op
            with patch.object(dbs, "_network", lambda app: None):
                dbs._provision_docker(app, item)

        # Collect all docker run commands
        run_cmds = [c for c in captured if "docker" in c and "run" in c]
        self.assertTrue(run_cmds, "No docker run command was captured")
        for cmd in run_cmds:
            cmd_str = " ".join(cmd)
            # Must use --env-file
            self.assertIn("--env-file", cmd_str, "docker run must use --env-file")
            # Must NOT contain any raw credential values as -e args
            self.assertNotIn("-e POSTGRES_PASSWORD", cmd_str)
            self.assertNotIn("-e POSTGRES_USER",     cmd_str)
            self.assertNotIn("-e POSTGRES_DB",       cmd_str)
            # Scan for -e flags followed by a password-like value
            for i, tok in enumerate(cmd):
                if tok == "-e" and i + 1 < len(cmd):
                    self.assertNotIn("PASSWORD", cmd[i + 1],
                                     f"Credential in -e arg: {cmd[i+1]!r}")
                    self.assertNotIn("secret",   cmd[i + 1].lower(),
                                     f"Secret value in -e arg: {cmd[i+1]!r}")


# ---------------------------------------------------------------------------
# 6. Dockerfile build uses Buildx
# ---------------------------------------------------------------------------

class BuildxRoutingTests(unittest.TestCase):

    def test_dockerfile_build_uses_buildx(self):
        """_build_or_pull must call 'docker buildx build' not 'docker build' for Dockerfile mode."""
        from services import container_app_deployment_service as deploy_svc
        from services import container_app_build_process_service as bps

        app = SimpleNamespace(
            id=1,
            source_type="git",
            build_mode="dockerfile",
            repository_url="https://github.com/org/repo",
            branch="main",
            image_reference=None,
            image_digest=None,
            deployed_revision=None,
        )
        deployment = SimpleNamespace(
            id=42,
            output="",
        )

        captured_commands = []

        def fake_run(dep_id, cmd, timeout, **kwargs):
            captured_commands.append(list(cmd))
            return SimpleNamespace(returncode=0, stdout="sha256:abc", stderr="")

        fake_checkout = SimpleNamespace(revision=SimpleNamespace(sha="abc123"))

        with (
            patch("services.container_app_deployment_service.apps.root",
                  return_value=Path(tempfile.mkdtemp())),
            patch("services.container_app_service._run",
                  return_value=SimpleNamespace(returncode=0, stdout="", stderr="")),
            patch("services.container_app_deployment_service.repository_service") as rs,
            patch("services.container_app_deployment_service.build_process") as bp,
            patch("services.container_app_deployment_service.progress"),
            patch("services.container_app_deployment_service.config") as cfg,
        ):
            rs.clone.return_value = fake_checkout
            bp.run.side_effect = fake_run
            cfg.CONTAINER_APP_BUILD_TIMEOUT = 600
            cfg.BUILDX_BUILDER_NAME = "srv-panel-builder"

            deploy_svc._build_or_pull(app, deployment)

        self.assertTrue(captured_commands, "No build command was run")
        build_cmd = captured_commands[0]
        cmd_str = " ".join(build_cmd)
        self.assertIn("buildx", cmd_str,    "Dockerfile build must use 'docker buildx build'")
        self.assertIn("build",  cmd_str)
        self.assertIn("--builder", cmd_str, "Must specify the panel-owned builder")
        self.assertIn("--load",    cmd_str, "Must use --load to export to local images")
        # Must NOT be a plain 'docker build' (i.e., no 'buildx' token)
        self.assertNotEqual(["docker", "build"], build_cmd[:2],
                            "Must not use plain 'docker build' without buildx")

    def test_railpack_build_does_not_use_buildx_flag(self):
        """Railpack uses its own CLI with BUILDKIT_HOST — no --builder flag."""
        from services import container_app_deployment_service as deploy_svc

        app = SimpleNamespace(
            id=1,
            source_type="git",
            build_mode="railpack",
            repository_url="https://github.com/org/repo",
            branch="main",
            image_reference=None,
            image_digest=None,
            deployed_revision=None,
        )
        deployment = SimpleNamespace(id=42, output="")

        captured_commands = []

        def fake_run(dep_id, cmd, timeout, **kwargs):
            captured_commands.append(list(cmd))
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        fake_checkout = SimpleNamespace(revision=SimpleNamespace(sha="abc123"))

        with (
            patch("services.container_app_deployment_service.apps.root",
                  return_value=Path(tempfile.mkdtemp())),
            patch("services.container_app_deployment_service._ensure_buildkit_daemon"),
            patch("services.container_app_deployment_service.repository_service") as rs,
            patch("services.container_app_deployment_service.build_process") as bp,
            patch("services.container_app_deployment_service.progress"),
            patch("services.container_app_deployment_service.config") as cfg,
        ):
            rs.clone.return_value = fake_checkout
            bp.run.side_effect = fake_run
            cfg.CONTAINER_APP_BUILD_TIMEOUT = 600
            cfg.BUILDX_BUILDER_NAME = "srv-panel-builder"

            deploy_svc._build_or_pull(app, deployment)

        self.assertTrue(captured_commands)
        build_cmd = captured_commands[0]
        self.assertEqual("railpack", build_cmd[0])
        self.assertNotIn("--builder", build_cmd)


# ---------------------------------------------------------------------------
# 7. Source directory cleanup
# ---------------------------------------------------------------------------

class SourceCleanupTests(unittest.IsolatedAsyncioTestCase):

    async def test_source_dir_is_removed_after_successful_build(self):
        import asyncio
        from services import container_app_deployment_service as deploy_svc

        tmp = tempfile.mkdtemp()
        source_path = Path(tmp) / "build" / "1" / "source"
        source_path.mkdir(parents=True)
        (source_path / "Dockerfile").write_text("FROM alpine\n")

        app = SimpleNamespace(
            id=99, source_type="git", build_mode="dockerfile",
            repository_url="https://github.com/org/repo", branch="main",
            image_reference=None, image_digest=None, deployed_revision=None,
        )
        deployment = SimpleNamespace(id=1, output="", stage="")

        def fake_build(dep_id, cmd, timeout, **kwargs):
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        fake_checkout = SimpleNamespace(revision=SimpleNamespace(sha="abc"))

        with (
            patch("services.container_app_deployment_service.apps.root",
                  return_value=Path(tmp)),
            patch("services.container_app_service._run",
                  return_value=SimpleNamespace(returncode=0, stdout="", stderr="")),
            patch("services.container_app_deployment_service.repository_service") as rs,
            patch("services.container_app_deployment_service.build_process") as bp,
            patch("services.container_app_deployment_service.progress"),
            patch("services.container_app_deployment_service.config") as cfg,
        ):
            rs.clone.return_value = fake_checkout
            bp.run.side_effect = fake_build
            cfg.CONTAINER_APP_BUILD_TIMEOUT = 600
            cfg.BUILDX_BUILDER_NAME = "srv-panel-builder"

            async with asyncio.Lock() as _lock:
                # Use AsyncMock for progress.stage so await works
                mock_progress = MagicMock()
                mock_progress.stage = AsyncMock()
                mock_progress.append_log = MagicMock()
                with (
                    patch.object(deploy_svc, "_build_lock", asyncio.Lock()),
                    patch.object(deploy_svc, "progress", mock_progress),
                ):
                    await deploy_svc._prepare_image(None, app, deployment)

        # Source dir must be gone after build
        self.assertFalse(source_path.exists(), "Source directory was not cleaned up after build")


# ---------------------------------------------------------------------------
# 8. Settings — protected_reserve_mb persisted
# ---------------------------------------------------------------------------

class SettingsPersistenceTests(unittest.IsolatedAsyncioTestCase):

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

    async def test_protected_reserve_mb_is_saved_and_loaded(self):
        async with self.sessions() as db:
            await self.guard.save_settings(db, "enabled", 85, protected_reserve_mb=600)
            await db.commit()
        async with self.sessions() as db:
            cfg = await self.guard.settings(db)
        self.assertEqual(600, cfg.protected_reserve_mb)

    async def test_protected_reserve_mb_out_of_range_raises(self):
        async with self.sessions() as db:
            with self.assertRaises(ValueError):
                await self.guard.save_settings(db, "enabled", 85, protected_reserve_mb=50)
            with self.assertRaises(ValueError):
                await self.guard.save_settings(db, "enabled", 85, protected_reserve_mb=9999)

    async def test_default_protected_reserve_is_400(self):
        async with self.sessions() as db:
            cfg = await self.guard.settings(db)
        self.assertEqual(400, cfg.protected_reserve_mb)


if __name__ == "__main__":
    unittest.main(verbosity=2)
