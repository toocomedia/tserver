"""
tests/test_domain_creation_and_subdomains.py
Unit tests for domain creation, domain purpose (Website vs DNS only),
smart subdomain detection, and DNS record vs standalone zone routing.
"""
import unittest
from unittest.mock import AsyncMock, patch, MagicMock
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from database import Base
from models.domain import Domain
from services import domain_service


class TestDomainCreationAndSubdomains(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Create an in-memory SQLite engine for tests
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

    async def test_find_parent_domain_hierarchy(self):
        async with self.session_factory() as session:
            # Add root domain 'example.com'
            root = Domain(
                name="example.com",
                server_ip="1.2.3.4",
                project_type="static",
                dns_zone_created=True,
                nginx_active=True,
            )
            session.add(root)
            await session.commit()

            # Test direct subdomain 'app.example.com'
            parent, prefix = await domain_service.find_parent_domain(session, "app.example.com")
            self.assertIsNotNone(parent)
            self.assertEqual(parent.name, "example.com")
            self.assertEqual(prefix, "app")

            # Test deep subdomain 'api.v1.example.com'
            parent, prefix = await domain_service.find_parent_domain(session, "api.v1.example.com")
            self.assertIsNotNone(parent)
            self.assertEqual(parent.name, "example.com")
            self.assertEqual(prefix, "api.v1")

            # Test root domain has no parent
            parent, prefix = await domain_service.find_parent_domain(session, "example.com")
            self.assertIsNone(parent)
            self.assertIsNone(prefix)

            # Test domain with no parent in DB
            parent, prefix = await domain_service.find_parent_domain(session, "sub.otherdomain.org")
            self.assertIsNone(parent)
            self.assertIsNone(prefix)

    @patch("services.nginx_service.reload", new_callable=AsyncMock)
    @patch("services.nginx_service.create_static_site", new_callable=AsyncMock)
    @patch("services.nginx_service.create_webroot")
    @patch("services.nginx_service.ensure_acme_root")
    @patch("services.nginx_service.server_name_in_use", return_value=False)
    @patch("services.dns_service.add_a_record", new_callable=AsyncMock)
    @patch("services.dns_service.create_zone", new_callable=AsyncMock)
    async def test_create_root_website_domain(
        self,
        mock_create_zone,
        mock_add_a_record,
        mock_server_name_in_use,
        mock_ensure_acme_root,
        mock_create_webroot,
        mock_create_static_site,
        mock_reload,
    ):
        mock_create_webroot.return_value = "/var/www/mybrand.com"
        mock_create_static_site.return_value = "/etc/nginx/sites-available/mybrand.com.conf"

        async with self.session_factory() as session:
            domain = await domain_service.create(
                session,
                name="mybrand.com",
                project_type="static",
                dns_mode="new_zone",
            )
            await session.commit()

            self.assertEqual(domain.name, "mybrand.com")
            self.assertEqual(domain.project_type, "static")
            self.assertTrue(domain.dns_zone_created)
            self.assertIsNone(domain.parent_domain)
            self.assertTrue(domain.nginx_active)
            self.assertEqual(domain.webroot_path, "/var/www/mybrand.com")

            mock_create_zone.assert_awaited_once_with("mybrand.com")
            mock_add_a_record.assert_awaited_once_with("mybrand.com", "@", unittest.mock.ANY)
            mock_create_static_site.assert_awaited_once_with("mybrand.com")

    @patch("services.nginx_service.server_name_in_use", return_value=False)
    @patch("services.dns_service.add_a_record", new_callable=AsyncMock)
    @patch("services.dns_service.create_zone", new_callable=AsyncMock)
    async def test_create_dns_only_domain(
        self,
        mock_create_zone,
        mock_add_a_record,
        mock_server_name_in_use,
    ):
        async with self.session_factory() as session:
            domain = await domain_service.create(
                session,
                name="dns-only.org",
                project_type="dns",
                dns_mode="new_zone",
            )
            await session.commit()

            self.assertEqual(domain.name, "dns-only.org")
            self.assertEqual(domain.project_type, "dns")
            self.assertTrue(domain.dns_zone_created)
            self.assertFalse(domain.nginx_active)
            self.assertIsNone(domain.webroot_path)
            self.assertIsNone(domain.nginx_config_path)

            mock_create_zone.assert_awaited_once_with("dns-only.org")

    @patch("services.nginx_service.reload", new_callable=AsyncMock)
    @patch("services.nginx_service.create_static_site", new_callable=AsyncMock)
    @patch("services.nginx_service.create_webroot")
    @patch("services.nginx_service.ensure_acme_root")
    @patch("services.nginx_service.server_name_in_use", return_value=False)
    @patch("services.dns_service.add_a_record", new_callable=AsyncMock)
    @patch("services.dns_service.create_zone", new_callable=AsyncMock)
    async def test_create_subdomain_as_parent_record(
        self,
        mock_create_zone,
        mock_add_a_record,
        mock_server_name_in_use,
        mock_ensure_acme_root,
        mock_create_webroot,
        mock_create_static_site,
        mock_reload,
    ):
        mock_create_webroot.return_value = "/var/www/blog.example.com"
        mock_create_static_site.return_value = "/etc/nginx/sites-available/blog.example.com.conf"

        async with self.session_factory() as session:
            # Create parent domain first
            parent = Domain(
                name="example.com",
                server_ip="1.2.3.4",
                project_type="static",
                dns_zone_created=True,
                nginx_active=True,
            )
            session.add(parent)
            await session.commit()

            # Create subdomain as record in parent
            subdomain = await domain_service.create(
                session,
                name="blog.example.com",
                project_type="static",
                dns_mode="parent_record",
                parent_domain="example.com",
            )
            await session.commit()

            self.assertEqual(subdomain.name, "blog.example.com")
            self.assertFalse(subdomain.dns_zone_created)
            self.assertEqual(subdomain.parent_domain, "example.com")
            self.assertTrue(subdomain.nginx_active)

            # create_zone was NOT called for blog.example.com
            mock_create_zone.assert_not_called()
            # add_a_record was called with parent 'example.com' and prefix 'blog'
            mock_add_a_record.assert_awaited_once_with("example.com", "blog", unittest.mock.ANY)

    @patch("services.dns_service.delete_record", new_callable=AsyncMock)
    @patch("services.dns_service.delete_zone", new_callable=AsyncMock)
    @patch("services.nginx_service.remove_site", new_callable=AsyncMock)
    @patch("services.nginx_service.reload", new_callable=AsyncMock)
    @patch("services.nginx_service.remove_webroot")
    async def test_delete_subdomain_removes_parent_record(
        self,
        mock_remove_webroot,
        mock_reload,
        mock_remove_site,
        mock_delete_zone,
        mock_delete_record,
    ):
        async with self.session_factory() as session:
            # Subdomain record
            sub = Domain(
                name="api.example.com",
                server_ip="1.2.3.4",
                project_type="static",
                dns_zone_created=False,
                parent_domain="example.com",
                nginx_active=True,
            )
            session.add(sub)
            await session.commit()

            await domain_service.delete(session, sub.id)
            await session.commit()

            # delete_zone should NOT be called
            mock_delete_zone.assert_not_called()
            # delete_record should be called on parent 'example.com' with prefix 'api'
            mock_delete_record.assert_awaited_once_with("example.com", "api", "A")

    async def test_api_check_hostname_flow(self):
        import httpx
        from fastapi import FastAPI
        from routers.domains import router
        from database import get_db

        test_app = FastAPI()
        test_app.include_router(router)

        async with self.session_factory() as session:
            parent = Domain(
                name="parentsite.org",
                server_ip="1.2.3.4",
                project_type="static",
                dns_zone_created=True,
                nginx_active=True,
            )
            session.add(parent)
            await session.commit()

        async def override_get_db():
            async with self.session_factory() as session:
                yield session

        test_app.dependency_overrides[get_db] = override_get_db
        try:
            transport = httpx.ASGITransport(app=test_app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                # 1. Check parent itself
                res = await client.get("/domains/api/check-hostname?name=parentsite.org")
                self.assertEqual(res.status_code, 200)
                data = res.json()
                self.assertTrue(data["exists"])

                # 2. Check subdomain of parent
                res = await client.get("/domains/api/check-hostname?name=dash.parentsite.org")
                self.assertEqual(res.status_code, 200)
                data = res.json()
                self.assertFalse(data["exists"])
                self.assertTrue(data["is_subdomain"])
                self.assertEqual(data["parent_domain"], "parentsite.org")
                self.assertEqual(data["subdomain_prefix"], "dash")

                # 3. Check unrelated domain
                res = await client.get("/domains/api/check-hostname?name=newdomain.io")
                self.assertEqual(res.status_code, 200)
                data = res.json()
                self.assertFalse(data["exists"])
                self.assertFalse(data["is_subdomain"])
                self.assertIsNone(data["parent_domain"])
        finally:
            test_app.dependency_overrides.pop(get_db, None)


if __name__ == "__main__":
    unittest.main()
