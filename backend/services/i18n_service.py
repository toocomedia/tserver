import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

class I18nService:
    RTL_LANGUAGES = {"ar", "fa", "he", "ur"}

    def __init__(self):
        self.locales = {}
        self.language_names = {}
        self.en_strings = {}

    def is_rtl(self, lang: str) -> bool:
        return (lang or "").lower() in self.RTL_LANGUAGES

    def get_direction(self, lang: str) -> str:
        return "rtl" if self.is_rtl(lang) else "ltr"

    def init_app(self, base_dir: Path):
        self.locales_dir = base_dir / "locales"
        self.load_locales()

    def load_locales(self):
        self.locales.clear()
        self.language_names.clear()
        if not hasattr(self, "locales_dir") or not self.locales_dir.exists():
            logger.error("Locales directory not found")
            return

        # 1. Load languages.json manifest if present
        manifest_path = self.locales_dir / "languages.json"
        if manifest_path.exists():
            try:
                with open(manifest_path, "r", encoding="utf-8") as f:
                    self.language_names = json.load(f)
            except Exception as e:
                logger.error(f"Failed to load languages.json manifest: {e}")

        # 2. Load all locale files (*.json except languages.json)
        for json_file in self.locales_dir.glob("*.json"):
            if json_file.name == "languages.json":
                continue
            lang = json_file.stem.lower()
            try:
                with open(json_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.locales[lang] = data
                    # Fallback to key or stem if not in manifest
                    if lang not in self.language_names:
                        self.language_names[lang] = data.get(lang, data.get("language_name", lang.upper()))
            except Exception as e:
                logger.error(f"Failed to load translations from {json_file}: {e}")

        # English is mandatory fallback
        self.en_strings = self.locales.get("en", {})
        if not self.en_strings:
            logger.critical("English translations (en.json) missing or empty.")

    def get_available_languages(self) -> dict[str, str]:
        """
        Returns a dictionary of {lang_code: display_name} for all loaded locales.
        Example: {"en": "English", "fr": "Français", "ru": "Русский"}
        """
        return {code: self.language_names.get(code, code.upper()) for code in self.locales.keys()}

    @property
    def french_enabled(self) -> bool:
        return "fr" in self.locales

    @property
    def fr_strings(self) -> dict:
        return self.locales.get("fr", {})

    def get_string(self, key: str, lang: str = "en") -> str:
        """
        Fallback logic: Requested lang -> English -> Key
        """
        lang_strings = self.locales.get(lang, {})
        val = lang_strings.get(key)
        if val is not None:
            return val
                
        val = self.en_strings.get(key)
        if val is not None:
            return val
            
        return key

    def get_plural_string(self, key: str, count: int, lang: str = "en") -> str:
        lookup_key = key if count == 1 else f"{key}_plural"
        lang_strings = self.locales.get(lang, {})
        
        val = lang_strings.get(lookup_key)
        if val is not None:
            return val
            
        val = lang_strings.get(key)
        if val is not None:
            return val
                
        val = self.en_strings.get(lookup_key)
        if val is not None:
            return val
        
        val = self.en_strings.get(key)
        if val is not None:
            return val
            
        return lookup_key

i18n_service = I18nService()
