"""
test_ai_helper_search_tools.py — Unit tests for AI Helper search grounding tools,
Docker Hub repository search, and source-code environment variable scanning.
"""
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from plugins.ai_helper.permissions.policy import TOOL_CATEGORY_MAP
from plugins.ai_helper.tools import definitions, docker_hub, registry, web_reader
from services.apps_engine import doc_evidence


class TestAiHelperSearchTools(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.root = Path(self.temp_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    async def test_search_web_docs_success(self):
        """Verify search_web_docs fetches clean Markdown snippets via Jina Search."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = (
            "# n8n Docker Setup Guide\n"
            "Set `N8N_PORT=5678` and `WEBHOOK_URL=https://example.com`.\n"
            "Database: `DB_TYPE=postgresdb` with `DB_POSTGRESDB_PASSWORD=my-secret-password`."
        )

        with patch("httpx.AsyncClient.get", AsyncMock(return_value=mock_response)), \
             patch("plugins.ai_helper.tools.web_reader._validate_and_resolve_host", return_value=["93.184.216.34"]):
            res = await web_reader.search_web_docs("n8n docker setup")

            self.assertEqual(res["status"], "ok")
            self.assertEqual(res["query"], "n8n docker setup")
            self.assertIn("n8n Docker Setup Guide", res["content"])
            self.assertIn("N8N_PORT=5678", res["content"])
            # Verify secret redaction
            self.assertNotIn("my-secret-password", res["content"])
            self.assertIn("DB_POSTGRESDB_PASSWORD=[REDACTED]", res["content"])
            self.assertIn("EXTERNAL WEB SEARCH RESULTS", res["content"])

    async def test_search_web_docs_empty_query(self):
        """Verify search_web_docs gracefully rejects empty queries."""
        res = await web_reader.search_web_docs("   ")
        self.assertEqual(res["status"], "error")
        self.assertIn("empty", res["message"])

    async def test_fetch_web_documentation_with_jina_fallback(self):
        """Verify fetch_web_documentation falls back to Jina Reader when direct fetch returns 403."""
        direct_resp = MagicMock()
        direct_resp.status_code = 403

        jina_resp = MagicMock()
        jina_resp.status_code = 200
        jina_resp.text = "# Ghost CMS Installation\nRun `docker run -d -p 2368:2368 ghost:5`."

        async def mock_get(url, *args, **kwargs):
            if "r.jina.ai" in str(url):
                return jina_resp
            return direct_resp

        with patch("httpx.AsyncClient.get", AsyncMock(side_effect=mock_get)), \
             patch("plugins.ai_helper.tools.web_reader._validate_and_resolve_host", return_value=["93.184.216.34"]):
            res = await web_reader.fetch_web_documentation("https://docs.ghost.org/install")

            self.assertEqual(res["status"], "ok")
            self.assertEqual(res.get("fallback"), "jina_reader")
            self.assertIn("Ghost CMS Installation", res["content"])
            self.assertIn("ghost:5", res["content"])

    async def test_search_docker_hub_success(self):
        """Verify search_docker_hub retrieves structured image metadata from Docker Hub API."""
        mock_hub_data = {
            "count": 2,
            "results": [
                {
                    "repo_name": "library/redis",
                    "short_description": "Redis is an open source in-memory data structure store",
                    "is_official": True,
                    "star_count": 12500,
                    "pull_count": "1B+",
                },
                {
                    "repo_name": "bitnami/redis",
                    "short_description": "Bitnami Docker Image for Redis",
                    "is_official": False,
                    "star_count": 1500,
                    "pull_count": "500M+",
                },
            ],
        }

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json = MagicMock(return_value=mock_hub_data)

        with patch("httpx.AsyncClient.get", AsyncMock(return_value=mock_resp)):
            res = await docker_hub.search_docker_hub("redis", max_results=2)

            self.assertEqual(res["status"], "ok")
            self.assertEqual(res["query"], "redis")
            self.assertEqual(len(res["results"]), 2)
            self.assertEqual(res["results"][0]["image"], "library/redis")
            self.assertTrue(res["results"][0]["is_official"])
            self.assertEqual(res["results"][0]["stars"], 12500)
            self.assertEqual(res["results"][1]["image"], "bitnami/redis")
            self.assertFalse(res["results"][1]["is_official"])

    async def test_search_docker_hub_empty_query(self):
        """Verify search_docker_hub rejects empty queries."""
        res = await docker_hub.search_docker_hub("")
        self.assertEqual(res["status"], "error")
        self.assertIn("empty", res["message"])

    def test_scan_code_env_vars(self):
        """Verify scan_code_env_vars extracts referenced environment variables across languages."""
        # Create simulated repo files
        src_dir = self.root / "src"
        src_dir.mkdir(parents=True)

        (src_dir / "server.ts").write_text(
            "const port = process.env.PORT || 3000;\nconst db = process.env['DATABASE_URL'];\n",
            encoding="utf-8",
        )
        (src_dir / "worker.py").write_text(
            "import os\nsecret = os.environ['SECRET_KEY']\nhost = os.getenv('API_HOST')\n",
            encoding="utf-8",
        )
        (src_dir / "config.php").write_text(
            "<?php\n$redis = getenv('REDIS_HOST');\n$debug = env('APP_DEBUG');\n",
            encoding="utf-8",
        )
        (src_dir / "main.go").write_text(
            "package main\nimport \"os\"\nfunc main() { kafka := os.Getenv(\"KAFKA_BROKERS\") }\n",
            encoding="utf-8",
        )

        # Create ignored folder to test exclusion
        nm_dir = self.root / "node_modules" / "some-package"
        nm_dir.mkdir(parents=True)
        (nm_dir / "bad.js").write_text("process.env.IGNORED_NODE_VAR = 'x';", encoding="utf-8")

        result = doc_evidence.scan_code_env_vars(self.root)

        expected_vars = {"PORT", "DATABASE_URL", "SECRET_KEY", "API_HOST", "REDIS_HOST", "APP_DEBUG", "KAFKA_BROKERS"}
        for var in expected_vars:
            self.assertIn(var, result, f"Variable {var} should have been extracted.")

        self.assertNotIn("IGNORED_NODE_VAR", result)
        self.assertNotIn("PATH", result)

    def test_tool_registry_and_policy_integration(self):
        """Verify search tools are registered, categorized, and present in LLM tool definitions."""
        self.assertIn("search_web_docs", registry.TOOL_HANDLERS)
        self.assertIn("search_docker_hub", registry.TOOL_HANDLERS)

        self.assertEqual(TOOL_CATEGORY_MAP.get("search_web_docs"), "web")
        self.assertEqual(TOOL_CATEGORY_MAP.get("search_docker_hub"), "apps")

        self.assertIn("search_web_docs", definitions.APP_SETUP_TOOL_NAMES)
        self.assertIn("search_docker_hub", definitions.APP_SETUP_TOOL_NAMES)

        openai_tools = definitions.get_tool_definitions("openai_compatible")
        tool_names = {t["function"]["name"] for t in openai_tools}
        self.assertIn("search_web_docs", tool_names)
        self.assertIn("search_docker_hub", tool_names)

        anthropic_tools = definitions.get_tool_definitions("anthropic")
        anthropic_names = {t["name"] for t in anthropic_tools}
        self.assertIn("search_web_docs", anthropic_names)
        self.assertIn("search_docker_hub", anthropic_names)


if __name__ == "__main__":
    unittest.main()
