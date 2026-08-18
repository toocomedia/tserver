"""
test_ai_helper.py — Unit and integration tests for AI Helper plugin.
"""
import asyncio
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from database import AsyncSessionLocal, init_db
from models.ai_helper import AiChatMessage, AiHelperSettings
from plugins.ai_helper import engine, prompts, service
from plugins.manager import PluginManager


class AIHelperTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await init_db()

    def test_plugin_manifest_discovery(self):
        """Verify the ai_helper plugin is discovered and valid."""
        manager = PluginManager()
        plugins = manager.discover_plugins()
        ai_plugin = next((p for p in plugins if p["id"] == "ai_helper"), None)
        self.assertIsNotNone(ai_plugin)
        self.assertEqual(ai_plugin["name"], "AI Assistant")
        self.assertEqual(ai_plugin["route_prefix"], "/plugins/ai_helper")
        self.assertTrue(ai_plugin["sidebar"])

    def test_encryption_and_decryption(self):
        """Verify API keys encrypt and decrypt accurately."""
        test_key = "sk-test-secret-key-1234567890abcdef"
        encrypted = service.encrypt_key(test_key)
        self.assertNotEqual(test_key, encrypted)
        decrypted = service.decrypt_key(encrypted)
        self.assertEqual(test_key, decrypted)

        # Empty keys
        self.assertEqual(service.encrypt_key(""), "")
        self.assertEqual(service.decrypt_key(""), "")

    def test_prompt_builder(self):
        """Verify system prompt merges fixed rules, context, and custom instructions."""
        prompt = prompts.build_system_prompt(
            context="Current app: Flask on port 5000",
            custom_rules="Always respond in French.",
        )
        self.assertIn("AI Assistant for the Barq VPS Control Panel", prompt)
        self.assertIn("Linux (Debian / Ubuntu)", prompt)
        self.assertIn("[ACTION:SET_PORT:", prompt)
        self.assertIn("Current app: Flask on port 5000", prompt)
        self.assertIn("Always respond in French.", prompt)

    def test_trim_context_log(self):
        """Verify oversized error logs are trimmed to the recent lines."""
        many_lines = "\n".join([f"Log line {i}" for i in range(500)])
        trimmed = engine.trim_context_log(many_lines, max_lines=50)
        lines = trimmed.splitlines()
        self.assertEqual(len(lines), 50)
        self.assertEqual(lines[-1], "Log line 499")

    async def test_provider_creation_and_update(self):
        """Verify provider persistence and updates in database."""
        async with AsyncSessionLocal() as db:
            provider = await service.create_provider(db, {
                "name": "Anthropic Claude Test",
                "provider_type": "anthropic",
                "base_url": "https://api.anthropic.com/v1",
                "model_name": "claude-3-5-sonnet-20241022",
                "api_key": "sk-ant-test-key",
                "temperature": 0.3,
                "custom_rules": "Always suggest PostgreSQL.",
            })
            self.assertIsNotNone(provider.id)
            self.assertEqual(provider.provider_type, "anthropic")
            self.assertEqual(provider.model_name, "claude-3-5-sonnet-20241022")
            self.assertEqual(provider.temperature, 0.3)
            self.assertEqual(provider.custom_rules, "Always suggest PostgreSQL.")
            self.assertEqual(service.decrypt_key(provider.api_key_encrypted), "sk-ant-test-key")

    async def test_stream_openai_compatible_mock(self):
        """Verify OpenAI SSE parsing with mocked httpx stream."""
        sse_lines = [
            'data: {"choices":[{"delta":{"content":"Hello"}}]}',
            'data: {"choices":[{"delta":{"content":" world"}}]}',
            'data: [DONE]',
        ]

        class MockResponse:
            status_code = 200
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
            async def aiter_lines(self):
                for line in sse_lines:
                    yield line

        class MockClient:
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
            def stream(self, method, url, headers=None, json=None):
                return MockResponse()

        with patch("httpx.AsyncClient", return_value=MockClient()):
            chunks = []
            async for chunk in engine.stream_chat(
                provider_type="openai_compatible",
                base_url="https://api.openai.com/v1",
                api_key="sk-fake",
                model_name="gpt-4o-mini",
                messages=[{"role": "user", "content": "hi"}],
            ):
                chunks.append(chunk)

            self.assertEqual("".join(chunks), "Hello world")

    async def test_stream_anthropic_mock(self):
        """Verify Anthropic SSE parsing with mocked httpx stream."""
        sse_lines = [
            'data: {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Bonjour"}}',
            'data: {"type": "content_block_delta", "delta": {"type": "text_delta", "text": " le monde"}}',
            'data: {"type": "message_stop"}',
        ]

        class MockResponse:
            status_code = 200
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
            async def aiter_lines(self):
                for line in sse_lines:
                    yield line

        class MockClient:
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
            def stream(self, method, url, headers=None, json=None):
                return MockResponse()

        with patch("httpx.AsyncClient", return_value=MockClient()):
            chunks = []
            async for chunk in engine.stream_chat(
                provider_type="anthropic",
                base_url="https://api.anthropic.com/v1",
                api_key="sk-ant-fake",
                model_name="claude-3-5-sonnet-20241022",
                messages=[{"role": "user", "content": "hi"}],
            ):
                chunks.append(chunk)

            self.assertEqual("".join(chunks), "Bonjour le monde")

    async def test_chat_pipeline_and_session_memory(self):
        """Verify multi-turn chat pipeline saves history and clears session."""
        sse_lines = [
            'data: {"choices":[{"delta":{"content":"Configure port 3000: [ACTION:SET_PORT:3000]"}}]}',
            'data: [DONE]',
        ]

        class MockResponse:
            status_code = 200
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
            async def aiter_lines(self):
                for line in sse_lines:
                    yield line

        class MockClient:
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
            def stream(self, method, url, headers=None, json=None):
                return MockResponse()

    async def test_provider_crud_lifecycle(self):
        """Verify full lifecycle of adding, setting default, and deleting providers."""
        async with AsyncSessionLocal() as db:
            # 1. Create provider
            p1 = await service.create_provider(db, {
                "name": "OpenAI GPT-4o",
                "provider_type": "openai_compatible",
                "base_url": "https://api.openai.com/v1",
                "model_name": "gpt-4o",
                "api_key": "sk-openai-test",
                "is_default": True,
            })
            self.assertIsNotNone(p1.id)
            self.assertEqual(p1.name, "OpenAI GPT-4o")
            self.assertTrue(p1.is_default)
            self.assertEqual(service.decrypt_key(p1.api_key_encrypted), "sk-openai-test")

            # 2. Create second provider
            p2 = await service.create_provider(db, {
                "name": "DeepSeek Chat",
                "provider_type": "openai_compatible",
                "base_url": "https://api.deepseek.com/v1",
                "model_name": "deepseek-chat",
                "api_key": "sk-deepseek-test",
                "is_default": False,
            })
            self.assertFalse(p2.is_default)

            # 3. List providers
            providers = await service.list_providers(db)
            self.assertTrue(len(providers) >= 2)

            # 4. Set p2 as default
            await service.set_default_provider(db, p2.id)
            active = await service.get_active_provider(db)
            self.assertEqual(active.id, p2.id)
            self.assertEqual(active.model_name, "deepseek-chat")

            # 5. Delete provider
            await service.delete_provider(db, p2.id)
            remaining_active = await service.get_active_provider(db)
            self.assertNotEqual(remaining_active.id, p2.id)

    async def test_chat_pipeline_and_session_memory(self):
        """Verify multi-turn chat pipeline saves history and clears session."""
        sse_lines = [
            'data: {"choices":[{"delta":{"content":"Configure port 3000: [ACTION:SET_PORT:3000]"}}]}',
            'data: [DONE]',
        ]

        class MockResponse:
            status_code = 200
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
            async def aiter_lines(self):
                for line in sse_lines:
                    yield line

        class MockClient:
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
            def stream(self, method, url, headers=None, json=None):
                return MockResponse()

        async with AsyncSessionLocal() as db:
            # Set up active default provider
            p = await service.create_provider(db, {
                "name": "Test Active Provider",
                "provider_type": "openai_compatible",
                "api_key": "sk-valid-test-key",
                "base_url": "https://api.openai.com/v1",
                "model_name": "gpt-4o-mini",
                "is_default": True,
                "is_enabled": True,
            })

            import uuid
            session_id = f"test_sess_{uuid.uuid4().hex[:8]}"
            with patch("httpx.AsyncClient", return_value=MockClient()):
                chunks = []
                async for chunk in service.stream_ai_chat(
                    db=db,
                    session_id=session_id,
                    user_message="What port should I use?",
                ):
                    chunks.append(chunk)

                self.assertIn("Configure port 3000: [ACTION:SET_PORT:3000]", "".join(chunks))

            # Verify saved messages in DB
            history = await service.get_session_messages(db, session_id)
            self.assertEqual(len(history), 2)
            self.assertEqual(history[0]["role"], "user")
            self.assertEqual(history[1]["role"], "assistant")
            self.assertIn("[ACTION:SET_PORT:3000]", history[1]["content"])

            # Test clearing session
            await service.clear_session(db, session_id)
            empty_history = await service.get_session_messages(db, session_id)
            self.assertEqual(len(empty_history), 0)


    def test_provider_presets_catalog(self):
        """Verify pre-configured presets catalog contains all popular providers."""
        presets = service.PROVIDER_PRESETS
        self.assertIn("openai", presets)
        self.assertIn("anthropic", presets)
        self.assertIn("deepseek", presets)
        self.assertIn("openrouter", presets)
        self.assertIn("groq", presets)
        self.assertIn("gemini", presets)
        self.assertIn("mistral", presets)
        self.assertIn("together", presets)
        self.assertIn("custom", presets)

        # Check endpoints and types
        self.assertEqual(presets["openai"]["type"], "openai_compatible")
        self.assertEqual(presets["openai"]["base_url"], "https://api.openai.com/v1")
        self.assertEqual(presets["anthropic"]["type"], "anthropic")

    async def test_fetch_available_models_openai_mock(self):
        """Verify fetching model IDs from standard OpenAI-compatible /models endpoint."""
        mock_json = {
            "data": [
                {"id": "gpt-4o"},
                {"id": "gpt-4o-mini"},
                {"id": "text-embedding-3-small"}
            ]
        }

        class MockResponse:
            status_code = 200
            def json(self):
                return mock_json

        class MockClient:
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
            async def get(self, url, headers=None):
                return MockResponse()

        with patch("httpx.AsyncClient", return_value=MockClient()):
            models = await engine.fetch_available_models(
                provider_type="openai_compatible",
                base_url="https://api.openai.com/v1",
                api_key="sk-test",
            )
            self.assertEqual(models, ["gpt-4o", "gpt-4o-mini", "text-embedding-3-small"])

    def test_is_plugin_active_helper(self):
        """Verify templating is_plugin_active helper."""
        from templating import is_plugin_active
        # ai_helper is registered and active by default
        self.assertTrue(is_plugin_active("ai_helper"))
        # non-existent plugin returns False
        self.assertFalse(is_plugin_active("non_existent_plugin_123"))

    async def test_multi_model_persistence_and_get_models(self):
        """Verify multiple models per provider are stored and retrieved."""
        async with AsyncSessionLocal() as db:
            provider = await service.create_provider(db, {
                "name": "OpenAI Multi-Model",
                "provider_type": "openai_compatible",
                "base_url": "https://api.openai.com/v1",
                "model_name": "gpt-4o-mini",
                "models_list": "gpt-4o, gpt-4o-mini, o3-mini",
                "api_key": "sk-multi-test",
            })
            self.assertEqual(provider.model_name, "gpt-4o-mini")
            self.assertIn("o3-mini", provider.models_list)
            models = provider.get_models()
            self.assertIn("gpt-4o", models)
            self.assertIn("gpt-4o-mini", models)
            self.assertIn("o3-mini", models)

            # Test updating models list
            updated = await service.update_provider(db, provider.id, {
                "models_list": "gpt-4o, claude-3-5-sonnet",
            })
            self.assertIn("claude-3-5-sonnet", updated.models_list)
            self.assertIn("claude-3-5-sonnet", updated.get_models())

    def test_modular_prompts_architecture(self):
        """Verify prompt sub-package separation and dynamic assembly."""
        from plugins.ai_helper.prompts import base_rules, tool_rules, action_tags, builder
        self.assertIn("AI Assistant for the Barq VPS Control Panel", base_rules.FIXED_CORE_SYSTEM_PROMPT)
        self.assertIn("Panel Inspection Tools & Permissions", tool_rules.TOOL_USAGE_RULES)
        self.assertIn("[ACTION:SET_PORT:", action_tags.ACTION_TAGS_SPEC)

        assembled = builder.build_system_prompt(
            context="Active file: index.php",
            custom_rules="Enforce strict typing.",
            include_tools_rules=True,
        )
        self.assertIn("Panel Inspection Tools & Permissions", assembled)
        self.assertIn("Active file: index.php", assembled)
        self.assertIn("Enforce strict typing.", assembled)

        # Without tools rules when tools are disabled
        assembled_no_tools = builder.build_system_prompt(
            context=None,
            custom_rules=None,
            include_tools_rules=False,
        )
        self.assertNotIn("Panel Inspection Tools & Permissions", assembled_no_tools)

    def test_tool_definitions_formats(self):
        """Verify OpenAI and Anthropic JSON tool schema formats."""
        from plugins.ai_helper.tools import definitions
        openai_tools = definitions.get_tool_definitions("openai_compatible")
        self.assertTrue(len(openai_tools) >= 7)
        self.assertEqual(openai_tools[0]["type"], "function")
        self.assertIn("name", openai_tools[0]["function"])
        self.assertIn("parameters", openai_tools[0]["function"])

        anthropic_tools = definitions.get_tool_definitions("anthropic")
        self.assertTrue(len(anthropic_tools) >= 7)
        self.assertIn("name", anthropic_tools[0])
        self.assertIn("input_schema", anthropic_tools[0])

    async def test_permission_policy_enforcement(self):
        """Verify permission policy checks and selective filters."""
        from plugins.ai_helper.permissions import policy, PermissionDeniedError
        async with AsyncSessionLocal() as db:
            # 1. Default full_read_only policy
            await policy.update_policy(db, {
                "global_mode": "full_read_only",
                "allow_domains_proxy": True,
                "allow_dns": True,
            })
            self.assertTrue(await policy.check_tool_permission(db, "get_domains_and_ssl", {}))
            self.assertTrue(await policy.check_tool_permission(db, "get_dns_records", {"domain": "test.com"}))

            # 2. Category flag disabled
            await policy.update_policy(db, {
                "allow_dns": False,
            })
            with self.assertRaises(PermissionDeniedError):
                await policy.check_tool_permission(db, "get_dns_records", {"domain": "test.com"})

            # 3. Global mode disabled
            await policy.update_policy(db, {
                "global_mode": "disabled",
            })
            with self.assertRaises(PermissionDeniedError):
                await policy.check_tool_permission(db, "get_domains_and_ssl", {})

            # 4. Granular Scope (Selective mode)
            await policy.update_policy(db, {
                "global_mode": "selective",
                "allow_domains_proxy": True,
                "allow_databases": True,
                "allow_files_read": True,
                "allowed_domains": ["allowed.com"],
                "allowed_app_ids": ["app-123"],
                "allowed_databases": ["production_db"],
                "allowed_file_targets": ["container:1", "php:2"],
            })
            # Allowed domain
            self.assertTrue(await policy.check_tool_permission(db, "get_domains_and_ssl", {"domain_name": "allowed.com"}))
            # Denied domain
            with self.assertRaises(PermissionDeniedError):
                await policy.check_tool_permission(db, "get_domains_and_ssl", {"domain_name": "forbidden.com"})

            # Allowed DB
            self.assertTrue(await policy.check_tool_permission(db, "get_databases_overview", {"database_name": "production_db"}))
            # Denied DB
            with self.assertRaises(PermissionDeniedError):
                await policy.check_tool_permission(db, "get_databases_overview", {"database_name": "secret_db"})

            # Allowed File target
            self.assertTrue(await policy.check_tool_permission(db, "list_website_directory", {"target_id": "container:1"}))
            # Denied File target
            with self.assertRaises(PermissionDeniedError):
                await policy.check_tool_permission(db, "list_website_directory", {"target_id": "container:99"})

    async def test_discoverable_resources(self):
        """Verify discovery of domains, apps, databases, and file targets for permissions UI."""
        async with AsyncSessionLocal() as db:
            resources = await service.get_discoverable_resources(db)
            self.assertIn("domains", resources)
            self.assertIn("apps", resources)
            self.assertIn("databases", resources)
            self.assertIn("file_targets", resources)
            self.assertIsInstance(resources["domains"], list)
            self.assertIsInstance(resources["apps"], list)
            self.assertIsInstance(resources["databases"], list)
            self.assertIsInstance(resources["file_targets"], list)

    async def test_tool_registry_execution(self):
        """Verify tool execution via registry and safe data sanitization."""
        from plugins.ai_helper.tools import registry
        from models.domain import Domain
        from models.proxy import ReverseProxy

        async with AsyncSessionLocal() as db:
            # Reset permission policy to full_read_only
            from plugins.ai_helper.permissions import policy
            await policy.update_policy(db, {
                "global_mode": "full_read_only",
                "allow_domains_proxy": True,
                "allow_databases": True,
            })

            # Create test domain
            test_domain = Domain(
                name="ai-test.local",
                server_ip="127.0.0.1",
                project_type="proxy",
                nginx_active=True,
            )
            db.add(test_domain)
            await db.commit()
            await db.refresh(test_domain)

            # Create test proxy
            test_proxy = ReverseProxy(
                domain_id=test_domain.id,
                full_domain="ai-test.local",
                subdomain="",
                target_ip="127.0.0.1",
                target_port=8080,
                protocol="http",
                ssl_enabled=False,
            )
            db.add(test_proxy)
            await db.commit()

            # Execute get_domains_and_ssl
            res_domains = await registry.execute_tool(db, "get_domains_and_ssl", {"domain_name": "ai-test.local"})
            self.assertEqual(res_domains["status"], "ok")
            self.assertTrue(any(d["domain"] == "ai-test.local" for d in res_domains["domains"]))

            # Execute get_reverse_proxy_routes
            res_proxy = await registry.execute_tool(db, "get_reverse_proxy_routes", {"domain": "ai-test.local"})
            self.assertEqual(res_proxy["status"], "ok")
            self.assertEqual(res_proxy["reverse_proxies"][0]["target_port"], 8080)

            # Execute get_databases_overview
            res_db = await registry.execute_tool(db, "get_databases_overview", {})
            self.assertEqual(res_db["status"], "ok")
            self.assertIn("container_databases", res_db)
            self.assertIn("php_databases", res_db)

            # Clean up
            await db.delete(test_proxy)
            await db.delete(test_domain)
            await db.commit()

    async def test_session_crud_and_task_types(self):
        """Verify session creation, updating, retrieval, and deletion with task scopes."""
        async with AsyncSessionLocal() as db:
            import uuid
            session_id = f"test_sess_{uuid.uuid4().hex[:8]}"
            session = await service.get_or_create_session(
                db=db,
                session_id=session_id,
                title="Node.js App Deployment",
                task_type="app",
                context_key="app:42",
            )
            self.assertIsNotNone(session.id)
            self.assertEqual(session.session_id, session_id)
            self.assertEqual(session.title, "Node.js App Deployment")
            self.assertEqual(session.task_type, "app")
            self.assertEqual(session.context_key, "app:42")

            # Retrieve session
            fetched = await service.get_session(db, session_id)
            self.assertIsNotNone(fetched)
            self.assertEqual(fetched["title"], "Node.js App Deployment")
            self.assertEqual(fetched["task_type"], "app")

            # Update session
            updated = await service.update_session(db, session_id, {"title": "Node.js Production Setup", "task_type": "container"})
            self.assertEqual(updated["title"], "Node.js Production Setup")
            self.assertEqual(updated["task_type"], "container")

            # Delete session
            deleted = await service.delete_session(db, session_id)
            self.assertTrue(deleted)
            self.assertIsNone(await service.get_session(db, session_id))

    def test_auto_title_generation(self):
        """Verify auto-generation of clean session titles from prompts."""
        title1 = service._generate_title_from_prompt("How do I configure Nginx reverse proxy on port 8080?")
        self.assertEqual(title1, "Configure Nginx reverse proxy on port 8080?")

        title2 = service._generate_title_from_prompt("Explain common reasons for 502 Bad Gateway errors.")
        self.assertEqual(title2, "Common reasons for 502 Bad Gateway errors.")

        title3 = service._generate_title_from_prompt("```bash\ndocker run -p 3000:3000\n```")
        self.assertNotIn("```", title3)

        title4 = service._generate_title_from_prompt("")
        self.assertEqual(title4, "New Chat")

    async def test_task_separation_in_chat(self):
        """Verify that conversation history in Task A is isolated and does not leak into Task B."""
        sse_lines = [
            'data: {"choices":[{"delta":{"content":"Generic AI response"}}]}',
            'data: [DONE]',
        ]

        class MockResponse:
            status_code = 200
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
            async def aiter_lines(self):
                for line in sse_lines:
                    yield line

        class MockClient:
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
            def stream(self, method, url, headers=None, json=None):
                return MockResponse()

        async with AsyncSessionLocal() as db:
            import uuid
            provider = await service.create_provider(db, {
                "name": "Test Isolation Provider",
                "provider_type": "openai_compatible",
                "api_key": "sk-iso-test-key",
                "base_url": "https://api.openai.com/v1",
                "model_name": "gpt-4o-mini",
                "is_default": True,
                "is_enabled": True,
            })

            session_a = f"task_a_{uuid.uuid4().hex[:8]}"
            session_b = f"task_b_{uuid.uuid4().hex[:8]}"

            with patch("httpx.AsyncClient", return_value=MockClient()):
                # Run chat in Task A
                chunks_a = []
                async for chunk in service.stream_ai_chat(
                    db=db,
                    session_id=session_a,
                    user_message="Hello from Task A (Error Diagnostic)",
                    task_type="error_diag",
                    provider_id=provider.id,
                ):
                    chunks_a.append(chunk)

                # Run chat in Task B
                chunks_b = []
                async for chunk in service.stream_ai_chat(
                    db=db,
                    session_id=session_b,
                    user_message="Hello from Task B (Domain Config)",
                    task_type="domain",
                    provider_id=provider.id,
                ):
                    chunks_b.append(chunk)

            # Check messages in Task A
            msgs_a = await service.get_session_messages(db, session_a)
            self.assertEqual(len(msgs_a), 2)
            self.assertEqual(msgs_a[0]["content"], "Hello from Task A (Error Diagnostic)")

            # Check messages in Task B
            msgs_b = await service.get_session_messages(db, session_b)
            self.assertEqual(len(msgs_b), 2)
            self.assertEqual(msgs_b[0]["content"], "Hello from Task B (Domain Config)")

            # Verify no cross-contamination
            self.assertFalse(any("Task B" in m["content"] for m in msgs_a))
            self.assertFalse(any("Task A" in m["content"] for m in msgs_b))

            # Verify sessions listed with correct task types
            sessions = await service.list_sessions(db)
            sess_a_meta = next((s for s in sessions if s["session_id"] == session_a), None)
            sess_b_meta = next((s for s in sessions if s["session_id"] == session_b), None)
            self.assertIsNotNone(sess_a_meta)
            self.assertIsNotNone(sess_b_meta)
            self.assertEqual(sess_a_meta["task_type"], "error_diag")
            self.assertEqual(sess_b_meta["task_type"], "domain")

            # Clean up
            await service.delete_session(db, session_a)
            await service.delete_session(db, session_b)


if __name__ == "__main__":
    unittest.main()

