import asyncio
import json
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from fastapi import HTTPException
from starlette.requests import Request

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from models.file_manager_event import FileManagerEvent
from plugins.file_manager import audit, file_service, file_targets, host_file_service, router
from plugins.manager import PluginManager
from services import container_app_deployment_service, container_app_service


def app(**changes):
    values = {
        "id": 7,
        "container_name": "srv-container-app-7",
        "status": "running",
        "preset": None,
        "data_volume": None,
        "data_mount_path": None,
        "wordpress_content_volume": None,
        "env_path": str(container_app_service.env_path(7)),
    }
    values.update(changes)
    return SimpleNamespace(**values)


def container(*, workdir="/app", labels=None, mounts=None):
    return {
        "Config": {"WorkingDir": workdir, "Labels": labels or {
            "srv-panel.plugin": "railpack_apps", "srv-panel.app-id": "7",
        }},
        "State": {"Running": True},
        "Mounts": mounts or [],
    }


class FileManagerPathTests(unittest.TestCase):
    def test_plugin_manifest_and_api_router_are_valid(self):
        plugin_dir = BACKEND / "plugins" / "file_manager"
        manifest = json.loads((plugin_dir / "plugin.json").read_text(encoding="utf-8"))
        self.assertIsNone(PluginManager()._validate_manifest(manifest, plugin_dir))
        self.assertEqual(router.router.prefix, "/plugins/file_manager")
        self.assertTrue(manifest["sidebar"])
        self.assertTrue(manifest["route_dependency_checks"])

    def test_relative_paths_cannot_escape_root(self):
        for value in ("../etc", "/etc", "folder/../secret", "folder\\secret", "\x00bad"):
            with self.assertRaises(HTTPException):
                file_service.validate_relative_path(value)
        self.assertEqual(file_service.validate_relative_path(".env"), ".env")
        self.assertEqual(file_service.validate_relative_path("storage/uploads"), "storage/uploads")

    def test_container_roots_are_scoped_to_workdir_and_managed_volumes(self):
        item = app(data_volume="srv-container-data-7", data_mount_path="/data")
        roots = file_service._roots(item, container(mounts=[{
            "Type": "volume", "Name": "srv-container-data-7", "Destination": "/data",
        }]))
        self.assertEqual([root.id for root in roots], ["application", "data", "runtime-env"])
        self.assertFalse(roots[0].persistent)
        self.assertTrue(roots[1].persistent)

    def test_normal_app_without_safe_workdir_has_no_application_root(self):
        roots = file_service._roots(app(), container(workdir="/"))
        self.assertNotIn("application", [root.id for root in roots])

    def test_docker_inspect_requires_panel_ownership_labels(self):
        item = app()
        bad = container(labels={"srv-panel.plugin": "other", "srv-panel.app-id": "7"})
        with patch.object(container_app_service, "_run", return_value=Mock(
            returncode=0, stdout=json.dumps([bad]), stderr="",
        )):
            with self.assertRaises(HTTPException) as error:
                file_service._owned_container(item)
        self.assertEqual(error.exception.status_code, 409)

    def test_symlink_paths_are_rejected_before_operation(self):
        context = file_service.FileContext(app(), "srv-container-app-7", file_service.FileRoot(
            "application", "Application files", "/app", "container", False,
        ))
        with patch.object(file_service, "_container_shell", return_value=Mock(returncode=0, stdout="", stderr="")):
            with self.assertRaises(HTTPException) as error:
                file_service._require_no_symlinks(context, "/app/storage")
        self.assertEqual(error.exception.status_code, 409)

    def test_upload_uses_direct_docker_copy_to_the_verified_target(self):
        context = file_service.FileContext(app(), "srv-container-app-7", file_service.FileRoot(
            "application", "Application files", "/app", "container", False,
        ))
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.txt"
            source.write_text("safe", encoding="utf-8")
            with patch.object(container_app_service, "run_binary", return_value=Mock(returncode=0, stdout=b"", stderr=b"")) as run:
                file_service._copy_file_to_container(context, "/app/storage/file.txt", source)
        command = run.call_args.args[0]
        self.assertEqual(command[:2], ["docker", "cp"])
        self.assertEqual(command[-1], "srv-container-app-7:/app/storage/file.txt")

    def test_host_root_rejects_symlink_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "root"
            root.mkdir()
            context = file_service.FileContext(
                SimpleNamespace(), None,
                file_service.FileRoot("application", "Website files", str(root), "host", True),
            )
            outside = root / "outside"
            original_lstat = Path.lstat

            def symlink_lstat(path, *args, **kwargs):
                if path == outside:
                    return SimpleNamespace(st_mode=stat.S_IFLNK)
                return original_lstat(path, *args, **kwargs)

            with patch.object(Path, "lstat", symlink_lstat):
                with self.assertRaises(HTTPException) as error:
                    host_file_service.read_text(context, "outside/secret.txt")
        self.assertEqual(error.exception.status_code, 409)

    def test_host_root_uses_etag_before_replacing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = file_service.FileContext(
                SimpleNamespace(), None,
                file_service.FileRoot("application", "Website files", str(root), "host", True),
            )
            host_file_service.write_text(context, ".env", "APP_KEY=first", None, set())
            before = host_file_service.read_text(context, ".env")
            host_file_service.write_text(context, ".env", "APP_KEY=next", before["etag"], set())
            self.assertEqual(host_file_service.read_text(context, ".env")["content"], "APP_KEY=next")


class FileManagerEnvironmentTests(unittest.TestCase):
    def test_runtime_environment_write_requires_a_later_restart(self):
        context = file_service.FileContext(app(), "srv-container-app-7", file_service.FileRoot(
            "runtime-env", "Runtime .env", "/tmp/7.env", "environment", True, True,
        ))
        body = router.TextWrite(path=".env", content="APP_KEY=value", etag="etag")
        with patch.object(file_service, "write_text", return_value=13):
            self.assertEqual(
                router._write(context, body, {"PORT"}),
                {"size": 13, "restart_required": True},
            )

    def test_runtime_environment_preserves_panel_managed_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "7.env"
            path.write_text("PORT=3000\nDATABASE_URL=managed\nAPP_KEY=old\n", encoding="utf-8")
            context = file_service.FileContext(app(env_path=str(path)), "srv-container-app-7", file_service.FileRoot(
                "runtime-env", "Runtime .env", str(path), "environment", True, True,
            ))
            etag = file_service._etag(path.read_bytes())
            with self.assertRaises(HTTPException):
                file_service.write_text(
                    context, ".env", "PORT=4000\nDATABASE_URL=managed\nAPP_KEY=new\n",
                    etag, {"PORT", "DATABASE_URL"},
                )
            written = file_service.write_text(
                context, ".env", "PORT=3000\nDATABASE_URL=managed\nAPP_KEY=new\nMAIL_HOST=mail.test\n",
                etag, {"PORT", "DATABASE_URL"},
            )
            self.assertGreater(written, 0)
            self.assertIn("APP_KEY=new", path.read_text(encoding="utf-8"))
            self.assertIn("MAIL_HOST=mail.test", path.read_text(encoding="utf-8"))

    def test_upload_limit_is_enforced_without_container_access(self):
        context = file_service.FileContext(app(), "srv-container-app-7", file_service.FileRoot(
            "application", "Application files", "/app", "container", False,
        ))
        with patch.object(file_service.config, "FILE_MANAGER_MAX_TRANSFER_BYTES", 2):
            with self.assertRaises(HTTPException) as error:
                file_service.write_upload(context, "large.bin", b"123", None)
        self.assertEqual(error.exception.status_code, 413)


class FileManagerLifecycleAndAuditTests(unittest.TestCase):
    def test_same_app_uses_one_operation_lock(self):
        self.assertIs(file_service.lock_for(7), file_service.lock_for(7))

    def test_deployment_blocks_file_context_before_docker_inspection(self):
        async def run():
            db = AsyncMock()
            db.get.return_value = app()
            with patch.object(container_app_deployment_service, "active_deployment", new=AsyncMock(return_value=Mock())):
                with self.assertRaises(HTTPException) as error:
                    await file_service.resolve_context(db, 7, "application")
            self.assertEqual(error.exception.status_code, 409)
        asyncio.run(run())

    def test_audit_record_has_metadata_but_no_file_content(self):
        class Db:
            def __init__(self): self.items = []
            def add(self, value): self.items.append(value)

        async def run():
            request = Request({
                "type": "http", "method": "POST", "path": "/plugins/file_manager/api",
                "headers": [], "client": ("127.0.0.1", 1234), "session": {"user_id": 3},
            })
            request.state.request_id = "abc123"
            db = Db()
            await audit.record(db, request, app_id=7, root_id="application", relative_path=".env", action="write", result="success", size_bytes=8)
            event = db.items[0]
            self.assertIsInstance(event, FileManagerEvent)
            self.assertEqual(event.relative_path, ".env")
            self.assertFalse(hasattr(event, "content"))
            self.assertFalse(hasattr(event, "secret"))
        asyncio.run(run())


class FileManagerTargetTests(unittest.TestCase):
    def test_target_identifier_requires_a_known_provider_and_numeric_id(self):
        target = file_targets.parse_target("python:7")
        self.assertEqual((target.kind, target.resource_id), ("python", 7))
        target_php = file_targets.parse_target("php:7")
        self.assertEqual((target_php.kind, target_php.resource_id), ("php", 7))
        for value in ("7", "unknown:7", "python:0", "python:seven", "php:0"):
            with self.assertRaises(HTTPException):
                file_targets.parse_target(value)

    def test_static_root_must_match_the_panel_webroot(self):
        with tempfile.TemporaryDirectory() as directory:
            public = Path(directory) / "site.test" / "public"
            public.mkdir(parents=True)
            domain = SimpleNamespace(
                name="site.test", nginx_active=True, project_type="static",
                webroot_path=str(public),
            )
            with patch.object(file_targets.config, "NGINX_WEBROOT", directory):
                roots = file_targets._static_roots(domain)
                self.assertEqual([root.id for root in roots], ["application"])
            domain.webroot_path = "/tmp/not-owned"
            with patch.object(file_targets.config, "NGINX_WEBROOT", directory):
                self.assertEqual(file_targets._static_roots(domain), [])

    def test_php_roots_must_match_the_panel_webroot(self):
        with tempfile.TemporaryDirectory() as directory:
            site_dir = Path(directory) / "phpsite.test"
            site_dir.mkdir(parents=True)
            site = SimpleNamespace(
                id=1, domain_id=1, preset="plain", status="active",
                root_path=str(site_dir),
            )
            with patch.object(file_targets.config, "NGINX_WEBROOT", directory):
                roots = file_targets._php_roots(site, "phpsite.test")
                self.assertEqual([root.id for root in roots], ["application"])
            site.root_path = "/tmp/not-owned"
            with patch.object(file_targets.config, "NGINX_WEBROOT", directory):
                self.assertEqual(file_targets._php_roots(site, "phpsite.test"), [])

    def test_target_listing_combines_container_python_static_and_php_apps(self):
        class Result:
            def __init__(self, rows): self.rows = rows
            def all(self): return self.rows

        class Db:
            def __init__(self):
                self.results = iter((
                    Result([(SimpleNamespace(id=2, preset=None, status="running"), "api.test")]),
                    Result([(SimpleNamespace(id=3, status="stopped"), "python.test")]),
                    Result([(SimpleNamespace(id=5, preset="plain", status="active", root_path="/var/www/php.test"), "php.test")]),
                ))

            async def execute(self, _statement):
                return next(self.results)

            async def scalars(self, _statement):
                return Result([SimpleNamespace(id=4, name="site.test")])

        async def run():
            with patch.object(file_targets, "_python_roots", return_value=[Mock()]), patch.object(
                file_targets, "_static_roots", return_value=[Mock()],
            ), patch.object(
                file_targets, "_php_roots", return_value=[Mock()],
            ):
                return await file_targets.list_targets(Db())

        self.assertEqual(
            [item["id"] for item in asyncio.run(run())],
            ["container:2", "php:5", "python:3", "static:4"],
        )

    def test_python_roots_stay_inside_the_owned_active_release(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            release = root / "7" / "releases" / "42"
            (release / "source").mkdir(parents=True)
            (root / "7" / "data").mkdir()
            item = SimpleNamespace(id=7, work_dir=str(root / "7"))
            with patch.object(file_targets.config, "APP_HOSTING_ROOT", directory), patch.object(
                file_targets, "_active_python_release", return_value=release,
            ):
                roots = file_targets._python_roots(item)
            self.assertEqual([root.id for root in roots], ["application", "data"])


if __name__ == "__main__":
    unittest.main()
