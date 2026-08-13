"""Unit tests for native PHP website live process usage metrics."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from services import php_site_usage_service


class MockSite:
    def __init__(self, id_: int, domain_id: int, php_version: str, preset: str, linux_user: str, status: str, root_path: str):
        self.id = id_
        self.domain_id = domain_id
        self.php_version = php_version
        self.preset = preset
        self.linux_user = linux_user
        self.status = status
        self.root_path = root_path


class PhpSiteUsageServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_usage_matches_by_username_pool_and_calculates_metrics(self):
        site1 = MockSite(
            id_=1,
            domain_id=1,
            php_version="8.3",
            preset="wordpress",
            linux_user="srvphp1",
            status="active",
            root_path="/var/www/myblog.com",
        )
        site2 = MockSite(
            id_=2,
            domain_id=2,
            php_version="8.2",
            preset="php",
            linux_user="srvphp2",
            status="active",
            root_path="/var/www/api.example.com",
        )

        mock_db = AsyncMock()
        mock_result = Mock()
        mock_result.all.return_value = [
            (site1, "myblog.com"),
            (site2, "api.example.com"),
        ]
        mock_db.execute.return_value = mock_result

        processes = [
            {
                "name": "php-fpm8.3",
                "username": "srvphp1",
                "cmdline": ["php-fpm: pool srv-panel-site-1"],
                "cpu_percent": 4.5,
                "memory_info": SimpleNamespace(rss=30 * 1024 ** 2),
            },
            {
                "name": "php-fpm8.3",
                "username": "srvphp1",
                "cmdline": ["php-fpm: pool srv-panel-site-1"],
                "cpu_percent": 1.5,
                "memory_info": SimpleNamespace(rss=20 * 1024 ** 2),
            },
            # unrelated process
            {
                "name": "nginx",
                "username": "www-data",
                "cmdline": ["nginx: worker process"],
                "cpu_percent": 0.5,
                "memory_info": SimpleNamespace(rss=10 * 1024 ** 2),
            },
        ]

        total_ram = 1000 * 1024 ** 2
        rows = await php_site_usage_service.get_usage(mock_db, processes, total_ram)

        self.assertEqual(2, len(rows))

        # Site 1 (myblog.com - WordPress - 2 processes running)
        self.assertEqual("myblog.com", rows[0]["label"])
        self.assertEqual("WordPress", rows[0]["service"])
        self.assertEqual(2, rows[0]["count"])
        self.assertEqual(6.0, rows[0]["cpu"])
        self.assertEqual("running", rows[0]["status"])
        self.assertIn("50 MB", rows[0]["memory"])
        self.assertEqual(5.0, rows[0]["mem"])

        # Site 2 (api.example.com - PHP 8.2 - 0 processes running, idle active pool)
        self.assertEqual("api.example.com", rows[1]["label"])
        self.assertEqual("PHP 8.2", rows[1]["service"])
        self.assertEqual(0, rows[1]["count"])
        self.assertEqual(0.0, rows[1]["cpu"])
        self.assertEqual("active", rows[1]["status"])
        self.assertIn("0 MB", rows[1]["memory"])


if __name__ == "__main__":
    unittest.main()
