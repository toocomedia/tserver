"""Tests for App Engine in-browser container command execution and container isolation."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
import subprocess

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from fastapi import HTTPException
from models.container_app import ContainerApp
from models.domain import Domain
from plugins.railpack_apps import command_service
from plugins.railpack_apps.router_command import CommandRunRequest, run_command_in_app, get_app_quick_commands


class RailpackAppsCommandTests(unittest.IsolatedAsyncioTestCase):
    def test_single_container_app_authorized_containers(self):
        app = SimpleNamespace(
            id=42,
            deploy_type="railpack",
            container_name="srv-app-42",
            stack_catalog_id=None,
            stack_services=None,
        )
        containers = command_service.get_authorized_containers(app)
        self.assertEqual(len(containers), 1)
        self.assertEqual(containers[0]["name"], "srv-app-42")
        self.assertTrue(containers[0]["is_primary"])

    def test_official_stack_authorized_containers(self):
        app = SimpleNamespace(
            id=10,
            deploy_type="official_stack",
            container_name="srv-stack-10-web",
            stack_catalog_id="plausible",
            stack_services='{"services": {"plausible": {}, "postgres": {}, "clickhouse": {}}}',
        )
        containers = command_service.get_authorized_containers(app)
        names = [c["name"] for c in containers]
        self.assertIn("srv-stack-10-plausible", names)
        self.assertIn("srv-stack-10-postgres", names)
        self.assertIn("srv-stack-10-clickhouse", names)

    def test_quick_commands_framework_tailored(self):
        # WordPress
        wp_app = SimpleNamespace(
            image_reference="wordpress:latest",
            repository_url=None,
            stack_catalog_id=None,
            preset="wordpress",
            build_mode="dockerfile",
            wordpress_admin_email="admin@test.com",
        )
        wp_cmds = command_service.get_quick_commands(wp_app)
        wp_labels = [c["label"] for c in wp_cmds]
        self.assertIn("WP Info", wp_labels)
        self.assertIn("List Users", wp_labels)

        # Shynet
        shynet_app = SimpleNamespace(
            image_reference="milesmcc/shynet:latest",
            repository_url=None,
            stack_catalog_id="shynet",
            preset=None,
            build_mode="image",
            wordpress_admin_email="test@admin.com",
        )
        shynet_cmds = command_service.get_quick_commands(shynet_app)
        shynet_labels = [c["label"] for c in shynet_cmds]
        self.assertIn("Admin Setup", shynet_labels)
        self.assertIn("Migrations", shynet_labels)

        # Generic system commands always present
        self.assertIn("Directory (ls -la)", wp_labels)
        self.assertIn("Environment (env)", wp_labels)

    async def test_execute_empty_command_raises_400(self):
        app = SimpleNamespace(id=1, status="running", container_name="srv-app-1", deploy_type="railpack")
        with self.assertRaises(HTTPException) as ctx:
            await command_service.execute_app_command(app, "")
        self.assertEqual(ctx.exception.status_code, 400)

    async def test_execute_when_stopped_raises_400(self):
        app = SimpleNamespace(id=1, status="stopped", container_name="srv-app-1", deploy_type="railpack")
        with self.assertRaises(HTTPException) as ctx:
            await command_service.execute_app_command(app, "ls -la")
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("not running", str(ctx.exception.detail))

    async def test_execute_when_deleting_raises_409(self):
        app = SimpleNamespace(id=1, status="deleting", container_name="srv-app-1", deploy_type="railpack")
        with self.assertRaises(HTTPException) as ctx:
            await command_service.execute_app_command(app, "ls -la")
        self.assertEqual(ctx.exception.status_code, 409)

    async def test_execute_unauthorized_container_raises_403(self):
        app = SimpleNamespace(id=1, status="running", container_name="srv-app-1", deploy_type="railpack")
        with self.assertRaises(HTTPException) as ctx:
            await command_service.execute_app_command(app, "ls -la", container_name="srv-app-999")
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertIn("does not belong to this application", str(ctx.exception.detail))

    @patch("services.container_app_service._run")
    async def test_execute_command_success(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=["docker", "exec", "-i", "srv-app-1", "sh", "-c", "echo hello"],
            returncode=0,
            stdout="hello\n",
            stderr="",
        )
        app = SimpleNamespace(id=1, status="running", container_name="srv-app-1", deploy_type="railpack")
        result = await command_service.execute_app_command(app, "echo hello")

        self.assertTrue(result["success"])
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["stdout"], "hello\n")
        self.assertEqual(result["command"], "echo hello")
        self.assertEqual(result["container"], "srv-app-1")
        self.assertIn("duration_ms", result)
        self.assertIn("timestamp", result)

    @patch("services.container_app_service._run")
    async def test_execute_command_failure(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=["docker", "exec", "-i", "srv-app-1", "sh", "-c", "false"],
            returncode=1,
            stdout="",
            stderr="command not found\n",
        )
        app = SimpleNamespace(id=1, status="running", container_name="srv-app-1", deploy_type="railpack")
        result = await command_service.execute_app_command(app, "false")

        self.assertFalse(result["success"])
        self.assertEqual(result["exit_code"], 1)
        self.assertEqual(result["stderr"], "command not found\n")

    @patch("services.container_app_service._run")
    async def test_router_run_command_endpoint(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=["docker", "exec"],
            returncode=0,
            stdout="output",
            stderr="",
        )
        app = SimpleNamespace(id=5, status="running", container_name="srv-app-5", deploy_type="railpack")
        mock_db = AsyncMock()
        mock_db.get.return_value = app

        req = CommandRunRequest(command="whoami", timeout=15)
        response = await run_command_in_app(5, req, request=MagicMock(), db=mock_db)

        self.assertEqual(response.status_code, 200)
        import json
        body = json.loads(response.body)
        self.assertTrue(body["success"])
        self.assertEqual(body["command"], "whoami")

    async def test_router_quick_commands_endpoint(self):
        app = SimpleNamespace(
            id=5,
            domain_id=1,
            status="running",
            container_name="srv-app-5",
            deploy_type="railpack",
            image_reference="nginx:latest",
            repository_url=None,
            stack_catalog_id=None,
            preset=None,
            build_mode="image",
            wordpress_admin_email=None,
        )
        mock_db = AsyncMock()
        mock_db.get.side_effect = lambda model, obj_id: app if obj_id == 5 else SimpleNamespace(id=1, name="test.com")

        response = await get_app_quick_commands(5, db=mock_db)
        self.assertEqual(response.status_code, 200)
        import json
        body = json.loads(response.body)
        self.assertEqual(body["app_id"], 5)
        self.assertTrue(len(body["containers"]) >= 1)
        self.assertTrue(len(body["quick_commands"]) >= 1)


if __name__ == "__main__":
    unittest.main()
