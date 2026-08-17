/**
 * modules/presets.js — AI Provider preset configurations
 * Minimal endpoint definitions without hardcoded default models.
 */
export const PRESET_CONFIGS = {
  openai: {
    name: "OpenAI",
    provider_type: "openai_compatible",
    base_url: "https://api.openai.com/v1",
  },
  anthropic: {
    name: "Anthropic Claude",
    provider_type: "anthropic",
    base_url: "https://api.anthropic.com/v1",
  },
  openrouter: {
    name: "OpenRouter",
    provider_type: "openai_compatible",
    base_url: "https://openrouter.ai/api/v1",
  },
  deepseek: {
    name: "DeepSeek",
    provider_type: "openai_compatible",
    base_url: "https://api.deepseek.com/v1",
  },
  groq: {
    name: "Groq Cloud",
    provider_type: "openai_compatible",
    base_url: "https://api.groq.com/openai/v1",
  },
  gemini: {
    name: "Google Gemini",
    provider_type: "openai_compatible",
    base_url: "https://generativelanguage.googleapis.com/v1beta/openai",
  },
  ollama: {
    name: "Local Ollama",
    provider_type: "openai_compatible",
    base_url: "http://localhost:11434/v1",
  },
  custom: {
    name: "Custom Endpoint",
    provider_type: "openai_compatible",
    base_url: "",
  },
};
