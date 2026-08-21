import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class SwapPersistenceTests(unittest.TestCase):
    def test_resize_persists_the_actual_active_swap_path(self):
        script = (ROOT / "scripts" / "optimize.sh").read_text(encoding="utf-8")

        self.assertNotIn('mv "$SWAP_FILE_TMP" "$SWAP_FILE" 2>/dev/null || true', script)
        self.assertIn('persistent_swap_file="$SWAP_FILE_TMP"', script)
        self.assertIn('echo "$persistent_swap_file none swap sw 0 0" >> /etc/fstab', script)

    def test_status_counts_all_managed_disk_swap_paths(self):
        script = (ROOT / "scripts" / "optimize.sh").read_text(encoding="utf-8")

        self.assertIn('[[ "$path" == /swapfile* ]]', script)
        self.assertIn("fields[0].startswith(\"/swapfile\")", (ROOT / "backend" / "routers" / "system.py").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
