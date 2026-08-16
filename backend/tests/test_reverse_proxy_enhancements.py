"""
tests/test_reverse_proxy_enhancements.py — Unit and integration tests for reverse proxy enhancements:
1. Root domain proxying (optional empty subdomain)
2. Domain, hostname, and localhost targets (not just IPs)
3. Nginx HTTPS SNI and upstream generation
"""
import unittest
from unittest.mock import AsyncMock, patch
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import select

from database import Base
from models.domain import Domain
from models.proxy import ReverseProxy
from utils.validators import is_valid_target_host, sanitize_subdomain_label
from utils.nginx_templates import reverse_proxy_config, reverse_proxy_ssl_config
from services import proxy_service


class TestReverseProxyEnhancements(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(
            bind=self.engine, class_=AsyncSession, expire_on_commit=False
        )

    async def asyncTearDown(self):
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await self.engine.dispose()

    def test_target_host_validation(self):
        """Verify that is_valid_target_host allows IPs, localhost, domains, and container names."""
        # Valid IPs
        self.assertTrue(is_valid_target_host("127.0.0.1"))
        self.assertTrue(is_valid_target_host("192.168.1.100"))
        self.assertTrue(is_valid_target_host("::1"))

        # Valid localhost & hostnames
        self.assertTrue(is_valid_target_host("localhost"))
        self.assertTrue(is_valid_target_host("LOCALHOST"))
        self.assertTrue(is_valid_target_host("api-service"))
        self.assertTrue(is_valid_target_host("web_backend"))
        self.assertTrue(is_valid_target_host("app_container_1"))

        # Valid domain names
        self.assertTrue(is_valid_target_host("api.upstream.com"))
        self.assertTrue(is_valid_target_host("myapp.railway.app"))
        self.assertTrue(is_valid_target_host("backend.internal.org"))

        # Invalid targets
        self.assertFalse(is_valid_target_host(""))
        self.assertFalse(is_valid_target_host("   "))
        self.assertFalse(is_valid_target_host("http://api.upstream.com"))
        self.assertFalse(is_valid_target_host("target host with spaces"))

    def test_subdomain_label_optional(self):
        """Verify that sanitize_subdomain_label allows empty string or @ when allow_empty=True."""
        self.assertEqual(sanitize_subdomain_label("", allow_empty=True), "")
        self.assertEqual(sanitize_subdomain_label("@", allow_empty=True), "")
        self.assertEqual(sanitize_subdomain_label("  ", allow_empty=True), "")
        self.assertEqual(sanitize_subdomain_label("api", allow_empty=True), "api")
        self.assertEqual(sanitize_subdomain_label("APP-1", allow_empty=True), "app-1")

        with self.assertRaises(ValueError):
            sanitize_subdomain_label("invalid.with.dots", allow_empty=True)

    def test_nginx_templates_with_domain_and_https(self):
        """Verify Nginx config generation includes proxy_ssl_server_name for HTTPS domains."""
        config_http = reverse_proxy_config("proxy.example.com", "127.0.0.1", 8080, "http")
        self.assertIn("proxy_pass", config_http)
        self.assertIn("server 127.0.0.1:8080;", config_http)
        self.assertNotIn("proxy_ssl_server_name", config_http)

        config_https_domain = reverse_proxy_config("proxy.example.com", "api.upstream.com", 443, "https")
        self.assertIn("server api.upstream.com:443;", config_https_domain)
        self.assertIn("proxy_ssl_server_name on;", config_https_domain)
        self.assertIn("proxy_ssl_name        api.upstream.com;", config_https_domain)

        config_ssl = reverse_proxy_ssl_config(
            "proxy.example.com", "api.upstream.com", 443, "https",
            "/etc/ssl/cert.pem", "/etc/ssl/key.pem"
        )
        self.assertIn("server api.upstream.com:443;", config_ssl)
        self.assertIn("proxy_ssl_server_name on;", config_ssl)
        self.assertIn("proxy_ssl_name        api.upstream.com;", config_ssl)

    async def test_create_managed_proxy_on_root_domain(self):
        """Test creating a reverse proxy directly on a root parent domain (subdomain='')."""
        async with self.session_factory() as session:
            # Create parent domain
            parent = Domain(name="myrootsite.com", server_ip="1.2.3.4", project_type="static", dns_zone_created=True)
            session.add(parent)
            await session.commit()
            await session.refresh(parent)

            with patch("services.dns_service.add_record", new_callable=AsyncMock) as mock_dns, \
                 patch("services.nginx_service.ensure_acme_root"), \
                 patch("services.nginx_service.create_proxy", new_callable=AsyncMock, return_value="/etc/nginx/sites-available/myrootsite.com.conf"), \
                 patch("services.nginx_service.reload", new_callable=AsyncMock), \
                 patch("services.nginx_service.config_exists", return_value=False):

                proxy = await proxy_service.create_proxy(
                    session,
                    domain_id=parent.id,
                    subdomain="",  # Root domain
                    target_ip="127.0.0.1",
                    target_port=3000,
                    protocol="http",
                )

                self.assertEqual(proxy.full_domain, "myrootsite.com")
                self.assertEqual(proxy.subdomain, "")
                self.assertEqual(proxy.target_ip, "127.0.0.1")
                self.assertEqual(proxy.target_port, 3000)
                self.assertEqual(proxy.domain_id, parent.id)

                # Verify apex record was ensured
                mock_dns.assert_called_with("myrootsite.com", "@", "A", unittest.mock.ANY)

    async def test_create_managed_proxy_with_domain_target(self):
        """Test creating a reverse proxy with a domain target (backend.internal.org) and HTTPS."""
        async with self.session_factory() as session:
            parent = Domain(name="example.com", server_ip="1.2.3.4", project_type="static", dns_zone_created=True)
            session.add(parent)
            await session.commit()
            await session.refresh(parent)

            with patch("services.dns_service.add_record", new_callable=AsyncMock) as mock_dns, \
                 patch("services.nginx_service.ensure_acme_root"), \
                 patch("services.nginx_service.create_proxy", new_callable=AsyncMock, return_value="/etc/nginx/sites-available/api.example.com.conf"), \
                 patch("services.nginx_service.reload", new_callable=AsyncMock), \
                 patch("services.nginx_service.server_name_in_use", return_value=False):

                proxy = await proxy_service.create_proxy(
                    session,
                    domain_id=parent.id,
                    subdomain="api",
                    target_ip="backend.internal.org",  # Domain target!
                    target_port=443,
                    protocol="https",
                )

                self.assertEqual(proxy.full_domain, "api.example.com")
                self.assertEqual(proxy.subdomain, "api")
                self.assertEqual(proxy.target_ip, "backend.internal.org")
                self.assertEqual(proxy.target_port, 443)
                self.assertEqual(proxy.protocol, "https")

                mock_dns.assert_called_with("example.com", "api", "A", unittest.mock.ANY)

    async def test_create_external_proxy_with_domain_target(self):
        """Test creating an external reverse proxy with domain target."""
        async with self.session_factory() as session:
            with patch("services.nginx_service.ensure_acme_root"), \
                 patch("services.nginx_service.create_proxy", new_callable=AsyncMock, return_value="/etc/nginx/sites-available/external.mysite.com.conf"), \
                 patch("services.nginx_service.reload", new_callable=AsyncMock), \
                 patch("services.nginx_service.server_name_in_use", return_value=False):

                proxy = await proxy_service.create_external_proxy(
                    session,
                    hostname="external.mysite.com",
                    target_ip="upstream.railway.app",  # Domain target!
                    target_port=8080,
                    protocol="http",
                )

                self.assertEqual(proxy.full_domain, "external.mysite.com")
                self.assertEqual(proxy.target_ip, "upstream.railway.app")
                self.assertEqual(proxy.target_port, 8080)
                self.assertFalse(proxy.dns_managed)
