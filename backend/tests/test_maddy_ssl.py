import sys
import unittest
from pathlib import Path


BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


class MaddySslPackagingTests(unittest.TestCase):
    def test_certificate_sync_uses_narrow_helper_and_background_route(self):
        plugin = BACKEND / "plugins" / "maddy"
        helper = (plugin / "scripts" / "manage_maddy.py").read_text(encoding="utf-8")
        service = (plugin / "service.py").read_text(encoding="utf-8")
        router = (plugin / "router.py").read_text(encoding="utf-8")
        updater = (BACKEND.parent / "scripts" / "update.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn("def sync_certificate(mail_host: str)", helper)
        self.assertIn("def _certificate_pairs()", helper)
        self.assertIn("def remove_certificate(mail_host: str)", helper)
        self.assertIn("Certificate hostname is not a configured Maddy domain.", helper)
        self.assertIn("Maddy SNI updated", helper)
        self.assertIn('"sync-cert"', service)
        self.assertNotIn('"sudo", "-n", "bash", "-c"', router)
        self.assertIn("asyncio.create_task(_issue_mail_ssl_task(domain))", router)
        self.assertIn('@router.get("/api/ssl/status")', router)
        self.assertIn("MADDY_MANAGE_SCRIPT", updater)

    def test_renewal_hook_reuses_certificate_helper(self):
        installer = (
            BACKEND / "plugins" / "maddy" / "scripts" / "install_maddy.sh"
        ).read_text(encoding="utf-8")

        self.assertIn('sync-cert "\\$host"', installer)
        self.assertNotIn(
            "cp /etc/letsencrypt/live/\\$RENEWED_LINEAGE/fullchain.pem",
            installer,
        )


if __name__ == "__main__":
    unittest.main()
