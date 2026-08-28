from __future__ import annotations

import unittest
from pathlib import Path
import sys

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from models.container_app_database import ContainerAppDatabase
from services.apps_engine.database_attachment_service import internal_service_target, target_from_record


class DatabaseAttachmentNetworkingTests(unittest.TestCase):
    def test_internal_and_private_provider_targets_use_dns_aliases(self):
        internal = internal_service_target("db", 5432)
        self.assertEqual((internal.host, internal.port), ("db", 5432))
        item = ContainerAppDatabase(
            id=4, app_id=9, kind="postgresql", provider="docker", status="ready",
            network_alias="db-postgresql",
        )
        target = target_from_record(item)
        self.assertEqual(target.host, "db-postgresql")
        self.assertEqual(target.network_name, "srv-container-net-9")

    def test_native_provider_uses_stable_host_gateway(self):
        item = ContainerAppDatabase(
            id=5, app_id=9, kind="postgresql", provider="panel_postgres", status="ready",
            network_alias="172.17.0.2",
        )
        target = target_from_record(item)
        self.assertEqual(target.host, "host.docker.internal")
        self.assertTrue(target.add_host_gateway)
        self.assertNotIn("172.17.", target.host)


if __name__ == "__main__":
    unittest.main()
