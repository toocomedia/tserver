"""Safe unit tests for PostgreSQL remote access.

Every network, Certbot, firewall, PostgreSQL, and sudo operation is mocked.
Run from backend/: python -m unittest tests.test_postgres_remote -v
"""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from plugins.postgres_manager import native_tls
from plugins.postgres_manager.schemas import RemoteConfigRequest


class TestRemoteValidation(unittest.TestCase):
    def test_managed_hostname(self):
        self.assertEqual(native_tls.build_hostname("managed", "Example.COM", "db", None), ("db.example.com", True))

    def test_external_hostname(self):
        self.assertEqual(native_tls.build_hostname("external", None, None, "pg.example.com"), ("pg.example.com", False))

    def test_bad_hostname_rejected(self):
        with self.assertRaises(ValueError):
            native_tls.build_hostname("external", None, None, "bad host")

    def test_cidrs_normalized(self):
        self.assertEqual(native_tls.normalize_cidrs(["203.0.113.8/24"]), ["203.0.113.0/24"])

    def test_bad_cidr_rejected(self):
        with self.assertRaises(ValueError):
            native_tls.normalize_cidrs(["not-an-ip"])

    def test_request_defaults_to_encryption(self):
        request = RemoteConfigRequest(mode="external", hostname="pg.example.com")
        self.assertTrue(request.encryption_enabled)


class TestPrivilegedOperations(unittest.IsolatedAsyncioTestCase):
    async def test_certificate_command_is_mocked(self):
        with patch.object(native_tls, "_run", new=AsyncMock()) as run:
            name, expiry = await native_tls.issue_shared_certificate(["pg.example.com"])
        self.assertEqual((name, expiry), ("pg.example.com", None))
        self.assertIn("certbot", run.await_args.args)

    async def test_postgres_payload_uses_ssl_and_plain_rules(self):
        encrypted = SimpleNamespace(full_domain="ssl.example.com", encryption_enabled=True, allowed_cidrs="203.0.113.1/32")
        plain = SimpleNamespace(full_domain="plain.example.com", encryption_enabled=False, allowed_cidrs="0.0.0.0/0")
        with patch.object(native_tls, "_run", new=AsyncMock()) as run:
            await native_tls.configure_postgres([encrypted, plain])
        payload = run.await_args.args[-1]
        self.assertIn("hostssl", payload)
        self.assertIn("hostnossl", payload)

    async def test_firewall_commands_are_mocked(self):
        with patch.object(native_tls, "_run", new=AsyncMock()) as run:
            await native_tls.firewall_allow(["203.0.113.1/32"])
            await native_tls.firewall_remove(["203.0.113.1/32"])
        self.assertEqual(run.await_count, 2)

    async def test_sudo_password_error_has_setup_guidance(self):
        failed = MagicMock(returncode=1, stderr="sudo: a password is required", stdout="")
        with patch("plugins.postgres_manager.native_tls.subprocess.run", return_value=failed):
            with self.assertRaisesRegex(RuntimeError, "permissions are not installed"):
                await native_tls._run("sudo", "-n", "ufw", "status")


if __name__ == "__main__":
    unittest.main()
