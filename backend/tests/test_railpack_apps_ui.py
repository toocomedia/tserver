"""Regression checks for the Railpack builder's Jinja and browser-facing contract."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

from jinja2 import ChoiceLoader, Environment, FileSystemLoader

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from plugins.railpack_apps import router, router_create
from services import container_app_inspection_service as inspection


class RailpackAppsUiTests(unittest.TestCase):
    def test_builder_jinja_templates_compile(self):
        env = Environment(loader=ChoiceLoader([
            FileSystemLoader(str(BACKEND / "templates")),
            FileSystemLoader(str(BACKEND / "plugins" / "railpack_apps" / "templates")),
        ]))
        for name in ("layout.html", "partials/csrf_field.html", "railpack_apps_create.html", "railpack_apps_detail.html"):
            env.get_template(name)

    def test_builder_has_editable_detected_controls(self):
        template_root = BACKEND / "plugins" / "railpack_apps" / "templates" / "railpack_apps"
        markup = (BACKEND / "plugins" / "railpack_apps" / "templates" / "railpack_apps_create.html").read_text(encoding="utf-8")
        markup += "".join(path.read_text(encoding="utf-8") for path in (template_root / "partials").glob("create_*.html"))
        for control in ("domain_id", "repository_url", "build_mode", "internal_port", "database_attachments", "environment_values", "wordpress_site_title"):
            self.assertIn(f'name="{control}"', markup)
        self.assertIn("data-wizard-next", markup)
        self.assertNotIn("readonly", markup)
        self.assertIn("data-database-row", markup)
        self.assertIn("data-database-requirement", markup)
        self.assertIn("data-environment-list", markup)
        self.assertIn("data-add-environment", markup)
        self.assertIn("WordPress preset", markup)

    def test_builder_script_auto_selects_required_and_detected_services(self):
        script = (BACKEND / "static" / "js" / "modules" / "railpack-app-create.js").read_text(encoding="utf-8")
        self.assertIn("wordpressDatabaseState(wordpress)", script)
        self.assertIn("[data-database-provider]').value = 'docker'", script)
        self.assertIn("sourceRequired", script)
        self.assertIn("database_types || []).forEach", script)
        self.assertIn("environmentValues(form)", script)
        self.assertIn("function domainState()", script)
        self.assertIn("HTTPS is already active for this domain", script)

    def test_builder_has_five_steps_and_local_database_artwork(self):
        markup = (BACKEND / "plugins" / "railpack_apps" / "templates" / "railpack_apps_create.html").read_text(encoding="utf-8")
        configuration = (BACKEND / "plugins" / "railpack_apps" / "templates" / "railpack_apps" / "partials" / "create_configuration.html").read_text(encoding="utf-8")
        panels = "".join((BACKEND / "plugins" / "railpack_apps" / "templates" / "railpack_apps" / "partials" / f"create_{name}.html").read_text(encoding="utf-8") for name in ("source", "inspection", "configuration", "deployment", "result"))
        for step in range(1, 6):
            self.assertIn(f'data-wizard-panel="{step}"', panels)
        self.assertIn("settings-choice-grid", configuration)
        self.assertIn("database-postgresql.svg", configuration)
        self.assertIn("data-environment-list", configuration)
        for name in ("database-mariadb.svg", "database-postgresql.svg", "database-redis.svg", "database-mongodb.svg"):
            self.assertTrue((BACKEND / "static" / "images" / "apps-engine" / name).is_file())

    def test_wizard_polls_real_deployment_status(self):
        script = (BACKEND / "static" / "js" / "modules" / "railpack-app-create.js").read_text(encoding="utf-8")
        ui_script = (BACKEND / "static" / "js" / "modules" / "railpack-app-create-ui.js").read_text(encoding="utf-8")
        self.assertIn("/deployments/${state.deploymentId}", script)
        self.assertIn("['queued', 'running']", script)
        self.assertIn("finishDeployment(data)", script)
        self.assertIn("actual = lines.length", ui_script)
        self.assertIn("start: 'Starting application container'", ui_script)

    def test_inspection_detects_runtime_port_and_database(self):
        self.assertEqual(inspection._port("EXPOSE 8080", "Node.js"), 8080)
        self.assertEqual(inspection._databases("import psycopg and redis"), ["postgresql", "redis"])
        self.assertEqual(inspection._databases("from pymongo import MongoClient"), ["mongodb"])
        self.assertEqual(inspection._databases("mysql2 pgx go-redis mongo-driver"), ["postgresql", "mariadb/mysql", "mongodb", "redis"])
        self.assertEqual(inspection._runtime({"Gemfile"}), "Ruby")
        self.assertEqual(inspection._runtime({"pom.xml"}), "Java")
        self.assertEqual(inspection._runtime({"index.html"}), "Static site")

    def test_specific_actions_precede_generic_control_route(self):
        paths = [route.path for route in router.router.routes]
        uninstall = next(i for i, path in enumerate(paths) if path.endswith("/{app_id}/uninstall"))
        control = next(i for i, path in enumerate(paths) if path.endswith("/{app_id}/{action}"))
        self.assertLess(uninstall, control)

    def test_detail_query_id_uses_mapping_compatible_parsing(self):
        self.assertEqual(router._optional_deployment_id("42"), 42)
        self.assertIsNone(router._optional_deployment_id("not-a-number"))
        source = (BACKEND / "plugins" / "railpack_apps" / "router.py").read_text(encoding="utf-8")
        self.assertNotIn('query_params.get("deployment", type=', source)

    def test_detail_includes_safe_database_actions(self):
        markup = (BACKEND / "plugins" / "railpack_apps" / "templates" / "railpack_apps_detail.html").read_text(encoding="utf-8")
        for component in ("hero-app-box", "layout-2col", "master-card", "Live deployment stream", "Danger zone"):
            self.assertIn(component, markup)
        for value in ("Rotate credentials", "Create backup", "RESTORE", "Update WordPress", "keep_database_ids", "keep_app_volume", "keep_saved_backups", "DELETE ALL", "railpack-delete-disclosure"):
            self.assertIn(value, markup)

    def test_ssl_uses_the_domain_certificate_not_the_original_request(self):
        detail = (BACKEND / "plugins" / "railpack_apps" / "templates" / "railpack_apps_detail.html").read_text(encoding="utf-8")
        source = (BACKEND / "plugins" / "railpack_apps" / "templates" / "railpack_apps" / "partials" / "create_source.html").read_text(encoding="utf-8")
        self.assertIn("https=ssl_active", detail)
        self.assertIn("'HTTPS active' if ssl_active", detail)
        self.assertIn("data-domain-ssl", source)

    def test_uninstall_deletes_all_unless_data_is_explicitly_kept(self):
        source = (BACKEND / "plugins" / "railpack_apps" / "router.py").read_text(encoding="utf-8")
        self.assertIn('delete_database_ids = list(managed_ids - set(keep_database_ids))', source)
        self.assertIn('confirmation != "DELETE ALL"', source)
        self.assertIn("remove_selected_data", source)

    def test_create_returns_json_only_when_requested(self):
        json_response = router_create._create_response(SimpleNamespace(headers={"accept": "application/json"}), 8, 13)
        redirect_response = router_create._create_response(SimpleNamespace(headers={}), 8, 13)
        self.assertEqual(json_response.status_code, 200)
        self.assertIn(b'"app_id":8', json_response.body)
        self.assertEqual(redirect_response.status_code, 303)
        self.assertEqual(redirect_response.headers["location"], "/plugins/railpack_apps/8?deployment=13")


if __name__ == "__main__":
    unittest.main()
