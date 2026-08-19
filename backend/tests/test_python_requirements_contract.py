import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
SCRIPTS = ROOT / "scripts"


class PythonRequirementsContractTests(unittest.TestCase):
    def test_every_supported_python_minor_has_tested_constraints(self):
        requirements = (BACKEND / "requirements.txt").read_text(encoding="utf-8")
        self.assertIn("sqlalchemy[asyncio]==2.0.51", requirements.lower())

        for version_key in ("310", "311", "312", "313", "314"):
            with self.subTest(version_key=version_key):
                constraints = (
                    BACKEND / "constraints" / f"python{version_key}.txt"
                ).read_text(encoding="utf-8")
                self.assertIn("SQLAlchemy==2.0.51", constraints)

    def test_install_and_update_use_shared_validation_helper(self):
        helper = (SCRIPTS / "python_requirements.sh").read_text(encoding="utf-8")
        install = (SCRIPTS / "install.sh").read_text(encoding="utf-8")
        update = (SCRIPTS / "update.sh").read_text(encoding="utf-8")

        self.assertIn("310|311|312|313|314", helper)
        self.assertIn("-m pip check", helper)
        self.assertIn("import sqlalchemy", helper)
        self.assertIn("srv_python_install_requirements", install)
        self.assertIn("srv_python_preflight_requirements", update)

        preflight_position = update.index("srv_python_preflight_requirements")
        deploy_position = update.index('info "Syncing application files..."')
        self.assertLess(preflight_position, deploy_position)


if __name__ == "__main__":
    unittest.main()
