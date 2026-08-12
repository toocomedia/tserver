import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

class I18nService:
    def __init__(self):
        self.en_strings = {}
        self.fr_strings = {}
        self.french_enabled = False

    def init_app(self, base_dir: Path):
        self.locales_dir = base_dir / "locales"
        self.load_locales()

    def load_locales(self):
        en_path = self.locales_dir / "en.json"
        fr_path = self.locales_dir / "fr.json"

        # English is mandatory
        try:
            with open(en_path, "r", encoding="utf-8") as f:
                self.en_strings = json.load(f)
        except Exception as e:
            logger.critical(f"Failed to load English translations from {en_path}: {e}")
            raise RuntimeError(f"English translations are required for startup. Error: {e}")

        # French is optional/graceful failure
        try:
            with open(fr_path, "r", encoding="utf-8") as f:
                self.fr_strings = json.load(f)
            self.french_enabled = True
        except Exception as e:
            logger.error(f"Failed to load French translations from {fr_path}. French will be disabled. Error: {e}")
            self.french_enabled = False

    def get_string(self, key: str, lang: str = "en") -> str:
        """
        Fallback logic: French -> English -> Key
        """
        if lang == "fr" and self.french_enabled:
            val = self.fr_strings.get(key)
            if val is not None:
                return val
                
        val = self.en_strings.get(key)
        if val is not None:
            return val
            
        return key

    def get_plural_string(self, key: str, count: int, lang: str = "en") -> str:
        # Very basic pluralization support for v1
        # Look for key_plural if count != 1
        lookup_key = key if count == 1 else f"{key}_plural"
        
        if lang == "fr" and self.french_enabled:
            val = self.fr_strings.get(lookup_key)
            if val is not None:
                return val
            # Fallback to singular French if plural missing
            val = self.fr_strings.get(key)
            if val is not None:
                return val
                
        val = self.en_strings.get(lookup_key)
        if val is not None:
            return val
        
        # Fallback to singular English
        val = self.en_strings.get(key)
        if val is not None:
            return val
            
        return lookup_key

i18n_service = I18nService()
