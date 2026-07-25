import unittest

from plugins.postgres_manager.native_tls import should_recover_missing_certificate


class TestPostgresNativeTls(unittest.TestCase):
    def test_recovers_missing_material_when_certbot_skips_renewal(self):
        self.assertTrue(should_recover_missing_certificate(
            False, False, "Certificate not yet due for renewal; no action taken.", "",
        ))

    def test_does_not_force_renewal_when_material_exists(self):
        self.assertFalse(should_recover_missing_certificate(
            True, True, "Certificate not yet due for renewal; no action taken.", "",
        ))

    def test_does_not_force_renewal_for_other_certbot_results(self):
        self.assertFalse(should_recover_missing_certificate(False, False, "", "challenge failed"))
