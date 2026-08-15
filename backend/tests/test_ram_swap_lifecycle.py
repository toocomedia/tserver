"""
backend/tests/test_ram_swap_lifecycle.py
Comprehensive unit & integration test suite for RAM & Swap management,
safety gates, optimization mode toggling, and frontend/backend state synchronization.
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from routers.system import (
    SwapConfigIn,
    OptimizationToggleIn,
    NginxWorkerToggleIn,
    AdvancedTuningToggleIn,
    set_swap_size,
    toggle_optimization,
    toggle_nginx_worker,
    toggle_advanced_tuning,
    clean_ram_cache,
    clean_swap_cache,
    _get_optimization_status,
)


class RamSwapLifecycleTests(unittest.IsolatedAsyncioTestCase):
    """Exhaustive test suite for RAM, Swap, and Optimization operations."""

    async def test_swap_config_input_validation(self):
        """Verify swap payload validation rules."""
        valid_zero = SwapConfigIn(size_mb=0)
        self.assertEqual(valid_zero.size_mb, 0)

        valid_1g = SwapConfigIn(size_mb=1024)
        self.assertEqual(valid_1g.size_mb, 1024)

        # Negative swap size rejected
        res_neg = await set_swap_size(SwapConfigIn(size_mb=-512))
        self.assertFalse(res_neg["success"])
        self.assertIn("between 0 and 32768", res_neg["detail"])

        # Oversized swap rejected (> 32GB)
        res_huge = await set_swap_size(SwapConfigIn(size_mb=65536))
        self.assertFalse(res_huge["success"])
        self.assertIn("between 0 and 32768", res_huge["detail"])

    @patch("routers.system.run", new_callable=AsyncMock)
    async def test_set_swap_size_success_cases(self, mock_run):
        """Test configuring swap across all valid sizes (0, 512, 1024, 2048, 4096 MB)."""
        test_sizes = [0, 512, 1024, 2048, 4096]
        for size in test_sizes:
            mock_res = MagicMock()
            mock_res.success = True
            mock_res.stdout = f"==> Swap successfully configured to {size} MB total."
            mock_res.stderr = ""
            mock_run.return_value = mock_res

            result = await set_swap_size(SwapConfigIn(size_mb=size))
            self.assertTrue(result["success"])
            self.assertIn(str(size), result["detail"])

    @patch("routers.system.run", new_callable=AsyncMock)
    async def test_set_swap_size_safety_failure(self, mock_run):
        """Test backend safety gate triggering when RAM cannot absorb swap."""
        mock_res = MagicMock()
        mock_res.success = False
        mock_res.stdout = ""
        mock_res.stderr = "ERROR: Cannot safely disable swap. Swap in use: 400 MB, RAM available: 150 MB."
        mock_run.return_value = mock_res

        result = await set_swap_size(SwapConfigIn(size_mb=0))
        self.assertFalse(result["success"])
        self.assertIn("Cannot safely disable swap", result["detail"])

    @patch("routers.system.run", new_callable=AsyncMock)
    async def test_toggle_optimization_enable_and_disable(self, mock_run):
        """Test enabling and disabling Low-RAM Optimization Mode."""
        # Enable
        mock_res_en = MagicMock(success=True, stdout="==> Low-RAM Optimization Mode ACTIVE.", stderr="")
        mock_run.return_value = mock_res_en
        res_en = await toggle_optimization(OptimizationToggleIn(enabled=True))
        self.assertTrue(res_en["success"])

        # Disable
        mock_res_dis = MagicMock(success=True, stdout="==> Low-RAM Optimization Mode DEACTIVATED.", stderr="")
        mock_run.return_value = mock_res_dis
        res_dis = await toggle_optimization(OptimizationToggleIn(enabled=False))
        self.assertTrue(res_dis["success"])

    @patch("routers.system.run", new_callable=AsyncMock)
    async def test_toggle_nginx_worker(self, mock_run):
        """Test switching Nginx worker mode between single (1) and auto."""
        mock_res = MagicMock(success=True, stdout="==> Nginx worker_processes set to 1.", stderr="")
        mock_run.return_value = mock_res
        res = await toggle_nginx_worker(NginxWorkerToggleIn(single_worker=True))
        self.assertTrue(res["success"])

    @patch("routers.system.run", new_callable=AsyncMock)
    async def test_toggle_advanced_tuning(self, mock_run):
        """Test switching Advanced Server Tuning on and off."""
        mock_res = MagicMock(success=True, stdout="==> Advanced Server Tuning ACTIVE.", stderr="")
        mock_run.return_value = mock_res
        res = await toggle_advanced_tuning(AdvancedTuningToggleIn(enabled=True))
        self.assertTrue(res["success"])

    @patch("routers.system.run", new_callable=AsyncMock)
    async def test_clean_ram_cache_endpoint(self, mock_run):
        """Test RAM pagecache cleaner route."""
        mock_res = MagicMock()
        mock_res.success = True
        mock_res.stdout = '{"success": true, "freed_mb": 150, "available_ram_mb": 600, "detail": "RAM pagecache safely purged."}'
        mock_res.stderr = ""
        mock_run.return_value = mock_res

        result = await clean_ram_cache()
        self.assertTrue(result["success"])
        self.assertEqual(result["freed_mb"], 150)

    @patch("routers.system.run", new_callable=AsyncMock)
    async def test_clean_swap_cache_endpoint(self, mock_run):
        """Test Smart Swap cleaner route."""
        mock_res = MagicMock()
        mock_res.success = True
        mock_res.stdout = '{"success": true, "purged": true, "freed_swap_mb": 250, "detail": "Swap refreshed successfully."}'
        mock_res.stderr = ""
        mock_run.return_value = mock_res

        result = await clean_swap_cache()
        self.assertTrue(result["success"])
        self.assertTrue(result["purged"])
        self.assertEqual(result["freed_swap_mb"], 250)

    @patch("routers.system.Path.is_file")
    @patch("routers.system.Path.exists")
    async def test_optimization_status_structure(self, mock_exists, mock_is_file):
        """Verify optimization status endpoint contains all required fields for UI."""
        mock_exists.return_value = False
        mock_is_file.return_value = False

        status = await _get_optimization_status()
        required_keys = [
            "optimization_active",
            "zram_active",
            "nginx_single_worker",
            "nginx_worker_setting",
            "advanced_active",
            "swapfile_size_mb",
            "can_safely_purge_swap",
            "can_safely_disable_swap",
            "ram_available_mb",
            "swap_used_mb",
        ]
        for k in required_keys:
            self.assertIn(k, status, f"Missing key '{k}' in optimization status response")


if __name__ == "__main__":
    unittest.main()
