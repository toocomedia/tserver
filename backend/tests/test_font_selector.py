"""Tests for appearance font selector configuration, templates, styles, and locales."""
from __future__ import annotations

import json
from pathlib import Path
import unittest

BACKEND = Path(__file__).resolve().parents[1]


class TestFontSelector(unittest.TestCase):
    def test_settings_template_contains_font_selector(self):
        template = (BACKEND / "templates" / "pages" / "settings" / "index.html").read_text(encoding="utf-8")
        self.assertIn('name="font_family"', template)
        self.assertIn('value="onest"', template)
        self.assertIn('value="inter"', template)
        self.assertIn('value="instrument-sans"', template)
        self.assertIn("font_family", template)
        self.assertIn("current_font", template)
        self.assertIn("inter_font_desc", template)
        self.assertIn("instrument_sans_desc", template)

    def test_main_css_contains_font_faces_and_tokens(self):
        css = (BACKEND / "static" / "css" / "main.css").read_text(encoding="utf-8")
        self.assertIn("font-family: 'Inter'", css)
        self.assertIn("font-family: 'Instrument Sans'", css)
        self.assertIn("/static/fonts/Inter-Variable.woff2", css)
        self.assertIn("/static/fonts/InstrumentSans-Variable.woff2", css)
        self.assertIn("--font-sans:", css)
        self.assertIn('[data-font="onest"]', css)
        self.assertIn('[data-font="inter"]', css)
        self.assertIn('[data-font="instrument-sans"]', css)
        self.assertIn(".settings-choice__font-preview", css)

    def test_layout_head_initializes_font_without_latency(self):
        layout = (BACKEND / "templates" / "layout.html").read_text(encoding="utf-8")
        self.assertIn("localStorage.getItem('panel_font')", layout)
        self.assertIn("document.documentElement.setAttribute('data-font'", layout)

    def test_settings_js_handles_font_changes(self):
        js = (BACKEND / "static" / "js" / "modules" / "settings.js").read_text(encoding="utf-8")
        self.assertIn('document.querySelectorAll(\'input[name="font_family"]\')', js)
        self.assertIn('localStorage.getItem("panel_font")', js)
        self.assertIn('localStorage.setItem("panel_font"', js)
        self.assertIn('document.documentElement.setAttribute("data-font"', js)

    def test_locales_contain_font_strings(self):
        locales = ["en", "fr", "es", "de", "ar", "tr", "ru"]
        required_keys = [
            "font_family",
            "current_font",
            "current_font_desc",
            "inter_font_desc",
            "instrument_sans_desc",
        ]
        for lang in locales:
            locale_file = BACKEND / "locales" / f"{lang}.json"
            self.assertTrue(locale_file.exists(), f"Locale file {lang}.json missing")
            data = json.loads(locale_file.read_text(encoding="utf-8"))
            for key in required_keys:
                self.assertIn(key, data, f"Key '{key}' missing from {lang}.json")
                self.assertTrue(bool(data[key]), f"Key '{key}' is empty in {lang}.json")


if __name__ == "__main__":
    unittest.main()
