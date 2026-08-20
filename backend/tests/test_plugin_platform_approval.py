import json
import sys
import tempfile
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from services.plugin_platform_approval_service import PluginPlatformApprovalService


class PluginPlatformApprovalServiceTests(unittest.TestCase):
    def test_approval_is_persisted_per_plugin_and_platform(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "approvals.json"
            service = PluginPlatformApprovalService(path)

            self.assertFalse(service.is_approved("maddy", "ubuntu:26.04"))
            service.approve("maddy", "ubuntu:26.04")
            service.approve("maddy", "ubuntu:26.04")

            self.assertTrue(service.is_approved("maddy", "ubuntu:26.04"))
            self.assertFalse(service.is_approved("maddy", "debian:13"))
            self.assertFalse(service.is_approved("wireguard", "ubuntu:26.04"))
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["approvals"]["maddy"], ["ubuntu:26.04"])


if __name__ == "__main__":
    unittest.main()
