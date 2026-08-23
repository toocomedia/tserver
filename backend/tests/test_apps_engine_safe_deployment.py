"""Static contract tests for the App Engine safe deployment boundary."""
from __future__ import annotations

from pathlib import Path
import unittest


BACKEND = Path(__file__).resolve().parents[1]


class AppsEngineSafeDeploymentTests(unittest.TestCase):
    def test_snapshot_and_vault_are_first_class_models(self):
        snapshot = (BACKEND / "models" / "container_app_snapshot.py").read_text(encoding="utf-8")
        secrets = (BACKEND / "models" / "container_app_secret.py").read_text(encoding="utf-8")
        self.assertIn("class ContainerAppSnapshot", snapshot)
        self.assertIn("environment_encrypted", snapshot)
        self.assertIn("fingerprint", snapshot)
        self.assertIn("class ContainerAppSecret", secrets)
        self.assertIn("class ContainerAppCredential", secrets)
        self.assertIn("class ContainerAppCredentialAccess", secrets)
        baseline = (BACKEND / "services" / "apps_engine" / "baseline.py").read_text(encoding="utf-8")
        self.assertIn("Never stops, rebuilds", baseline)

    def test_deployment_uses_snapshot_and_never_places_secret_in_railpack_cli(self):
        source = (BACKEND / "services" / "container_app_deployment_service.py").read_text(encoding="utf-8")
        self.assertIn("snapshots.get_snapshot", source)
        self.assertIn("snapshots.runtime_app", source)
        self.assertIn("snapshots.materialize_environment", source)
        self.assertNotIn('command.extend(["--env", f"{key}={val}"])', source)
        self.assertIn("env=build_env", source)
        self.assertIn('action not in {"deploy", "redeploy", "retry", "rollback"}', source)

    def test_source_access_confines_files_and_filters_secrets(self):
        source = (BACKEND / "services" / "apps_engine" / "source_access.py").read_text(encoding="utf-8")
        for required in ("relative_to(root)", "EXCLUDED_PARTS", "MAX_FILE_BYTES", "SECRET_LINE", "revision=revision"):
            self.assertIn(required, source)

    def test_ai_has_draft_only_tools_and_no_redeploy_registration(self):
        registry = (BACKEND / "plugins" / "ai_helper" / "tools" / "registry.py").read_text(encoding="utf-8")
        prompt = (BACKEND / "plugins" / "ai_helper" / "prompts" / "skills" / "app_redeploy.py").read_text(encoding="utf-8")
        self.assertIn('"propose_container_app_patch"', registry)
        self.assertNotIn('"redeploy_app":', registry)
        self.assertIn("_audit_arguments", registry)
        self.assertIn("[REDACTED]", registry)
        self.assertIn("Never deploy", prompt)
        self.assertIn("untrusted data", prompt)

    def test_page_has_outside_chat_snapshot_controls_and_masked_credentials(self):
        partial = (BACKEND / "plugins" / "railpack_apps" / "templates" / "railpack_apps" / "partials" / "detail_deployment_changes.html").read_text(encoding="utf-8")
        hero = (BACKEND / "plugins" / "railpack_apps" / "templates" / "railpack_apps" / "partials" / "detail_hero.html").read_text(encoding="utf-8")
        for action in ("Apply changes", "Deploy candidate", "Retry same snapshot", "Rollback", "Discard plan", "Start"):
            self.assertIn(action, partial)
        self.assertIn("••••••••", partial)
        self.assertIn("data-credential-show", partial)
        self.assertNotIn("/deploy", hero)
        self.assertNotIn("data-ai-diagnose-app", hero)

    def test_setup_handoff_prefills_only_supported_single_container_plans(self):
        tool = (BACKEND / "plugins" / "ai_helper" / "tools" / "app_setup.py").read_text(encoding="utf-8")
        registry = (BACKEND / "plugins" / "ai_helper" / "tools" / "registry.py").read_text(encoding="utf-8")
        prompt = (BACKEND / "plugins" / "ai_helper" / "prompts" / "skills" / "app_deploy.py").read_text(encoding="utf-8")
        chat = (BACKEND / "plugins" / "ai_helper" / "services" / "chat.py").read_text(encoding="utf-8")
        markdown = (BACKEND / "plugins" / "ai_helper" / "static" / "js" / "modules" / "chat_markdown.js").read_text(encoding="utf-8")
        actions = (BACKEND / "plugins" / "ai_helper" / "static" / "js" / "modules" / "chat_actions.js").read_text(encoding="utf-8")
        definitions = (BACKEND / "plugins" / "ai_helper" / "tools" / "definitions.py").read_text(encoding="utf-8")
        self.assertIn("_SUPPORTED_GIT_BUILD_MODES", tool)
        self.assertIn("Docker Compose and multi-service stacks", tool)
        self.assertIn('"secret_requirements"', definitions)
        self.assertIn('{"propose_container_app_patch", "propose_app_install"}', registry)
        self.assertIn("For every supported app, call propose_app_install exactly once", prompt)
        self.assertIn("APP_SETUP_PLAN", chat)
        self.assertIn("APP_SETUP_PLAN", markdown)
        self.assertIn("[data-action='APP_SETUP_PLAN']", actions)
        self.assertIn("window.applyAiAppPlan(data.plan)", actions)
        self.assertIn('window.location.href = "/plugins/railpack_apps/create?plan="', actions)
        self.assertIn('var hasSetupPlan = bubble.querySelector(".ai-app-plan-card")', actions)
        self.assertIn('bubble.classList.remove("ai-msg-bubble--collapsible")', actions)


if __name__ == "__main__":
    unittest.main()
