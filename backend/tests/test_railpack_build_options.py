"""Unit tests for Apps Engine build options, Git reference controls, deploy keys, and storage mounts."""
import asyncio
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import config
from fastapi import HTTPException
from dependencies.git import repository_service
from models.container_app import ContainerApp
from services import (
    container_app_cleanup_service,
    container_app_deployment_progress_service,
    container_app_deployment_service,
    container_app_removal_service,
    container_app_service,
)


class RailpackBuildOptionsTests(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="srv-test-build-opts-")
        self.orig_deploy_root = config.DEPLOY_KEY_ROOT
        self.orig_known_hosts = config.KNOWN_HOSTS_PATH
        config.DEPLOY_KEY_ROOT = os.path.join(self.temp_dir, "deploy-keys")
        config.KNOWN_HOSTS_PATH = os.path.join(self.temp_dir, "known_hosts")

    def tearDown(self):
        config.DEPLOY_KEY_ROOT = self.orig_deploy_root
        config.KNOWN_HOSTS_PATH = self.orig_known_hosts
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_draft_key_strict_validation_and_path_containment(self):
        # 1. Traversal attempt
        for bad_id in ("../7", "../../etc/passwd", "a" * 31, "a" * 33, "not_hex_at_all!", ""):
            with self.assertRaises(HTTPException):
                repository_service.validate_and_resolve_draft_dir(bad_id)

        # 2. Valid 32-hex ID
        valid_id = "0123456789abcdef0123456789abcdef"
        resolved = repository_service.validate_and_resolve_draft_dir(valid_id)
        drafts_root = (Path(config.DEPLOY_KEY_ROOT) / "drafts").resolve()
        self.assertEqual(resolved, drafts_root / valid_id)

    def test_cleanup_expired_drafts(self):
        drafts_root = (Path(config.DEPLOY_KEY_ROOT) / "drafts").resolve()
        old_id = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        old_dir = drafts_root / old_id
        old_dir.mkdir(parents=True, exist_ok=True)
        # Set mtime to 2 hours ago
        past_time = os.path.getmtime(old_dir) - 7200
        os.utime(old_dir, (past_time, past_time))

        repository_service.cleanup_expired_drafts(max_age_seconds=3600)
        self.assertFalse(old_dir.exists())

    def test_inspect_repository_and_branches_accept_ssh_key_path(self):
        from services import container_app_inspection_service
        key_path = Path(self.temp_dir) / "id_ed25519"
        key_path.write_text("KEY", encoding="utf-8")

        with patch.object(repository_service, "temporary_clone") as mock_clone:
            mock_checkout = Mock(path=Path(self.temp_dir), repository_url="git@github.com:org/repo.git", branch="main")
            mock_clone.return_value.__enter__.return_value = mock_checkout

            container_app_inspection_service.inspect_repository(
                "git@github.com:org/repo.git", "main", ssh_key_path=key_path,
            )
            mock_clone.assert_called_once()
            self.assertEqual(mock_clone.call_args[1]["ssh_key_path"], key_path)

        with patch.object(repository_service, "_run") as mock_run:
            mock_run.return_value = Mock(returncode=0, stdout="ref: refs/heads/main\tHEAD\nrefs/heads/main\n", stderr="")
            branches = repository_service.list_branches("git@github.com:org/repo.git", ssh_key_path=key_path)
            self.assertEqual(branches.default_branch, "main")

    def test_file_manager_discovers_storage_mounts(self):
        from plugins.file_manager import file_service
        app = Mock(
            id=7,
            preset=None,
            storage_mounts=json.dumps([
                {"label": "uploads", "volume": "srv-container-app-7-vol-uploads", "mount_path": "/app/uploads"},
                {"label": "cache", "volume": "srv-container-app-7-vol-cache", "mount_path": "/var/cache"},
            ]),
            data_volume=None,
            data_mount_path=None,
            wordpress_content_volume=None,
            env_path=str(container_app_service.env_path(7)),
        )
        container = {
            "Config": {"WorkingDir": "/app"},
            "Mounts": [
                {"Type": "volume", "Name": "srv-container-app-7-vol-uploads", "Destination": "/app/uploads"},
                {"Type": "volume", "Name": "srv-container-app-7-vol-cache", "Destination": "/var/cache"},
            ],
        }
        roots = file_service._roots(app, container)
        root_ids = [r.id for r in roots]
        self.assertIn("application", root_ids)
        self.assertIn("storage-uploads", root_ids)
        self.assertIn("storage-cache", root_ids)
        self.assertIn("runtime-env", root_ids)

    def test_validate_custom_start_command(self):
        # Valid commands
        self.assertEqual(
            container_app_service.validate_custom_start_command("gunicorn -w 4 app:app"),
            "gunicorn -w 4 app:app",
        )
        self.assertIsNone(container_app_service.validate_custom_start_command(""))
        self.assertIsNone(container_app_service.validate_custom_start_command(None))

        # Unclosed quotes
        with self.assertRaises(HTTPException):
            container_app_service.validate_custom_start_command('gunicorn "unclosed quote')

    def test_deployment_fails_on_volume_create_failure_or_invalid_mount_json(self):
        app_bad_json = Mock(
            id=8,
            container_name="srv-container-app-8",
            host_port=31008,
            internal_port=3000,
            memory_limit_mb=512,
            cpu_limit="1.0",
            pid_limit=256,
            env_path="/var/lib/srv-panel/container-app-env/8.env",
            storage_mounts="[invalid json",
            preset=None,
            custom_start_command=None,
        )
        with patch.object(container_app_deployment_service, "_ensure_network"), \
             patch.object(container_app_service, "_run", return_value=Mock(returncode=0, stdout="", stderr="")):
            with self.assertRaises(RuntimeError):
                container_app_deployment_service._replace_container(app_bad_json, "image:1.0")

        app_failed_vol = Mock(
            id=9,
            container_name="srv-container-app-9",
            host_port=31009,
            internal_port=3000,
            memory_limit_mb=512,
            cpu_limit="1.0",
            pid_limit=256,
            env_path="/var/lib/srv-panel/container-app-env/9.env",
            storage_mounts=json.dumps([{"label": "data", "volume": "vol-fail", "mount_path": "/data"}]),
            preset=None,
            custom_start_command=None,
        )
        with patch.object(container_app_deployment_service, "_ensure_network"), \
             patch.object(container_app_service, "_run") as mock_run:
            mock_run.return_value = Mock(returncode=1, stdout="", stderr="Disk error")
            with self.assertRaises(RuntimeError):
                container_app_deployment_service._replace_container(app_failed_vol, "image:1.0")

    def test_parse_storage_mounts_rejects_nested_overlaps(self):
        # Parent / Child overlap
        with self.assertRaises(HTTPException):
            container_app_service.parse_storage_mounts(1, [
                {"label": "app_root", "mount_path": "/app"},
                {"label": "app_uploads", "mount_path": "/app/uploads"},
            ])

        with self.assertRaises(HTTPException):
            container_app_service.parse_storage_mounts(1, [
                {"label": "app_uploads", "mount_path": "/var/data/uploads"},
                {"label": "app_data", "mount_path": "/var/data"},
            ])

        # Siblings should be accepted
        res = container_app_service.parse_storage_mounts(1, [
            {"label": "uploads", "mount_path": "/var/data/uploads"},
            {"label": "cache", "mount_path": "/var/data_cache"},
        ])
        self.assertIsNotNone(res)

    def test_deploy_key_requires_ssh_repository_url(self):
        domain = Mock(project_type="static")
        with patch("dependencies.dependency_manager.is_healthy", return_value=True), \
             patch.object(repository_service, "validate_source"):
            # HTTPS with deploy key should fail
            with self.assertRaises(HTTPException):
                container_app_service._validate_source(
                    domain, "git", "railpack", "https://github.com/org/repo.git",
                    "main", None, has_deploy_key=True,
                )

            # SSH with deploy key should succeed
            container_app_service._validate_source(
                domain, "git", "railpack", "git@github.com:org/repo.git",
                "main", None, has_deploy_key=True,
            )

    def test_create_app_compensating_cleanup_on_failure(self):
        async def run_test():
            db = AsyncMock()
            db.scalar.return_value = None
            scalars_mock = Mock()
            scalars_mock.all.return_value = []
            db.scalars = AsyncMock(return_value=scalars_mock)
            domain = Mock(id=1, name="example.test", project_type="static")

            with patch("dependencies.dependency_manager.is_healthy", return_value=True), \
                 patch.object(container_app_service, "next_host_port", new=AsyncMock(return_value=31001)), \
                 patch.object(repository_service, "validate_source"), \
                 patch("services.resource_guard_service.resource_guard_service.preflight", new=AsyncMock(return_value={"ok": True})), \
                 patch.object(repository_service, "attach_deploy_key", return_value=("ssh-ed25519 pub", Path("/fake/key"))), \
                 patch.object(repository_service, "delete_deploy_key") as mock_delete_key, \
                 patch("services.container_app_database_service.create_attachments", side_effect=RuntimeError("DB creation failed")):

                with self.assertRaises(RuntimeError):
                    await container_app_service.create_app(
                        db, domain=domain, source_type="git", build_mode="railpack",
                        repository_url="git@github.com:org/repo.git", branch="main", image_reference=None,
                        internal_port=3000, ssl_requested=False, environment_values={},
                        draft_key_id="0123456789abcdef0123456789abcdef",
                    )

                mock_delete_key.assert_called_once()

        asyncio.run(run_test())

    def test_resolve_session_draft_key_strict_session_requirement(self):
        from plugins.railpack_apps import router_create
        valid_id = "0123456789abcdef0123456789abcdef"

        # Missing session list
        req1 = Mock(session={})
        with self.assertRaises(HTTPException):
            router_create._resolve_session_draft_key(req1, valid_id)

        # ID not in session list
        req2 = Mock(session={"draft_deploy_keys": ["ffffffffffffffffffffffffffffffff"]})
        with self.assertRaises(HTTPException):
            router_create._resolve_session_draft_key(req2, valid_id)

        # ID in session list
        req3 = Mock(session={"draft_deploy_keys": [valid_id]})
        with patch.object(repository_service, "get_draft_deploy_key_path", return_value=Path("/fake/path")):
            path = router_create._resolve_session_draft_key(req3, valid_id)
            self.assertEqual(path, Path("/fake/path"))

    def test_ensure_known_hosts_creates_file(self):
        hosts_file = repository_service.ensure_known_hosts()
        self.assertTrue(hosts_file.exists())
        content = hosts_file.read_text(encoding="utf-8")
        self.assertIn("github.com", content)
        self.assertIn("gitlab.com", content)
        self.assertIn("bitbucket.org", content)

    def test_deploy_key_lifecycle(self):
        # 1. Draft Key Creation
        with patch.object(repository_service, "_run") as mock_run:
            mock_run.return_value = Mock(returncode=0, stdout="", stderr="")

            # Mock key files creation
            def side_effect(cmd, timeout=30, env=None):
                if cmd[0] == "ssh-keygen":
                    out_path = Path(cmd[cmd.index("-f") + 1])
                    out_path.parent.mkdir(parents=True, exist_ok=True)
                    out_path.write_text("PRIVATE_KEY", encoding="utf-8")
                    out_path.with_suffix(".pub").write_text("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5... test@srv", encoding="utf-8")
                    return Mock(returncode=0, stdout="", stderr="")
                return Mock(returncode=0, stdout="", stderr="")

            mock_run.side_effect = side_effect

            draft_id, pub_key = repository_service.create_draft_deploy_key()
            self.assertTrue(pub_key.startswith("ssh-ed25519"))

            # 2. Attach Draft Key to App
            attached_pub, key_path = repository_service.attach_deploy_key(draft_id, 42)
            self.assertEqual(attached_pub, pub_key)
            self.assertTrue(key_path.exists())
            self.assertEqual(key_path.read_text(encoding="utf-8"), "PRIVATE_KEY")

            # Verify draft folder cleaned up
            draft_folder = Path(config.DEPLOY_KEY_ROOT) / "drafts" / draft_id
            self.assertFalse(draft_folder.exists())

            # 3. Delete Deploy Key
            repository_service.delete_deploy_key(42)
            self.assertFalse(key_path.parent.exists())

    def test_validate_source_by_ref_type(self):
        # Branch
        repository_service.validate_source("https://github.com/org/repo.git", "main", "branch")
        repository_service.validate_source("https://github.com/org/repo.git", "feature/login-v2", "branch")

        # Tag
        repository_service.validate_source("https://github.com/org/repo.git", "v1.0.0", "tag")

        # Commit SHA
        repository_service.validate_source("https://github.com/org/repo.git", "a1b2c3d4e5f6789012345678901234567890abcd", "commit")
        repository_service.validate_source("https://github.com/org/repo.git", "a1b2c3d", "commit")

        # Invalid commit SHA
        with self.assertRaises(HTTPException):
            repository_service.validate_source("https://github.com/org/repo.git", "not-a-sha!", "commit")

        # Invalid branch name
        with self.assertRaises(HTTPException):
            repository_service.validate_source("https://github.com/org/repo.git", "bad branch name", "branch")

        with self.assertRaises(HTTPException):
            repository_service.validate_source("https://github.com/org/repo.git", "main", "unknown")

    def test_clone_commit_sha_strategy(self):
        target = Path(self.temp_dir) / "checkout"
        sha = "a1b2c3d4e5f6789012345678901234567890abcd"

        with patch.object(repository_service, "_run") as mock_run, \
             patch.object(repository_service, "_revision") as mock_rev:
            mock_run.return_value = Mock(returncode=0, stdout="", stderr="")
            mock_rev.return_value = Mock(sha=sha, message="Commit test", committed_at=None)

            checkout = repository_service.clone(
                "https://github.com/org/repo.git",
                sha,
                target,
                git_ref_type="commit",
            )
            self.assertEqual(checkout.branch, sha)
            self.assertEqual(checkout.revision.sha, sha)

            # Ensure git clone + git fetch sha + git checkout detach sha were invoked
            calls = [c[0][0] for c in mock_run.call_args_list]
            self.assertTrue(any("clone" in cmd and "--depth" in cmd for cmd in calls))
            self.assertTrue(any("fetch" in cmd and sha in cmd for cmd in calls))
            self.assertTrue(any("checkout" in cmd and "--detach" in cmd and sha in cmd for cmd in calls))

    def test_git_ssh_command_environment(self):
        key_path = Path(self.temp_dir) / "id_ed25519"
        key_path.write_text("KEY", encoding="utf-8")
        env = repository_service._git_env(key_path)
        self.assertIsNotNone(env)
        self.assertIn("GIT_SSH_COMMAND", env)
        self.assertIn("IdentitiesOnly=yes", env["GIT_SSH_COMMAND"])
        self.assertIn("UserKnownHostsFile=", env["GIT_SSH_COMMAND"])

    def test_parse_storage_mounts_server_owned_volumes(self):
        raw = [
            {"label": "uploads", "mount_path": "/app/uploads"},
            {"label": "cache_data", "mount_path": "/var/cache/app"},
        ]
        result = container_app_service.parse_storage_mounts(99, raw)
        parsed = json.loads(result)
        self.assertEqual(len(parsed), 2)
        self.assertEqual(parsed[0]["volume"], "srv-container-app-99-vol-uploads")
        self.assertEqual(parsed[0]["mount_path"], "/app/uploads")
        self.assertEqual(parsed[1]["volume"], "srv-container-app-99-vol-cache_data")
        self.assertEqual(parsed[1]["mount_path"], "/var/cache/app")

    def test_parse_storage_mounts_validations(self):
        # Invalid label
        with self.assertRaises(HTTPException):
            container_app_service.parse_storage_mounts(1, [{"label": "Invalid Label!", "mount_path": "/app/data"}])

        # Relative path
        with self.assertRaises(HTTPException):
            container_app_service.parse_storage_mounts(1, [{"label": "data", "mount_path": "app/data"}])

        # Path traversal
        with self.assertRaises(HTTPException):
            container_app_service.parse_storage_mounts(1, [{"label": "data", "mount_path": "/app/../etc"}])

        # Forbidden root
        with self.assertRaises(HTTPException):
            container_app_service.parse_storage_mounts(1, [{"label": "root", "mount_path": "/"}])

        # Duplicate labels
        with self.assertRaises(HTTPException):
            container_app_service.parse_storage_mounts(1, [
                {"label": "data", "mount_path": "/app/data1"},
                {"label": "data", "mount_path": "/app/data2"},
            ])

    def test_validate_build_and_runtime_helpers(self):
        # Root directory
        self.assertEqual(container_app_service.validate_root_directory("frontend/sub"), "frontend/sub")
        self.assertEqual(container_app_service.validate_root_directory("/"), "")
        with self.assertRaises(HTTPException):
            container_app_service.validate_root_directory("../outside")

        # Dockerfile path
        self.assertEqual(container_app_service.validate_dockerfile_path("docker/Dockerfile.prod"), "docker/Dockerfile.prod")
        with self.assertRaises(HTTPException):
            container_app_service.validate_dockerfile_path("../Dockerfile")

        # Health path
        self.assertEqual(container_app_service.validate_health_path("/healthz"), "/healthz")
        with self.assertRaises(HTTPException):
            container_app_service.validate_health_path("not-a-slash")

        # Startup timeout
        self.assertEqual(container_app_service.validate_startup_timeout(60), 60)
        with self.assertRaises(HTTPException):
            container_app_service.validate_startup_timeout(5)  # < 10
        with self.assertRaises(HTTPException):
            container_app_service.validate_startup_timeout(350)  # > 300

        # Build args
        self.assertEqual(
            json.loads(container_app_service.parse_build_args('{"API_URL": "https://api.test"}')),
            {"API_URL": "https://api.test"},
        )
        with self.assertRaises(HTTPException):
            container_app_service.parse_build_args('{"bad arg name!": "val"}')

    def test_dockerfile_build_includes_build_args_and_custom_dockerfile(self):
        app = Mock(
            id=10,
            source_type="git",
            build_mode="dockerfile",
            repository_url="https://github.com/org/repo.git",
            branch="main",
            git_ref="main",
            git_ref_type="branch",
            deploy_key_path=None,
            root_directory="backend",
            dockerfile_path="Dockerfile.prod",
            build_args=json.dumps({"ENV": "prod", "VER": "2"}),
        )
        deployment = Mock(id=1, output="")

        def fake_clone(url, branch, target, **kwargs):
            backend_dir = target / "backend"
            backend_dir.mkdir(parents=True, exist_ok=True)
            (backend_dir / "Dockerfile.prod").write_text("FROM alpine", encoding="utf-8")
            return Mock(revision=Mock(sha="abcdef123456"))

        with patch.object(container_app_service, "root", return_value=Path(self.temp_dir)), \
             patch.object(container_app_service, "_run", return_value=Mock(returncode=0)), \
             patch.object(repository_service, "clone", side_effect=fake_clone), \
             patch("services.container_app_build_process_service.run") as mock_run:

            mock_run.return_value = Mock(returncode=0, stdout="Build complete", stderr="")

            image = container_app_deployment_service._build_or_pull(app, deployment)
            self.assertEqual(image, "srv-panel/railpack-app:10-1")

            build_cmd = mock_run.call_args[0][1]
            self.assertIn("docker", build_cmd)
            self.assertIn("buildx", build_cmd)
            self.assertIn("-f", build_cmd)
            self.assertTrue(any("Dockerfile.prod" in item for item in build_cmd))
            self.assertIn("--build-arg", build_cmd)
            self.assertIn("ENV=prod", build_cmd)
            self.assertIn("VER=2", build_cmd)

    def test_cleanup_and_removal_deletes_storage_mounts_and_deploy_keys(self):
        app = Mock(
            id=12,
            data_volume="srv-container-app-12-vol-data",
            data_mount_path="/data",
            storage_mounts=json.dumps([
                {"label": "media", "volume": "srv-container-app-12-vol-media", "mount_path": "/media"},
            ]),
            wordpress_content_volume=None,
        )

        async def run_removal():
            db = AsyncMock()
            scalars_mock = Mock()
            scalars_mock.all.return_value = []
            db.scalars = AsyncMock(return_value=scalars_mock)
            db.scalar = AsyncMock(return_value=None)

            with patch.object(container_app_cleanup_service, "list_app_storage_volumes", new=AsyncMock(return_value=["srv-container-app-12-vol-detached"])), \
                 patch.object(container_app_cleanup_service, "remove_volume") as mock_rm_vol, \
                 patch.object(container_app_cleanup_service, "remove_private_network"):

                await container_app_removal_service.remove_selected_data(
                    db,
                    app,
                    [],
                    database_ids=[],
                    delete_app_volume=True,
                    delete_wordpress_files=False,
                    delete_backups=False,
                )

                removed_volumes = [c[0][0] for c in mock_rm_vol.call_args_list]
                self.assertIn("srv-container-app-12-vol-data", removed_volumes)
                self.assertIn("srv-container-app-12-vol-media", removed_volumes)
                self.assertIn("srv-container-app-12-vol-detached", removed_volumes)

        asyncio.run(run_removal())

    def test_replace_container_with_mounts_and_custom_start_command(self):
        app = Mock(
            id=5,
            container_name="srv-container-app-5",
            host_port=31005,
            internal_port=3000,
            memory_limit_mb=512,
            cpu_limit="1.0",
            pid_limit=256,
            env_path="/var/lib/srv-panel/container-app-env/5.env",
            storage_mounts=json.dumps([
                {"label": "uploads", "volume": "srv-container-app-5-vol-uploads", "mount_path": "/app/uploads"},
            ]),
            data_volume=None,
            data_mount_path=None,
            preset=None,
            wordpress_content_volume=None,
            custom_start_command="gunicorn -w 2 -b 0.0.0.0:3000 app:main",
        )

        with patch.object(container_app_service, "_run") as mock_run, \
             patch.object(container_app_deployment_service, "_ensure_network"):
            mock_run.return_value = Mock(returncode=0, stdout="", stderr="")

            container_app_deployment_service._replace_container(app, "my-image:latest")

            # Check docker volume create and docker run commands
            calls = [c[0][0] for c in mock_run.call_args_list]
            vol_create_call = next(c for c in calls if "volume" in c and "create" in c)
            self.assertIn("srv-container-app-5-vol-uploads", vol_create_call)

            run_call = next(c for c in calls if "run" in c and "-d" in c)
            self.assertIn("-v", run_call)
            self.assertIn("srv-container-app-5-vol-uploads:/app/uploads", run_call)
            self.assertIn("my-image:latest", run_call)
            # Custom start command tokens
            self.assertIn("gunicorn", run_call)
            self.assertIn("-w", run_call)
            self.assertIn("2", run_call)
            self.assertIn("app:main", run_call)

    def test_wait_for_http_custom_path_and_status_codes(self):
        async def run_test():
            # Test success on 200
            async def fake_open_connection(host, port):
                reader = AsyncMock()
                reader.readline = AsyncMock(return_value=b"HTTP/1.1 200 OK\r\n")
                writer = Mock()
                writer.write = Mock()
                writer.drain = AsyncMock()
                writer.close = Mock()
                writer.wait_closed = AsyncMock()
                return reader, writer

            with patch("asyncio.open_connection", side_effect=fake_open_connection):
                await container_app_deployment_progress_service.wait_for_http(31000, path="/health", timeout_seconds=10)

            # Test failure on 500 error timeout
            async def fake_open_connection_500(host, port):
                reader = AsyncMock()
                reader.readline = AsyncMock(return_value=b"HTTP/1.1 500 Internal Server Error\r\n")
                writer = Mock()
                writer.write = Mock()
                writer.drain = AsyncMock()
                writer.close = Mock()
                writer.wait_closed = AsyncMock()
                return reader, writer

            with patch("asyncio.open_connection", side_effect=fake_open_connection_500), \
                 patch("asyncio.sleep", new=AsyncMock()):
                with self.assertRaises(RuntimeError):
                    await container_app_deployment_progress_service.wait_for_http(31000, path="/api/health", timeout_seconds=0.01)

        asyncio.run(run_test())

    def test_database_migration_adds_new_columns_and_backfills(self):
        import sqlite3
        from sqlalchemy import create_engine
        from database import _migrate_sync

        db_path = Path(self.temp_dir) / "test_migration.db"
        raw_conn = sqlite3.connect(db_path)
        # Create legacy container_apps table
        raw_conn.execute("""
            CREATE TABLE container_apps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                domain_id INTEGER,
                source_type VARCHAR(16),
                build_mode VARCHAR(16),
                repository_url VARCHAR(512),
                branch VARCHAR(128),
                image_reference VARCHAR(512),
                container_name VARCHAR(128),
                internal_port INTEGER,
                host_port INTEGER,
                env_path VARCHAR(512),
                data_volume VARCHAR(128),
                data_mount_path VARCHAR(512)
            )
        """)
        raw_conn.execute("""
            INSERT INTO container_apps (id, domain_id, source_type, build_mode, repository_url, branch, container_name, internal_port, host_port, env_path, data_volume, data_mount_path)
            VALUES (1, 1, 'git', 'railpack', 'https://github.com/org/app.git', 'develop', 'srv-container-app-1', 3000, 31001, '/env/1.env', 'srv-container-app-1-vol-data', '/app/data')
        """)
        raw_conn.commit()
        raw_conn.close()

        # Run migration on SQLAlchemy connection
        engine = create_engine(f"sqlite:///{db_path.as_posix()}")
        with engine.connect() as sync_conn:
            _migrate_sync(sync_conn)
            sync_conn.commit()
        engine.dispose()

        # Verify new columns exist and data is backfilled
        verify_conn = sqlite3.connect(db_path)
        cursor = verify_conn.cursor()
        cursor.execute("PRAGMA table_info(container_apps)")
        cols = {row[1] for row in cursor.fetchall()}
        self.assertIn("git_ref", cols)
        self.assertIn("git_ref_type", cols)
        self.assertIn("deploy_key_path", cols)
        self.assertIn("root_directory", cols)
        self.assertIn("dockerfile_path", cols)
        self.assertIn("storage_mounts", cols)
        self.assertIn("health_path", cols)
        self.assertIn("startup_timeout_seconds", cols)

        cursor.execute("SELECT git_ref, git_ref_type, storage_mounts FROM container_apps WHERE id = 1")
        row = cursor.fetchone()
        self.assertEqual(row[0], "develop")  # git_ref backfilled from branch
        self.assertEqual(row[1], "branch")
        mounts = json.loads(row[2])
        self.assertEqual(len(mounts), 1)
        self.assertEqual(mounts[0]["volume"], "srv-container-app-1-vol-data")
        self.assertEqual(mounts[0]["mount_path"], "/app/data")
        verify_conn.close()

    def test_railpack_build_passes_secrets_via_railpack_json_and_env_dict(self):
        env_file = Path(self.temp_dir) / "app.env"
        env_file.write_text("DATABASE_URL=postgresql://user:pass@127.0.0.1:5432/appdb\nAPP_SECRET=topsecret\n", encoding="utf-8")

        app = Mock(
            id=15,
            source_type="git",
            build_mode="railpack",
            repository_url="https://github.com/org/umami",
            branch="main",
            git_ref="main",
            git_ref_type="branch",
            deploy_key_path=None,
            root_directory="",
            env_path=str(env_file),
            build_args=None,
        )
        deployment = Mock(id=2, output="")

        def fake_clone(url, branch, target, **kwargs):
            target.mkdir(parents=True, exist_ok=True)
            (target / "package.json").write_text('{"name": "umami"}', encoding="utf-8")
            return Mock(revision=Mock(sha="umami123456"))

        with patch.object(container_app_service, "root", return_value=Path(self.temp_dir)), \
             patch.object(container_app_deployment_service, "_ensure_buildkit_daemon"), \
             patch.object(repository_service, "clone", side_effect=fake_clone), \
             patch("services.container_app_build_process_service.run") as mock_run:

            mock_run.return_value = Mock(returncode=0, stdout="Railpack build success", stderr="")

            image = container_app_deployment_service._build_or_pull(app, deployment)
            self.assertEqual(image, "srv-panel/railpack-app:15-2")

            # Check CLI arguments do not leak secrets on process arguments
            build_cmd = mock_run.call_args[0][1]
            self.assertEqual(build_cmd[0], "railpack")
            self.assertEqual(build_cmd[1], "build")
            self.assertEqual(build_cmd[2], "--name")
            self.assertEqual(build_cmd[3], "srv-panel/railpack-app:15-2")

            # Check railpack.json written with secrets declared by name
            source_dir = Path(self.temp_dir) / "build" / "2" / "source"
            railpack_json_file = source_dir / "railpack.json"
            self.assertTrue(railpack_json_file.exists())
            railpack_config = json.loads(railpack_json_file.read_text(encoding="utf-8"))
            self.assertIn("DATABASE_URL", railpack_config["secrets"])
            self.assertIn("APP_SECRET", railpack_config["secrets"])

            # Check env passed safely to subprocess environment
            passed_env = mock_run.call_args[1].get("env")
            self.assertIsNotNone(passed_env)
            self.assertEqual(passed_env["DATABASE_URL"], "postgresql://user:pass@127.0.0.1:5432/appdb")
            self.assertEqual(passed_env["APP_SECRET"], "topsecret")

    def test_ensure_buildx_builder_raises_descriptive_repair_error_when_creation_fails(self):
        with patch.object(container_app_service, "_run") as mock_run:
            mock_run.return_value = Mock(returncode=1, stdout="", stderr="Error: docker-container driver not supported")
            with self.assertRaises(RuntimeError) as ctx:
                container_app_deployment_service._ensure_buildx_builder("srv-panel-builder")
            self.assertIn("srv-panel-builder", str(ctx.exception))
            self.assertIn("Repair with: docker buildx create", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
