import sys
import unittest
from pathlib import Path


BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


class PanelLoadingRegressionTests(unittest.TestCase):
    def test_plugin_pages_do_not_probe_runtime_during_initial_render(self):
        maddy_router = (
            BACKEND / "plugins" / "maddy" / "router.py"
        ).read_text(encoding="utf-8")
        maddy_index = maddy_router[
            maddy_router.index('@router.get("/", response_class=HTMLResponse)')
            : maddy_router.index('@router.post("/api/install")')
        ]
        self.assertNotIn("maddy_service.get_status", maddy_index)
        self.assertNotIn("roundcube_webmail_service.get_status", maddy_index)

        roundcube_router = (
            BACKEND / "plugins" / "roundcube_webmail" / "router.py"
        ).read_text(encoding="utf-8")
        roundcube_index = roundcube_router[
            roundcube_router.index('@router.get("/", response_class=HTMLResponse)')
            : roundcube_router.index('@router.get("/api/status")')
        ]
        self.assertNotIn("roundcube_webmail_service.get_status", roundcube_index)
        status_payload = roundcube_router[
            roundcube_router.index("async def _status_payload(")
            : roundcube_router.index("def _friendly_ssl_error")
        ]
        self.assertNotIn("roundcube_webmail_service.get_status", status_payload)

    def test_plugins_page_uses_loaded_registry(self):
        plugins_router = (
            BACKEND / "routers" / "plugins.py"
        ).read_text(encoding="utf-8")
        index_route = plugins_router[
            plugins_router.index('@router.get("/", response_class=HTMLResponse)')
            : plugins_router.index('@router.get("/assets/{plugin_id}/{filename}")')
        ]
        self.assertIn("plugin_manager.list_plugins(check_dependencies=True)", index_route)
        self.assertNotIn("discover_plugins()", index_route)

    def test_roundcube_page_does_not_start_live_status_polling(self):
        template = (
            BACKEND
            / "plugins"
            / "roundcube_webmail"
            / "templates"
            / "roundcube_webmail.html"
        ).read_text(encoding="utf-8")
        javascript = (
            BACKEND / "static" / "js" / "features" / "roundcube-webmail.js"
        ).read_text(encoding="utf-8")

        self.assertNotIn('id="container-status"', template)
        self.assertNotIn("\n  refreshStatus();\n})();", javascript)

    def test_usage_process_scan_runs_in_worker_thread(self):
        system_router = (
            BACKEND / "routers" / "system.py"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "snapshot = await asyncio.to_thread(_collect_usage_snapshot)",
            system_router,
        )

        maddy_service = (
            BACKEND / "plugins" / "maddy" / "service.py"
        ).read_text(encoding="utf-8")
        self.assertIn("def get_usage_details(self)", maddy_service)


if __name__ == "__main__":
    unittest.main()
