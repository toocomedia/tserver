import unittest
from unittest.mock import patch

from services import process_usage_classifier


class ProcessUsageClassifierTests(unittest.TestCase):
    @patch.object(
        process_usage_classifier.config,
        "APP_HOSTING_ROOT",
        "/var/lib/srv-panel/apps",
    )
    def test_hosted_app_is_not_counted_as_panel(self):
        service = process_usage_classifier.stack_service(
            "python3",
            "/var/lib/srv-panel/apps/7/current/.venv/bin/uvicorn app.main:app",
        )
        self.assertIsNone(service)

    @patch.object(
        process_usage_classifier.config,
        "APP_HOSTING_ROOT",
        "/var/lib/srv-panel/apps",
    )
    def test_panel_process_is_still_counted(self):
        service = process_usage_classifier.stack_service(
            "python3",
            "/opt/srv-panel/venv/bin/uvicorn main:app",
        )
        self.assertEqual(service, "panel")
