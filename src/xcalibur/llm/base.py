"""Provider-agnostic LLM interface."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol


@dataclass(frozen=True)
class ToolSpec:
    """A tool the model may call (the agent runtime builds these from its tools)."""

    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    input: dict[str, Any]


@dataclass(frozen=True)
class ToolResult:
    """What a tool call returned, sent back to the model in the next user turn."""

    tool_call_id: str
    content: str
    """Serialized result (JSON text); errors are structured too, with ``is_error`` set."""
    is_error: bool = False


@dataclass(frozen=True)
class Message:
    role: Literal["system", "user", "assistant"]
    content: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    """Assistant turns only: the tool calls the model asked for in this turn."""
    tool_results: tuple[ToolResult, ...] = ()
    """User turns only: results of the previous turn's tool calls."""

    def __post_init__(self) -> None:
        if self.tool_calls and self.role != "assistant":
            raise ValueError("only assistant messages can carry tool calls")
        if self.tool_results and self.role != "user":
            raise ValueError("only user messages can carry tool results")


@dataclass(frozen=True)
class Completion:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str | None = None
    model: str | None = None
    """The model that produced the answer (may differ when a server-side fallback ran)."""


@dataclass(frozen=True)
class ProviderConfig:
    provider: str
    """e.g. anthropic, openai, gemini, ollama."""
    model: str
    api_key_env: str | None = None
    """Name of the environment variable holding the key; keys are never stored in config."""
    base_url: str | None = None
    max_tokens: int = 16000
    effort: str | None = None
    """Provider-specific reasoning effort (e.g. low, medium, high); None keeps the default."""
    max_retries: int = 2
    """Bounded retries for rate limits, server errors and network failures."""
    timeout: float = 600.0
    """Seconds per request."""
    refusal_fallback: bool = True
    """Let the provider re-run a declined request on its recommended fallback model."""


class LLMProvider(Protocol):
    config: ProviderConfig

    def complete(self, messages: list[Message]) -> str:
        """Text answer; system messages become the provider's system prompt."""
        ...

    def generate(self, messages: list[Message], tools: list[ToolSpec] | None = None) -> Completion:
        """Full answer, including any tool calls the model requested."""
        ...

    def stream_text(self, messages: list[Message]) -> Iterator[str]:
        """Yield the answer's text as it is generated."""
        ...
