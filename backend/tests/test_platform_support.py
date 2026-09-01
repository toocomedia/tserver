import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from services.platform_support_service import PlatformSupportService


class PlatformSupportServiceTests(unittest.TestCase):
    @staticmethod
    def _release_file(root: str, os_id: str, version: str, codename: str) -> Path:
        path = Path(root) / "os-release"
        path.write_text(
            "\n".join(
                (
                    f"ID={os_id}",
                    f'VERSION_ID="{version}"',
                    f"VERSION_CODENAME={codename}",
                    f'PRETTY_NAME="{os_id.title()} {version}"',
                    "",
                )
            ),
            encoding="utf-8",
        )
        return path

    def _probe(self, os_id: str, version: str, arch: str = "amd64"):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        release = self._release_file(temporary.name, os_id, version, "test")
        environment = patch.dict(
            os.environ,
            {"SRV_OS_RELEASE_FILE": str(release), "SRV_OS_ARCH": arch},
        )
        environment.start()
        self.addCleanup(environment.stop)
        return PlatformSupportService()

    def test_linux_distributions_amd64_and_arm64_are_supported(self):
        cases = (
            ("ubuntu", "22.04", "amd64"),
            ("ubuntu", "24.04", "amd64"),
            ("ubuntu", "24.10", "amd64"),
            ("ubuntu", "26.04", "arm64"),
            ("debian", "12", "amd64"),
            ("debian", "13", "arm64"),
            ("almalinux", "9", "amd64"),
            ("rocky", "9", "arm64"),
        )
        for os_id, version, arch in cases:
            with self.subTest(platform=f"{os_id}:{version}:{arch}"):
                info = self._probe(os_id, version, arch).get(force=True)
                self.assertTrue(info["supported"])
                self.assertEqual(info["arch"], arch)
                self.assertIsNone(info["error"])
                for capability in (
                    "core",
                    "docker",
                    "php",
                    "mariadb",
                    "postgresql",
                    "railpack_apps",
                    "native_python",
                ):
                    self.assertIn(capability, info["capabilities"])

    def test_unsupported_32bit_architectures_are_rejected(self):
        cases = (
            ("ubuntu", "24.04", "i386"),
            ("debian", "12", "armv7l"),
            ("debian", "12", "mips"),
        )
        for os_id, version, arch in cases:
            with self.subTest(platform=f"{os_id}:{version}:{arch}"):
                info = self._probe(os_id, version, arch).get(force=True)
                self.assertFalse(info["supported"])
                self.assertIn("Unsupported CPU architecture", info["error"])

    def test_install_guide_preserves_contract_and_reports_platform(self):
        service = self._probe("debian", "13")
        guide = service.install_guide("docker", "sudo command", "warning")
        self.assertTrue(guide["supported"])
        self.assertEqual(guide["platform"], "Debian 13")
        self.assertIsNone(guide["unsupported_reason"])
        self.assertEqual(guide["command"], "sudo command")
        self.assertEqual(guide["warning"], "warning")

    def test_plugin_support_always_verifies_supported_hosts(self):
        service = self._probe("ubuntu", "24.04")
        supported, error = service.plugin_support()
        self.assertTrue(supported)
        self.assertIsNone(error)


if __name__ == "__main__":
    unittest.main()
