"""Provider-agnostic LLM interface."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol


@dataclass(frozen=True)
class Message:
    role: Literal["system", "user", "assistant"]
    content: str


@dataclass(frozen=True)
class ProviderConfig:
    provider: str
    """e.g. anthropic, openai, gemini, ollama."""
    model: str
    api_key_env: str | None = None
    """Name of the environment variable holding the key; keys are never stored in config."""
    base_url: str | None = None


class LLMProvider(Protocol):
    config: ProviderConfig

    def complete(self, messages: list[Message]) -> str: ...
