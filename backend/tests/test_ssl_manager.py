import unittest
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime, timezone
import httpx
from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

from database import Base, get_db
from models.domain import Domain
from models.ssl_cert import SslCert
from models.proxy import ReverseProxy
from routers.ssl import router as ssl_router, _build_eligible
from services import ssl_service


class TestSslManager(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
        self.session_factory = async_sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        self.test_app = FastAPI()
        self.test_app.add_middleware(SessionMiddleware, secret_key="test-secret-key-1234")
        self.test_app.include_router(ssl_router)

        async def override_get_db():
            async with self.session_factory() as session:
                yield session

        self.test_app.dependency_overrides[get_db] = override_get_db

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_build_eligible_includes_all_domains_and_proxies(self):
        """Test _build_eligible returns registered domains regardless of project_type, excluding already issued."""
        async with self.session_factory() as db:
            d1 = Domain(name="static.example.com", server_ip="1.2.3.4", project_type="static", nginx_active=True)
            d2 = Domain(name="php.example.com", server_ip="1.2.3.4", project_type="php", nginx_active=False)
            d3 = Domain(name="has-cert.example.com", server_ip="1.2.3.4", project_type="static", nginx_active=True)
            db.add_all([d1, d2, d3])
            await db.flush()

            # Existing cert
            cert = SslCert(domain_id=d3.id, full_domain="has-cert.example.com", auto_renew=True)
            # Proxy
            proxy = ReverseProxy(
                full_domain="proxy.example.com",
                target_ip="127.0.0.1",
                target_port=8080,
                protocol="http",
                nginx_config_path="/etc/nginx/sites-available/proxy.example.com.conf",
            )
            db.add_all([cert, proxy])
            await db.commit()

            eligible = await _build_eligible(db)
            full_domains = [e["full_domain"] for e in eligible]

            self.assertIn("static.example.com", full_domains)
            self.assertIn("php.example.com", full_domains)
            self.assertIn("proxy.example.com", full_domains)
            self.assertNotIn("has-cert.example.com", full_domains)

    @patch("services.ssl_service.issue_cert", new_callable=AsyncMock)
    async def test_ssl_issue_json_request(self, mock_issue_cert):
        """Test POST /ssl/issue with JSON request returns JSON status ok."""
        mock_cert = MagicMock()
        mock_cert.full_domain = "new.example.com"
        mock_issue_cert.return_value = mock_cert

        transport = httpx.ASGITransport(app=self.test_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            res = await client.post(
                "/ssl/issue",
                json={"full_domain": "new.example.com", "auto_renew": True},
                headers={"Content-Type": "application/json", "Accept": "application/json"},
            )
            self.assertEqual(res.status_code, 200)
            data = res.json()
            self.assertEqual(data["status"], "ok")
            self.assertEqual(data["full_domain"], "new.example.com")
            mock_issue_cert.assert_awaited_once()

    @patch("services.ssl_service.renew_cert", new_callable=AsyncMock)
    async def test_ssl_renew_json_and_form(self, mock_renew_cert):
        """Test POST /ssl/{id}/renew returns JSON for API and redirect for form."""
        transport = httpx.ASGITransport(app=self.test_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            # JSON request
            res = await client.post(
                "/ssl/1/renew",
                headers={"Content-Type": "application/json", "Accept": "application/json"},
            )
            self.assertEqual(res.status_code, 200)
            self.assertEqual(res.json(), {"status": "ok"})
            mock_renew_cert.assert_awaited_with(unittest.mock.ANY, 1)

            # Form request
            res2 = await client.post("/ssl/1/renew", follow_redirects=False)
            self.assertEqual(res2.status_code, 303)
            self.assertEqual(res2.headers["location"], "/ssl/?renewed=1")

    @patch("services.ssl_service.revoke_cert", new_callable=AsyncMock)
    async def test_ssl_revoke_json_and_form(self, mock_revoke_cert):
        """Test POST /ssl/{id}/revoke returns JSON for API and redirect for form."""
        transport = httpx.ASGITransport(app=self.test_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            # JSON request
            res = await client.post(
                "/ssl/1/revoke",
                headers={"Content-Type": "application/json", "Accept": "application/json"},
            )
            self.assertEqual(res.status_code, 200)
            self.assertEqual(res.json(), {"status": "ok"})
            mock_revoke_cert.assert_awaited_with(unittest.mock.ANY, 1)

            # Form request
            res2 = await client.post("/ssl/1/revoke", follow_redirects=False)
            self.assertEqual(res2.status_code, 303)
            self.assertEqual(res2.headers["location"], "/ssl/?revoked=1")

    @patch("services.nginx_service.create_static_site", new_callable=AsyncMock)
    @patch("services.nginx_service.update_static_site_ssl", new_callable=AsyncMock)
    @patch("services.nginx_service.reload", new_callable=AsyncMock)
    @patch("services.nginx_service.ensure_acme_root_privileged", new_callable=AsyncMock)
    @patch("services.nginx_service.config_exists")
    @patch("utils.shell.run", new_callable=AsyncMock)
    async def test_issue_cert_auto_provisions_missing_http_config(
        self, mock_shell_run, mock_config_exists, mock_acme, mock_reload, mock_update_ssl, mock_create_static
    ):
        """Test ssl_service.issue_cert auto-provisions missing HTTP config if domain is in DB."""
        mock_config_exists.return_value = False
        mock_create_static.return_value = "/etc/nginx/sites-available/autofix.example.com.conf"
        mock_update_ssl.return_value = "/etc/nginx/sites-available/autofix.example.com.conf"

        # Mock certbot success
        mock_shell_result = MagicMock()
        mock_shell_result.success = True
        mock_shell_result.stdout = "Certificate issued. Expiry Date: 2026-12-01 12:00:00+00:00"
        mock_shell_result.stderr = ""
        mock_shell_run.return_value = mock_shell_result

        async with self.session_factory() as db:
            domain = Domain(name="autofix.example.com", server_ip="1.2.3.4", project_type="custom", nginx_active=False)
            db.add(domain)
            await db.commit()

            cert = await ssl_service.issue_cert(db, domain.id, "autofix.example.com", auto_renew=True)
            self.assertEqual(cert.full_domain, "autofix.example.com")
            self.assertTrue(domain.nginx_active)
            mock_create_static.assert_awaited_once_with("autofix.example.com")
            mock_acme.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
