import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from services import dependency_usage_service


class DependencyUsageServiceTests(unittest.TestCase):
    def test_registered_dependencies_are_listed_except_stack_services(self):
        statuses = [
            {"id": "docker", "name": "Docker Engine", "running": True},
            {"id": "postgresql", "name": "PostgreSQL", "running": True,
             "usage": {"process_names": ["postgres"]},
             "detected_version": "psql (PostgreSQL) 17", "effective_state": "healthy"},
            {"id": "future_runtime", "name": "Future Runtime", "running": False,
             "detected_version": None, "effective_state": "stopped"},
        ]

        processes = [{"name": "postgres", "cpu_percent": 3.5,
                      "memory_info": SimpleNamespace(rss=20 * 1024 ** 2)}]
        rows = dependency_usage_service._rows(statuses, processes, 100 * 1024 ** 2)

        self.assertEqual(["PostgreSQL", "Future Runtime"], [row["label"] for row in rows])
        self.assertEqual(1, rows[0]["count"])
        self.assertEqual(3.5, rows[0]["cpu"])
        self.assertEqual("20 MB (20.0% of server)", rows[0]["memory"])
        self.assertEqual("—", rows[1]["memory"])

    def test_php_fpm_versioned_processes_are_matched(self):
        statuses = [
            {"id": "php", "name": "PHP Runtime", "running": True,
             "usage": {"process_names": ["php-fpm", "php-fpm8.3", "php-fpm8.5"]},
             "detected_version": "8.3, 8.5", "effective_state": "healthy"},
        ]

        processes = [
            {"name": "php-fpm8.3", "cpu_percent": 1.2, "memory_info": SimpleNamespace(rss=30 * 1024 ** 2)},
            {"name": "php-fpm8.5", "cpu_percent": 0.8, "memory_info": SimpleNamespace(rss=20 * 1024 ** 2)},
        ]
        rows = dependency_usage_service._rows(statuses, processes, 1000 * 1024 ** 2)
        self.assertEqual(1, len(rows))
        self.assertEqual("PHP Runtime", rows[0]["label"])
        self.assertEqual(2, rows[0]["count"])
        self.assertEqual(2.0, rows[0]["cpu"])
        self.assertIn("50 MB", rows[0]["memory"])


if __name__ == "__main__":
    unittest.main()

