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


if __name__ == "__main__":
    unittest.main()
