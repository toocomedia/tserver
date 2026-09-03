import unittest
from unittest.mock import AsyncMock, patch, MagicMock
from utils.search_and_bulk import BulkActionRequest, execute_bulk_action


class TestBulkActionsSchema(unittest.TestCase):
    def test_bulk_action_request_item_ids(self):
        req = BulkActionRequest(action="delete", item_ids=[1, 2, 3])
        self.assertEqual(req.action, "delete")
        self.assertEqual(req.item_ids, [1, 2, 3])
        self.assertEqual(req.ids, [1, 2, 3])
        self.assertEqual(req.target_ids, [1, 2, 3])

    def test_bulk_action_request_ids_alias(self):
        req = BulkActionRequest(action="restart", ids=[4, 5])
        self.assertEqual(req.action, "restart")
        self.assertEqual(req.item_ids, [4, 5])
        self.assertEqual(req.ids, [4, 5])
        self.assertEqual(req.target_ids, [4, 5])


class TestBulkEndpoints(unittest.IsolatedAsyncioTestCase):
    @patch("services.domain_service.delete", new_callable=AsyncMock)
    @patch("routers.domains.task_manager_service.record_completed_task", new_callable=AsyncMock)
    async def test_domains_bulk_delete(self, mock_task, mock_domain_delete):
        from routers.domains import domains_bulk_action
        from models.domain import Domain

        db = AsyncMock()
        # Mock scalars for PhpWebsite and HostedApp queries returning empty list
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        db.execute.return_value = mock_result

        payload = BulkActionRequest(action="delete", item_ids=[10, 20])
        res = await domains_bulk_action(payload, db=db)

        self.assertTrue(res["success"])
        self.assertEqual(res["processed"], 2)
        self.assertEqual(mock_domain_delete.call_count, 2)
        mock_task.assert_called_once()

    @patch("services.cascade_service.delete_reverse_proxy_full", new_callable=AsyncMock)
    @patch("routers.proxy.task_manager_service.record_completed_task", new_callable=AsyncMock)
    async def test_proxy_bulk_delete(self, mock_task, mock_cascade_delete):
        from routers.proxy import proxy_bulk_action
        from models.proxy import ReverseProxy

        db = AsyncMock()
        fake_proxy = ReverseProxy(id=1, domain_id=None, full_domain="api.example.com")
        db.get.return_value = fake_proxy

        payload = BulkActionRequest(action="delete", item_ids=[1])
        res = await proxy_bulk_action(payload, db=db)

        self.assertTrue(res["success"])
        self.assertEqual(res["processed"], 1)
        mock_cascade_delete.assert_called_once()
        mock_task.assert_called_once()

    @patch("services.app_deployment_service.cancel", new_callable=AsyncMock)
    @patch("services.app_lifecycle_service.cancel_deployment", new_callable=AsyncMock)
    @patch("services.app_lifecycle_service.run", new_callable=AsyncMock)
    @patch("services.app_dependency_service.stop_app", new_callable=AsyncMock)
    async def test_apps_bulk_stop(self, mock_stop, mock_lifecycle, mock_cancel_dep, mock_cancel_srv):
        from routers.apps import apps_bulk_action
        from models.hosted_app import HostedApp
        from models.domain import Domain

        db = AsyncMock()
        fake_app = HostedApp(id=5, service_name="my-app", domain_id=1, status="running")
        fake_domain = Domain(id=1, name="myapp.com")

        mock_result = MagicMock()
        mock_result.all.return_value = [fake_app]
        db.scalars.return_value = mock_result
        db.get.return_value = fake_domain

        payload = BulkActionRequest(action="stop", ids=[5])
        res = await apps_bulk_action(payload, db=db)

        self.assertTrue(res["success"])
        self.assertEqual(res["processed"], 1)
        mock_lifecycle.assert_called_once()

    @patch("plugins.ai_helper.service.delete_provider", new_callable=AsyncMock, return_value=True)
    @patch("plugins.ai_helper.router.task_manager_service.record_completed_task", new_callable=AsyncMock)
    async def test_ai_helper_bulk_delete(self, mock_task, mock_delete):
        from plugins.ai_helper.router import ai_helper_bulk_action

        db = AsyncMock()
        payload = BulkActionRequest(action="delete", item_ids=[1, 2])
        res = await ai_helper_bulk_action(payload, db=db)

        self.assertTrue(res["success"])
        self.assertEqual(res["processed"], 2)
        self.assertEqual(mock_delete.call_count, 2)
        mock_task.assert_called_once()

    @patch("plugins.ai_helper.service.test_provider", new_callable=AsyncMock, return_value={"success": True, "latency_ms": 120})
    @patch("plugins.ai_helper.router.task_manager_service.record_completed_task", new_callable=AsyncMock)
    async def test_ai_helper_bulk_test(self, mock_task, mock_test):
        from plugins.ai_helper.router import ai_helper_bulk_action

        db = AsyncMock()
        payload = BulkActionRequest(action="test", item_ids=[3])
        res = await ai_helper_bulk_action(payload, db=db)

        self.assertTrue(res["success"])
        self.assertEqual(res["processed"], 1)
        mock_test.assert_called_once_with(db, 3)
        mock_task.assert_called_once()

    @patch("services.php_site_service.delete_site", new_callable=AsyncMock)
    @patch("services.php_site_service.get_site", new_callable=AsyncMock)
    @patch("services.task_manager_service.task_manager_service.record_completed_task", new_callable=AsyncMock)
    async def test_php_sites_bulk_delete(self, mock_task, mock_get_site, mock_del_site):
        from routers.php_sites import php_sites_bulk_action
        from models.domain import Domain
        from models.php_website import PhpWebsite

        db = AsyncMock()
        fake_site = PhpWebsite(id=1, domain_id=10)
        fake_domain = Domain(id=10, name="example.php")
        mock_get_site.return_value = fake_site
        db.get.return_value = fake_domain

        payload = BulkActionRequest(action="delete", item_ids=[1])
        res = await php_sites_bulk_action(payload, db=db)

        self.assertTrue(res["success"])
        self.assertEqual(res["processed"], 1)
        mock_del_site.assert_called_once()
        mock_task.assert_called_once()

    @patch("services.ssl_service.renew_cert", new_callable=AsyncMock)
    @patch("services.task_manager_service.task_manager_service.record_completed_task", new_callable=AsyncMock)
    async def test_ssl_bulk_renew(self, mock_task, mock_renew):
        from routers.ssl import ssl_bulk_action

        db = AsyncMock()
        payload = BulkActionRequest(action="renew", item_ids=[11, 12])
        res = await ssl_bulk_action(payload, db=db)

        self.assertTrue(res["success"])
        self.assertEqual(res["processed"], 2)
        self.assertEqual(mock_renew.call_count, 2)
        mock_task.assert_called_once()

    @patch("plugins.mariadb_manager.router._require_managed_mariadb")
    @patch("plugins.mariadb_manager.service.mariadb_manager_service.drop_database")
    @patch("plugins.mariadb_manager.router.task_manager_service.record_completed_task", new_callable=AsyncMock)
    async def test_mariadb_bulk_delete(self, mock_task, mock_drop, mock_req):
        from plugins.mariadb_manager.router import mariadb_bulk_action

        db = AsyncMock()
        db.scalar.return_value = None  # Not owned by a PHP site

        payload = BulkActionRequest(action="delete", item_ids=["test_db_1"])
        res = await mariadb_bulk_action(payload, db=db)

        self.assertTrue(res["success"])
        self.assertEqual(res["processed"], 1)
        mock_drop.assert_called_once_with("test_db_1")
        mock_task.assert_called_once()

    @patch("plugins.postgres_manager.queries.drop_database")
    @patch("plugins.postgres_manager.router.task_manager_service.record_completed_task", new_callable=AsyncMock)
    async def test_postgres_bulk_delete(self, mock_task, mock_drop):
        from plugins.postgres_manager.router import postgres_bulk_action

        payload = BulkActionRequest(action="delete", item_ids=["pg_db_1"])
        res = await postgres_bulk_action(payload)

        self.assertTrue(res["success"])
        self.assertEqual(res["processed"], 1)
        mock_drop.assert_called_once_with("pg_db_1")
        mock_task.assert_called_once()


if __name__ == "__main__":
    unittest.main()
