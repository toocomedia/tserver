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
        self.assertIn("plugin_manager.list_plugins(check_dependencies=False)", index_route)
        self.assertNotIn("dependency_manager.get_all_statuses", index_route)
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

    def test_usage_polling_is_slow_visible_and_single_flight(self):
        usage_template = (
            BACKEND / "templates" / "pages" / "usage" / "index.html"
        ).read_text(encoding="utf-8")

        self.assertIn("const INTERVAL = 30000", usage_template)
        self.assertIn("let statsRequestPending = false", usage_template)
        self.assertIn("document.hidden", usage_template)
        self.assertIn("document.addEventListener('visibilitychange'", usage_template)
        self.assertNotIn("const INTERVAL = 3000;", usage_template)

    def test_stats_endpoint_uses_shared_cache_and_no_live_dependency_hooks(self):
        system_router = (
            BACKEND / "routers" / "system.py"
        ).read_text(encoding="utf-8")
        stats_builder = system_router[
            system_router.index("async def _build_server_stats")
            : system_router.index("class OptimizationToggleIn")
        ]

        self.assertIn("_STATS_CACHE_SECONDS = 15.0", system_router)
        self.assertIn('dependency_manager.get_status, "docker", cached=True', stats_builder)
        self.assertIn("live_hooks=False", stats_builder)
        self.assertIn("async with _stats_cache_lock", stats_builder)
        self.assertNotIn("systemctl", stats_builder)
        self.assertNotIn("snap list", stats_builder)

    def test_php_site_list_batches_related_records(self):
        php_sites = (
            BACKEND / "services" / "php_site_service.py"
        ).read_text(encoding="utf-8")
        list_sites = php_sites[
            php_sites.index("async def list_sites")
            : php_sites.index("async def serialize_site")
        ]

        self.assertIn("Domain.id.in_(domain_ids)", list_sites)
        self.assertIn("PhpWebsiteDatabase.site_id.in_(site_ids)", list_sites)
        self.assertIn("PhpWebsiteOperation.site_id.in_(site_ids)", list_sites)
        self.assertIn("_site_payload(", list_sites)
        self.assertNotIn("await serialize_site", list_sites)

    def test_hosting_routers_use_batched_queries(self):
        domains_router = (
            BACKEND / "routers" / "domains.py"
        ).read_text(encoding="utf-8")
        domains_list = domains_router[
            domains_router.index('@router.get("/", response_class=HTMLResponse)')
            : domains_router.index('@router.get("/create", response_class=HTMLResponse)')
        ]
        self.assertIn("all_certs = {c.full_domain: c for c in", domains_list)
        self.assertNotIn("select(SslCert).where(", domains_list)

        proxy_router = (
            BACKEND / "routers" / "proxy.py"
        ).read_text(encoding="utf-8")
        self.assertIn("domain_map = {d.id: d for d in", proxy_router)
        self.assertIn("dns_cache = {}", proxy_router)

    def test_base_layout_includes_universal_loader(self):
        layout_html = (
            BACKEND / "templates" / "layout.html"
        ).read_text(encoding="utf-8")
        self.assertIn('id="top-progress-bar"', layout_html)
        self.assertIn("data-async-load", layout_html)

        loading_css = (
            BACKEND / "static" / "css" / "components" / "loading.css"
        ).read_text(encoding="utf-8")
        self.assertIn(".top-progress-bar", loading_css)





if __name__ == "__main__":
    unittest.main()
