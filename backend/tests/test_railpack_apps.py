import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from fastapi import HTTPException
from dependencies.docker.service import DockerDependencyService
from plugins.railpack_apps.service import RailpackAppsService
from services import container_app_deployment_service, container_app_service


class RailpackAppsValidationTests(unittest.TestCase):
    def test_registry_image_validation_rejects_unsafe_values(self):
        self.assertEqual(
            container_app_service.validate_image_reference("ghcr.io/acme/app:1.2.3"),
            "ghcr.io/acme/app:1.2.3",
        )
        for value in ("", "/tmp/image", "../image", "image with spaces"):
            with self.assertRaises(HTTPException):
                container_app_service.validate_image_reference(value)

    def test_internal_http_port_must_be_valid(self):
        self.assertEqual(container_app_service.validate_port(3000), 3000)
        for value in (0, 65536):
            with self.assertRaises(HTTPException):
                container_app_service.validate_port(value)

    def test_port_environment_matches_the_private_container_port(self):
        self.assertEqual(container_app_service.environment_for_port({}, 3000), {"PORT": "3000"})
        self.assertEqual(container_app_service.environment_for_port({"PORT": "8080"}, 8080), {"PORT": "8080"})
        with self.assertRaises(HTTPException):
            container_app_service.environment_for_port({"PORT": "8080"}, 3000)

    def test_external_database_url_is_written_as_the_app_secret(self):
        values = container_app_service.database_environment(
            "external", "postgresql://app:secret@db.example:5432/app", {},
        )
        self.assertEqual(values["DATABASE_URL"], "postgresql://app:secret@db.example:5432/app")
        with self.assertRaises(HTTPException):
            container_app_service.database_environment("external", "not a URL", {})

    def test_environment_file_is_private_and_rejects_bad_values(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "app.env"
            container_app_service._write_env(path, {"PORT": "3000", "APP_KEY": "secret"})
            self.assertEqual(path.read_text(encoding="utf-8"), "PORT=3000\nAPP_KEY=secret\n")
            if os.name != "nt":
                self.assertEqual(path.stat().st_mode & 0o077, 0)
            with self.assertRaises(HTTPException):
                container_app_service._write_env(path, {"bad-key": "x"})
            with self.assertRaises(HTTPException):
                container_app_service._write_env(path, {"KEY": "line\nbreak"})


class RailpackAppsLifecycleTests(unittest.TestCase):
    def test_disabling_plugin_leaves_deployments_running(self):
        service = RailpackAppsService()
        self.assertIsNone(service.pause())

    def test_container_run_is_private_labeled_and_limited(self):
        app = Mock(
            id=7,
            container_name="srv-container-app-7",
            previous_image=None,
            memory_limit_mb=512,
            cpu_limit="1.0",
            pid_limit=256,
            host_port=31007,
            internal_port=3000,
            env_path="/tmp/app.env",
            data_volume=None,
            data_mount_path=None,
        )
        with patch.object(container_app_service, "_run") as run:
            run.side_effect = [
                Mock(returncode=1, stdout="", stderr=""), Mock(returncode=1, stdout="", stderr=""),
                Mock(returncode=0, stdout="", stderr=""), Mock(returncode=0, stdout="", stderr=""),
            ]
            container_app_deployment_service._replace_container(app, "srv-panel/railpack-app:7-1")
        command = run.call_args_list[-1].args[0]
        self.assertIn("srv-panel.plugin=railpack_apps", command)
        self.assertIn("srv-panel.app-id=7", command)
        self.assertIn("127.0.0.1:31007:3000", command)
        self.assertIn("no-new-privileges", command)
        self.assertIn("srv-container-net-7", command)
        self.assertNotIn("0.0.0.0:31007:3000", command)


class DockerPluginResourceTests(unittest.TestCase):
    def test_resource_summary_is_read_only(self):
        service = DockerDependencyService()
        service.get_status = Mock(return_value={"healthy": True})
        service._run = Mock(side_effect=[
            Mock(returncode=0, stdout="a\n", stderr=""),
            Mock(returncode=0, stdout="", stderr=""),
            Mock(returncode=0, stdout="v\n", stderr=""),
        ])
        self.assertEqual(service.plugin_resource_summary("railpack_apps"), {
            "containers": 1, "networks": 0, "volumes": 1,
        })
        self.assertFalse(any("rm" in call.args[0] for call in service._run.call_args_list))


if __name__ == "__main__":
    unittest.main()
