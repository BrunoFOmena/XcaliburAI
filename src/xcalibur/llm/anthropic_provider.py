"""Anthropic provider (Claude) behind the generic LLMProvider interface.

The API key is read from an environment variable at call time and is never stored,
logged or put in an error message.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any

from xcalibur.llm.base import Completion, Message, ProviderConfig, ToolCall, ToolSpec
from xcalibur.llm.errors import (
    LLMAuthError,
    LLMConfigError,
    LLMConnectionError,
    LLMError,
    LLMRateLimitError,
    LLMRefusalError,
)

DEFAULT_API_KEY_ENV = "ANTHROPIC_API_KEY"
INSTALL_HINT = "pip install 'xcalibur-ai[anthropic]'"

# Server-side refusal fallback (beta): the API re-runs a declined request on the
# recommended fallback model. Only these models accept fallbacks="default"; update this set
# when Anthropic adds models that support it (see the "refusals and fallback" API docs).
_FALLBACK_BETA = "server-side-fallback-2026-07-01"
_FALLBACK_MODELS = frozenset(
    {
        "claude-fable-5-2",
        "claude-fable-5-1",
        "claude-opus-5-5",
        "claude-opus-5",
        "claude-sonnet-5-5",
    }
)


def _sdk():
    try:
        import anthropic
    except ImportError:  # pragma: no cover - exercised only without the extra
        raise LLMConfigError(f"The Anthropic SDK is not installed: {INSTALL_HINT}") from None
    return anthropic


class AnthropicProvider:
    def __init__(self, config: ProviderConfig, client: Any | None = None) -> None:
        if config.provider != "anthropic":
            raise LLMConfigError(f"AnthropicProvider cannot serve provider {config.provider!r}.")
        if not config.model:
            raise LLMConfigError("No model configured; run 'xcalibur init'.")
        self.config = config
        self._client = client

    @property
    def api_key_env(self) -> str:
        return self.config.api_key_env or DEFAULT_API_KEY_ENV

    @property
    def client(self) -> Any:
        if self._client is None:
            anthropic = _sdk()
            key = os.environ.get(self.api_key_env)
            if not key:
                raise LLMConfigError(
                    f"Environment variable {self.api_key_env} is not set. Export your Anthropic "
                    "API key in it; the key is never written to the configuration."
                )
            options: dict[str, Any] = {
                "api_key": key,
                "max_retries": self.config.max_retries,
                "timeout": self.config.timeout,
            }
            if self.config.base_url:
                options["base_url"] = self.config.base_url
            self._client = anthropic.Anthropic(**options)
        return self._client

    # -- public API -------------------------------------------------------------------

    def complete(self, messages: list[Message]) -> str:
        return self.generate(messages).text

    def generate(self, messages: list[Message], tools: list[ToolSpec] | None = None) -> Completion:
        api, request = self._request(messages, tools)
        with self._errors():
            response = api.create(**request)
        return self._completion(response)

    def stream_text(self, messages: list[Message]) -> Iterator[str]:
        api, request = self._request(messages, None)
        with self._errors():
            with api.stream(**request) as stream:
                yield from stream.text_stream
                final = stream.get_final_message()
        self._completion(final)  # raises on refusal after a partial stream

    # -- internals --------------------------------------------------------------------

    @property
    def _uses_fallback(self) -> bool:
        return self.config.refusal_fallback and self.config.model in _FALLBACK_MODELS

    def _request(
        self, messages: list[Message], tools: list[ToolSpec] | None
    ) -> tuple[Any, dict[str, Any]]:
        system = "\n\n".join(m.content for m in messages if m.role == "system")
        turns = [_turn(m) for m in messages if m.role != "system"]
        if not turns:
            raise LLMError("At least one user message is required.")
        request: dict[str, Any] = {
            "model": self.config.model,
            "max_tokens": self.config.max_tokens,
            "messages": turns,
        }
        if system:
            request["system"] = system
        if self.config.effort:
            request["output_config"] = {"effort": self.config.effort}
        if tools:
            request["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": t.input_schema}
                for t in tools
            ]
        client = self.client
        if self._uses_fallback:
            request["betas"] = [_FALLBACK_BETA]
            request["fallbacks"] = "default"
            return client.beta.messages, request
        return client.messages, request

    def _completion(self, response: Any) -> Completion:
        if response.stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            category = getattr(details, "category", None)
            suffix = f" (category: {category})" if category else ""
            raise LLMRefusalError(f"The model declined the request{suffix}.")
        texts: list[str] = []
        calls: list[ToolCall] = []
        for block in response.content:
            if block.type == "text":
                texts.append(block.text)
            elif block.type == "tool_use":
                calls.append(ToolCall(id=block.id, name=block.name, input=dict(block.input)))
        return Completion(
            text="".join(texts),
            tool_calls=calls,
            stop_reason=response.stop_reason,
            model=getattr(response, "model", None),
        )

    def _errors(self) -> _ErrorMapper:
        return _ErrorMapper(self)


def _turn(message: Message) -> dict[str, Any]:
    """One conversation turn; tool calls and results become content blocks."""
    if not message.tool_calls and not message.tool_results:
        return {"role": message.role, "content": message.content}
    blocks: list[dict[str, Any]] = []
    # Tool results must come first in a user turn; text, if any, follows them.
    for result in message.tool_results:
        block: dict[str, Any] = {
            "type": "tool_result",
            "tool_use_id": result.tool_call_id,
            "content": result.content,
        }
        if result.is_error:
            block["is_error"] = True
        blocks.append(block)
    if message.content:
        text = {"type": "text", "text": message.content}
        if message.tool_calls:
            blocks.insert(0, text)  # assistant: its text precedes the tool calls
        else:
            blocks.append(text)
    for call in message.tool_calls:
        blocks.append({"type": "tool_use", "id": call.id, "name": call.name, "input": call.input})
    return {"role": message.role, "content": blocks}


class _ErrorMapper:
    """Translate SDK exceptions into LLM errors with clear, secret-free messages."""

    def __init__(self, provider: AnthropicProvider) -> None:
        self.provider = provider

    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc is None or isinstance(exc, LLMError):
            return False
        anthropic = _sdk()
        cfg = self.provider.config
        env = self.provider.api_key_env
        if isinstance(exc, anthropic.AuthenticationError | anthropic.PermissionDeniedError):
            raise LLMAuthError(
                f"Anthropic rejected the API key in {env} (HTTP {exc.status_code}). "
                "Check that the key is valid and allowed to use this model."
            ) from exc
        if isinstance(exc, anthropic.NotFoundError):
            raise LLMConfigError(
                f"Model {cfg.model!r} was not found or is not available to this key."
            ) from exc
        if isinstance(exc, anthropic.RateLimitError):
            raise LLMRateLimitError(
                f"Anthropic usage limit reached after {cfg.max_retries} retries; "
                "try again later or raise your limits."
            ) from exc
        if isinstance(exc, anthropic.APITimeoutError):
            raise LLMConnectionError(
                f"Anthropic did not answer within {cfg.timeout:g}s "
                f"(after {cfg.max_retries} retries)."
            ) from exc
        if isinstance(exc, anthropic.APIConnectionError):
            raise LLMConnectionError(
                f"Could not reach Anthropic after {cfg.max_retries} retries; "
                "check the network or the base URL."
            ) from exc
        if isinstance(exc, anthropic.APIStatusError):
            raise LLMError(
                f"Anthropic returned HTTP {exc.status_code}: {exc.message}"
            ) from exc
        return False
