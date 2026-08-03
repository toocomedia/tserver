"""Regression checks for the Railpack builder's Jinja and browser-facing contract."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

from jinja2 import ChoiceLoader, Environment, FileSystemLoader

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from plugins.railpack_apps import router
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
        markup = (BACKEND / "plugins" / "railpack_apps" / "templates" / "railpack_apps_create.html").read_text(encoding="utf-8")
        for control in ("domain_id", "repository_url", "build_mode", "internal_port", "database_attachments", "environment_values", "wordpress_site_title"):
            self.assertIn(f'name="{control}"', markup)
        self.assertIn("data-inspect", markup)
        self.assertNotIn("readonly", markup)
        self.assertIn("data-database-row", markup)
        self.assertIn("data-database-requirement", markup)
        self.assertIn("data-environment-list", markup)
        self.assertIn("data-add-environment", markup)
        self.assertIn("WordPress preset", markup)

    def test_builder_script_auto_selects_required_and_detected_services(self):
        script = (BACKEND / "static" / "js" / "modules" / "railpack-app-create.js").read_text(encoding="utf-8")
        self.assertIn("wordpressDatabaseState(wordpress)", script)
        self.assertIn("provider.value = 'docker'", script)
        self.assertIn("sourceRequired", script)
        self.assertIn("types.forEach", script)
        self.assertIn("environmentValues()", script)

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
        for value in ("Rotate credentials", "Create backup", "RESTORE", "Delete service and data", "Update WordPress", "Delete WordPress files"):
            self.assertIn(value, markup)


if __name__ == "__main__":
    unittest.main()
