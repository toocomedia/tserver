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

    def test_plugin_translation_loading(self):
        from pathlib import Path
        import config
        i18n_service.init_app(config.BASE_DIR)
        # Verify file_manager plugin translations are loaded for English and Arabic
        self.assertIn("select_target", i18n_service.en_strings)
        self.assertEqual(i18n_service.get_string("select_target", "en"), "Select Target")
        self.assertEqual(i18n_service.get_string("select_target", "ar"), "اختر الهدف")
        self.assertEqual(i18n_service.get_string("File Manager", "ar"), "مدير الملفات")

if __name__ == "__main__":
    unittest.main()
