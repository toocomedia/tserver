import tempfile
import unittest
from pathlib import Path

from services.app_project_detector import detect_project


class AppProjectDetectorTests(unittest.TestCase):
    def detect(self, files: dict[str, str]):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, content in files.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content)
            return detect_project(root)

    def test_fastapi_nested_entrypoint_is_quick_deployable(self):
        result = self.detect({
            "requirements.txt": "fastapi\nuvicorn\n",
            "app/main.py": "from fastapi import FastAPI\napp = FastAPI()\n",
        })
        self.assertEqual(result["framework"], "FastAPI")
        self.assertEqual(result["entrypoints"], ["app.main:app"])
        self.assertIn("uvicorn app.main:app", result["start_command"])
        self.assertTrue(result["can_quick_deploy"])

    def test_multiple_entrypoints_need_review(self):
        result = self.detect({
            "requirements.txt": "fastapi\n",
            "api.py": "from fastapi import FastAPI\na = FastAPI()\n",
            "admin.py": "from fastapi import FastAPI\na = FastAPI()\n",
        })
        self.assertFalse(result["can_quick_deploy"])
        self.assertIn("More than one web entrypoint was found.", result["warnings"])

    def test_environment_example_needs_review_without_values(self):
        result = self.detect({
            "requirements.txt": "fastapi\n",
            "main.py": "from fastapi import FastAPI\napp = FastAPI()\n",
            ".env.example": "SECRET_KEY=\nDATABASE_URL=\n",
        })
        self.assertEqual(result["required_environment_names"], ["DATABASE_URL", "SECRET_KEY"])
        self.assertFalse(result["can_quick_deploy"])

    def test_conda_is_rejected_from_quick_deploy(self):
        result = self.detect({
            "requirements.txt": "fastapi\n",
            "main.py": "from fastapi import FastAPI\napp = FastAPI()\n",
            "environment.yml": "name: test\n",
        })
        self.assertFalse(result["can_quick_deploy"])
        self.assertIn("Conda projects are not supported by lightweight hosting.", result["warnings"])

    def test_supported_package_manager_markers(self):
        for marker, expected in (("poetry.lock", "poetry"), ("uv.lock", "uv"), ("Pipfile", "pipenv")):
            with self.subTest(marker=marker):
                result = self.detect({marker: "", "main.py": "from fastapi import FastAPI\napp = FastAPI()\n"})
                self.assertEqual(result["package_manager"], expected)

    def test_django_uses_detected_asgi_module(self):
        result = self.detect({
            "requirements.txt": "django\nuvicorn\n",
            "manage.py": "",
            "site/asgi.py": "application = object()\n",
        })
        self.assertEqual(result["framework"], "Django")
        self.assertIn("site.asgi:application", result["start_command"])
