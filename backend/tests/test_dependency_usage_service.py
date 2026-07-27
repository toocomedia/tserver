import sys
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from services import dependency_usage_service


class DependencyUsageServiceTests(unittest.TestCase):
    def test_registered_dependencies_are_listed_except_stack_services(self):
        statuses = [
            {"id": "docker", "name": "Docker Engine", "running": True},
            {"id": "postgresql", "name": "PostgreSQL", "running": True,
             "detected_version": "psql (PostgreSQL) 17", "effective_state": "healthy"},
            {"id": "future_runtime", "name": "Future Runtime", "running": False,
             "detected_version": None, "effective_state": "stopped"},
        ]

        rows = dependency_usage_service._rows(statuses)

        self.assertEqual(["PostgreSQL", "Future Runtime"], [row["label"] for row in rows])
        self.assertEqual("Shared server runtime.", rows[0]["details"])
        self.assertEqual("Runtime is not currently running.", rows[1]["details"])


if __name__ == "__main__":
    unittest.main()
