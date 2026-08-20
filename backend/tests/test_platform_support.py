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

    def test_all_guaranteed_platforms_are_supported(self):
        for os_id, version in (
            ("ubuntu", "22.04"),
            ("ubuntu", "24.04"),
            ("ubuntu", "26.04"),
            ("debian", "12"),
            ("debian", "13"),
        ):
            with self.subTest(platform=f"{os_id}:{version}"):
                info = self._probe(os_id, version).get(force=True)
                self.assertTrue(info["supported"])
                self.assertEqual(info["arch"], "amd64")
                for capability in (
                    "core",
                    "docker",
                    "php",
                    "mariadb",
                    "postgresql",
                    "railpack_apps",
                ):
                    self.assertIn(capability, info["capabilities"])

    def test_python_and_php_repository_capabilities_match_policy(self):
        ubuntu_2204 = self._probe("ubuntu", "22.04").get(force=True)
        self.assertNotIn("native_python", ubuntu_2204["capabilities"])
        self.assertIn("php_external_repository", ubuntu_2204["capabilities"])

        ubuntu_2604 = self._probe("ubuntu", "26.04").get(force=True)
        self.assertIn("native_python", ubuntu_2604["capabilities"])
        self.assertNotIn("php_external_repository", ubuntu_2604["capabilities"])
        repository_error = self._probe("ubuntu", "26.04").capability_error(
            "php_external_repository"
        )
        self.assertIn("does not publish packages", repository_error)

        debian_13 = self._probe("debian", "13").get(force=True)
        self.assertIn("native_python", debian_13["capabilities"])
        self.assertNotIn("php_external_repository", debian_13["capabilities"])

    def test_unsupported_versions_os_and_arch_have_exact_reasons(self):
        cases = (
            ("ubuntu", "20.04", "amd64", "Unsupported Ubuntu version 20.04"),
            ("ubuntu", "25.10", "amd64", "Unsupported Ubuntu version 25.10"),
            ("debian", "11", "amd64", "Unsupported Debian version 11"),
            ("rocky", "9", "amd64", "Unsupported operating system Rocky 9"),
            ("ubuntu", "24.04", "arm64", "Unsupported CPU architecture arm64"),
        )
        for os_id, version, arch, reason in cases:
            with self.subTest(platform=f"{os_id}:{version}:{arch}"):
                info = self._probe(os_id, version, arch).get(force=True)
                self.assertFalse(info["supported"])
                self.assertIn(reason, info["error"])

    def test_install_guide_preserves_contract_and_reports_platform(self):
        service = self._probe("debian", "13")
        guide = service.install_guide("docker", "sudo command", "warning")
        self.assertTrue(guide["supported"])
        self.assertEqual(guide["platform"], "Debian 13")
        self.assertIsNone(guide["unsupported_reason"])
        self.assertEqual(guide["command"], "sudo command")
        self.assertEqual(guide["warning"], "warning")

    def test_shell_and_backend_matrices_stay_aligned(self):
        helper = (BACKEND.parent / "scripts" / "os_compat.sh").read_text(
            encoding="utf-8"
        )
        for selector in (
            "ubuntu:22.04",
            "ubuntu:24.04",
            "ubuntu:26.04",
            "debian:12",
            "debian:13",
        ):
            self.assertIn(selector, helper)


if __name__ == "__main__":
    unittest.main()
