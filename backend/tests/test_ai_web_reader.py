"""
test_ai_web_reader.py — Unit tests for SSRF protection, IP validation, and HTML sanitization in web_reader tool.
"""
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from plugins.ai_helper.tools import web_reader


class TestAiWebReader(unittest.IsolatedAsyncioTestCase):
    def test_html_to_clean_markdown(self):
        """Verify HTML tags, scripts, and navigation are stripped and formatted as markdown."""
        html_input = """
        <html>
        <head><title>Test App</title><script>alert('xss');</script><style>body{color:red;}</style></head>
        <body>
            <nav><a href="/">Home</a></nav>
            <h1>Installation Guide</h1>
            <p>Deploy using Docker:</p>
            <pre><code>docker run -d -p 3000:3000 myapp:latest</code></pre>
            <ul>
                <li>Requires Node 20</li>
                <li>Uses PostgreSQL database</li>
            </ul>
            <footer>Copyright 2026</footer>
        </body>
        </html>
        """
        markdown = web_reader._html_to_clean_markdown(html_input)
        self.assertNotIn("<script>", markdown)
        self.assertNotIn("alert('xss')", markdown)
        self.assertNotIn("<style>", markdown)
        self.assertNotIn("Home", markdown)  # <nav> stripped
        self.assertNotIn("Copyright", markdown)  # <footer> stripped
        self.assertIn("# Installation Guide", markdown)
        self.assertIn("```\ndocker run -d -p 3000:3000 myapp:latest\n```", markdown)
        self.assertIn("- Requires Node 20", markdown)

    async def test_non_https_blocked(self):
        """Verify that non-HTTPS URLs (http, file, gopher) are rejected."""
        res_http = await web_reader.fetch_web_documentation("http://example.com/docs")
        self.assertEqual(res_http["status"], "error")
        self.assertIn("HTTPS", res_http["message"])

        res_file = await web_reader.fetch_web_documentation("file:///etc/passwd")
        self.assertEqual(res_file["status"], "error")
        self.assertIn("HTTPS", res_file["message"])

    def test_ssrf_ip_ranges_blocked(self):
        """Verify that loopback, RFC1918 private, link-local, and metadata IPs are rejected."""
        disallowed_hosts = [
            "127.0.0.1",
            "10.0.0.1",
            "172.16.0.5",
            "192.168.1.1",
            "169.254.169.254",  # AWS/Cloud metadata
            "::1",
            "0.0.0.0",
        ]
        for host in disallowed_hosts:
            with self.assertRaises(ValueError, msg=f"Host {host} should have been blocked"):
                web_reader._validate_and_resolve_host(host)

    async def test_cloudflare_403_graceful_handling(self):
        """Verify 403 / Cloudflare challenge returns a helpful user-facing message."""
        mock_response = AsyncMock()
        mock_response.status_code = 403

        with patch("httpx.AsyncClient.get", return_value=mock_response), \
             patch("plugins.ai_helper.tools.web_reader._validate_and_resolve_host", return_value=["93.184.216.34"]):
            res = await web_reader.fetch_web_documentation("https://protected-site.com/docs")
            self.assertEqual(res["status"], "blocked")
            self.assertIn("docker-compose.yml", res["message"])


if __name__ == "__main__":
    unittest.main()
