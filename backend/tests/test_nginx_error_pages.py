import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from services import nginx_service


class NginxErrorPagesTests(unittest.IsolatedAsyncioTestCase):
    async def test_error_page_publisher_copies_binary_assets(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "source"
            target = Path(temp) / "target"
            source.mkdir()
            logo = b"\x89PNG\r\n\x1a\nasset"
            (source / "logo.png").write_bytes(logo)
            (source / "503.html").write_text("offline", encoding="utf-8")
            with patch("services.nginx_service.ERROR_PAGES_SOURCE", source), patch(
                "services.nginx_service.config.APP_ERROR_PAGES_ROOT", str(target)
            ):
                await nginx_service.ensure_error_pages()
            self.assertEqual(logo, (target / "logo.png").read_bytes())
            self.assertEqual("offline", (target / "503.html").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
