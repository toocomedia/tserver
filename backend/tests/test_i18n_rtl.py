import unittest
from services.i18n_service import i18n_service

class TestI18nRTL(unittest.TestCase):
    def test_rtl_language_detection(self):
        self.assertTrue(i18n_service.is_rtl("ar"))
        self.assertTrue(i18n_service.is_rtl("fa"))
        self.assertTrue(i18n_service.is_rtl("he"))
        self.assertTrue(i18n_service.is_rtl("ur"))

        self.assertEqual(i18n_service.get_direction("ar"), "rtl")
        self.assertEqual(i18n_service.get_direction("fa"), "rtl")

    def test_ltr_language_detection(self):
        self.assertFalse(i18n_service.is_rtl("en"))
        self.assertFalse(i18n_service.is_rtl("es"))
        self.assertFalse(i18n_service.is_rtl("fr"))

        self.assertEqual(i18n_service.get_direction("en"), "ltr")
        self.assertEqual(i18n_service.get_direction("es"), "ltr")

if __name__ == "__main__":
    unittest.main()
