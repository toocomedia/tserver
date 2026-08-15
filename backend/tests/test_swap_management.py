import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from routers.system import (
    SwapConfigIn,
    set_swap_size,
    clean_ram_cache,
    clean_swap_cache,
    _get_optimization_status,
)


class SwapManagementTests(unittest.IsolatedAsyncioTestCase):
    async def test_swap_config_validation(self):
        valid = SwapConfigIn(size_mb=2048)
        self.assertEqual(valid.size_mb, 2048)

        # Out of bounds check in route
        res_negative = await set_swap_size(SwapConfigIn(size_mb=-1))
        self.assertFalse(res_negative["success"])

        res_oversized = await set_swap_size(SwapConfigIn(size_mb=50000))
        self.assertFalse(res_oversized["success"])

    @patch("routers.system.run", new_callable=AsyncMock)
    async def test_set_swap_size_success(self, mock_run):
        mock_res = MagicMock()
        mock_res.success = True
        mock_res.stdout = "==> Swapfile successfully configured to 2048 MB."
        mock_res.stderr = ""
        mock_run.return_value = mock_res

        result = await set_swap_size(SwapConfigIn(size_mb=2048))
        self.assertTrue(result["success"])
        self.assertIn("2048 MB", result["detail"])

    @patch("routers.system.run", new_callable=AsyncMock)
    async def test_clean_ram_cache_success(self, mock_run):
        mock_res = MagicMock()
        mock_res.success = True
        mock_res.stdout = '{"success": true, "freed_mb": 128, "available_ram_mb": 450, "detail": "RAM pagecache safely purged. Reclaimed 128 MB."}'
        mock_res.stderr = ""
        mock_run.return_value = mock_res

        result = await clean_ram_cache()
        self.assertTrue(result["success"])
        self.assertEqual(result["freed_mb"], 128)

    @patch("routers.system.run", new_callable=AsyncMock)
    async def test_clean_swap_cache_safe_purge(self, mock_run):
        mock_res = MagicMock()
        mock_res.success = True
        mock_res.stdout = '{"success": true, "purged": true, "freed_swap_mb": 350, "detail": "Swap refreshed successfully. Freed 350 MB from swap."}'
        mock_res.stderr = ""
        mock_run.return_value = mock_res

        result = await clean_swap_cache()
        self.assertTrue(result["success"])
        self.assertTrue(result["purged"])
        self.assertEqual(result["freed_swap_mb"], 350)

    @patch("routers.system.run", new_callable=AsyncMock)
    async def test_clean_swap_cache_skipped_safety(self, mock_run):
        mock_res = MagicMock()
        mock_res.success = True
        mock_res.stdout = '{"success": false, "purged": false, "skipped_safety": true, "available_ram_mb": 150, "used_swap_mb": 400, "detail": "Safety hold: Available RAM is too low."}'
        mock_res.stderr = ""
        mock_run.return_value = mock_res

        result = await clean_swap_cache()
        self.assertFalse(result["success"])
        self.assertFalse(result["purged"])
        self.assertTrue(result["skipped_safety"])

    @patch("routers.system.Path.is_file")
    @patch("routers.system.Path.exists")
    async def test_optimization_status_includes_swap(self, mock_exists, mock_is_file):
        mock_exists.return_value = False
        mock_is_file.return_value = False

        status = await _get_optimization_status()
        self.assertIn("swapfile_size_mb", status)
        self.assertIn("can_safely_purge_swap", status)


if __name__ == "__main__":
    unittest.main()
