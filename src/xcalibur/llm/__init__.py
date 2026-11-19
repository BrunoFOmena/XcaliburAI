"""LLM provider seam. Users bring their own provider; the core never hard-codes one."""

from __future__ import annotations

from xcalibur.llm.base import LLMProvider, ProviderConfig
from xcalibur.llm.errors import LLMConfigError

SUPPORTED_PROVIDERS = ("anthropic",)

# Suggested defaults for `xcalibur init` only; the provider always uses the configured model.
SUGGESTED_MODELS = {"anthropic": "claude-opus-5-5"}
DEFAULT_KEY_ENVS = {"anthropic": "ANTHROPIC_API_KEY"}


def create_provider(config: ProviderConfig) -> LLMProvider:
    if config.provider == "anthropic":
        from xcalibur.llm.anthropic_provider import AnthropicProvider

        return AnthropicProvider(config)
    raise LLMConfigError(
        f"Unsupported provider {config.provider!r}; available: {', '.join(SUPPORTED_PROVIDERS)}."
    )
