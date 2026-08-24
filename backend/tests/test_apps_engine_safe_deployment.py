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
        self.assertIn('action not in {"deploy", "redeploy", "retry", "rebuild", "rollback"}', source)

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
        self.assertIn("hero-app-box", hero)
        self.assertNotIn("data-ai-diagnose-app", hero)

    def test_setup_handoff_requires_a_reviewed_plan_and_never_unlocks_deployment_secrets(self):
        tool = (BACKEND / "plugins" / "ai_helper" / "tools" / "app_setup.py").read_text(encoding="utf-8")
        registry = (BACKEND / "plugins" / "ai_helper" / "tools" / "registry.py").read_text(encoding="utf-8")
        prompt = (BACKEND / "plugins" / "ai_helper" / "prompts" / "skills" / "app_deploy.py").read_text(encoding="utf-8")
        chat = (BACKEND / "plugins" / "ai_helper" / "services" / "chat.py").read_text(encoding="utf-8")
        markdown = (BACKEND / "plugins" / "ai_helper" / "static" / "js" / "modules" / "chat_markdown.js").read_text(encoding="utf-8")
        actions = (BACKEND / "plugins" / "ai_helper" / "static" / "js" / "modules" / "chat_actions.js").read_text(encoding="utf-8")
        definitions = (BACKEND / "plugins" / "ai_helper" / "tools" / "definitions.py").read_text(encoding="utf-8")
        self.assertIn("_SUPPORTED_GIT_BUILD_MODES", tool)
        self.assertIn("Docker Compose and multi-service stacks", tool)
        self.assertIn("_resolve_stack_manifest_images", tool)
        self.assertIn("_needs_digest_resolution", tool)
        self.assertIn("_normalize_database_kind", tool)
        self.assertIn("_single_app_source_error", tool)
        inspector = (BACKEND / "services" / "container_app_inspection_service.py").read_text(encoding="utf-8")
        self.assertIn('"clickhouse"', inspector)
        self.assertIn('"mix.exs"', inspector)
        self.assertIn('"secret_requirements"', definitions)
        self.assertIn('"panel_postgres"', definitions)
        self.assertIn("APP_SETUP_TOOL_NAMES", definitions)
        self.assertIn('"propose_container_app_patch"', registry)
        self.assertIn('"propose_app_install"', registry)
        self.assertIn("Make exactly one review plan", prompt)
        self.assertIn("propose_stack_install", prompt)
        self.assertIn("Do not fetch external documentation", prompt)
        self.assertIn("Never emit a credential-unlock action tag", prompt)
        self.assertIn("APP_SETUP_PLAN", chat)
        self.assertIn("requires_reviewed_plan", chat)
        self.assertIn("missing_plan_message", chat)
        self.assertIn("SETUP_TOOL_NAMES", chat)
        self.assertNotIn("force_tool_name", chat)
        self.assertIn("VisibleOutputFilter", chat)
        self.assertIn("UNLOCK_SENSITIVE_FILE", chat)
        self.assertIn("APP_SETUP_PLAN", markdown)
        self.assertIn("Deploy reviewed setup", markdown)
        self.assertIn("UNLOCK_SENSITIVE_FILE", markdown)
        self.assertNotIn("Thought Process", markdown)
        self.assertIn("[data-action='APP_SETUP_PLAN']", actions)
        self.assertIn("/plugins/railpack_apps/deploy-reviewed-plan/", actions)
        self.assertIn("monitorDeploymentInChat", actions)
        self.assertNotIn("window.applyAiAppPlan(data.plan)", actions)
        self.assertIn('var hasSetupPlan = bubble.querySelector(".ai-app-plan-card")', actions)
        self.assertIn('bubble.classList.remove("ai-msg-bubble--collapsible")', actions)
        create_router = (BACKEND / "plugins" / "railpack_apps" / "router_create.py").read_text(encoding="utf-8")
        deploy_helper = (BACKEND / "services" / "apps_engine" / "reviewed_setup_deploy.py").read_text(encoding="utf-8")
        self.assertIn("/deploy-reviewed-plan/{plan_id}", create_router)
        self.assertIn("reviewed_setup_deploy.deploy_plan", create_router)
        self.assertIn("_database_attachments", deploy_helper)
        self.assertIn("_reject_unsafe_single_app_source", deploy_helper)


if __name__ == "__main__":
    unittest.main()
