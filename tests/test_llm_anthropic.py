"""AnthropicProvider against the real SDK with a simulated HTTP transport (no network)."""

import json

import anthropic
import httpx2
import pytest

from xcalibur.llm import create_provider
from xcalibur.llm.anthropic_provider import AnthropicProvider
from xcalibur.llm.base import Message, ProviderConfig, ToolSpec
from xcalibur.llm.errors import (
    LLMAuthError,
    LLMConfigError,
    LLMConnectionError,
    LLMRateLimitError,
    LLMRefusalError,
)

SECRET = "sk-ant-test-SECRET-123"


def message(content, stop_reason="end_turn", model="claude-opus-5-5", **extra):
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 3, "output_tokens": 2},
        **extra,
    }


def error(status, kind, text, headers=None):
    body = {"type": "error", "error": {"type": kind, "message": text}}
    return httpx2.Response(status, json=body, headers=headers or {})


class Server:
    """Records requests and replies with queued responses."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        reply = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, dict):
            return httpx2.Response(200, json=reply)
        return reply

    def body(self, i=-1):
        return json.loads(self.requests[i].content)


def provider(server, retries=0, **cfg):
    cfg.setdefault("model", "claude-opus-5-5")
    config = ProviderConfig(provider="anthropic", max_retries=retries, **cfg)
    client = anthropic.Anthropic(
        api_key=SECRET,
        base_url="http://anthropic.test",
        max_retries=retries,
        http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(server)),
    )
    return AnthropicProvider(config, client=client)


CHAT = [
    Message(role="system", content="You are XcaliburAI."),
    Message(role="system", content="Be brief."),
    Message(role="user", content="hello"),
]


def test_complete_sends_system_prompt_and_returns_text():
    server = Server(message([{"type": "text", "text": "Hi"}, {"type": "text", "text": "!"}]))
    assert provider(server).complete(CHAT) == "Hi!"
    body = server.body()
    assert body["model"] == "claude-opus-5-5"
    assert body["system"] == "You are XcaliburAI.\n\nBe brief."
    assert body["messages"] == [{"role": "user", "content": "hello"}]
    assert "temperature" not in body


def test_refusal_fallback_is_on_by_default_for_supported_models():
    server = Server(message([{"type": "text", "text": "ok"}]))
    provider(server).complete(CHAT)
    assert server.body()["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in server.requests[-1].headers["anthropic-beta"]


@pytest.mark.parametrize("cfg", [{"refusal_fallback": False}, {"model": "claude-haiku-5-5"}])
def test_no_fallback_when_disabled_or_unsupported(cfg):
    server = Server(message([{"type": "text", "text": "ok"}], model=cfg.get("model", "x")))
    provider(server, **cfg).complete(CHAT)
    assert "fallbacks" not in server.body()
    assert "anthropic-beta" not in server.requests[-1].headers


def test_effort_goes_to_output_config():
    server = Server(message([{"type": "text", "text": "ok"}]))
    provider(server, effort="low").complete(CHAT)
    assert server.body()["output_config"] == {"effort": "low"}


def test_generate_returns_tool_calls():
    server = Server(
        message(
            [
                {"type": "text", "text": "Running."},
                {"type": "tool_use", "id": "toolu_1", "name": "run", "input": {"mode": "AUTO"}},
            ],
            stop_reason="tool_use",
        )
    )
    tool = ToolSpec(name="run", description="Run the pipeline", input_schema={"type": "object"})
    result = provider(server).generate(CHAT, tools=[tool])
    assert result.text == "Running." and result.stop_reason == "tool_use"
    assert result.tool_calls[0].name == "run" and result.tool_calls[0].input == {"mode": "AUTO"}
    assert server.body()["tools"][0]["name"] == "run"


def sse(stop_reason="end_turn", parts=("Hel", "lo"), **message_delta):
    events = [
        ("message_start", {"type": "message_start", "message": message([])}),
        (
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
        ),
        *[
            (
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": part},
                },
            )
            for part in parts
        ],
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        (
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": stop_reason, "stop_sequence": None, **message_delta},
                "usage": {"output_tokens": 2},
            },
        ),
        ("message_stop", {"type": "message_stop"}),
    ]
    text = "".join(f"event: {name}\ndata: {json.dumps(data)}\n\n" for name, data in events)
    return httpx2.Response(200, text=text, headers={"content-type": "text/event-stream"})


@pytest.mark.parametrize("fallback", [False, True])
def test_stream_text_yields_deltas(fallback):
    server = Server(sse())
    chunks = list(provider(server, refusal_fallback=fallback).stream_text(CHAT))
    assert chunks == ["Hel", "lo"]
    body = server.body()
    assert body["stream"] is True
    assert ("fallbacks" in body) is fallback


def test_refusal_during_stream_raises_after_partial_text():
    server = Server(sse(stop_reason="refusal", parts=("partial",)))
    stream = provider(server).stream_text(CHAT)
    assert next(stream) == "partial"
    with pytest.raises(LLMRefusalError):
        list(stream)


def test_auth_error_while_streaming():
    server = Server(error(401, "authentication_error", "invalid x-api-key"))
    with pytest.raises(LLMAuthError) as info:
        list(provider(server).stream_text(CHAT))
    assert SECRET not in str(info.value)


def test_refusal_raises_with_category():
    details = {"type": "refusal", "category": "cyber", "explanation": "no"}
    server = Server(message([], stop_reason="refusal", stop_details=details))
    with pytest.raises(LLMRefusalError, match="cyber"):
        provider(server).complete(CHAT)


def test_auth_error_is_clear_and_never_contains_the_key():
    server = Server(error(401, "authentication_error", "invalid x-api-key"))
    with pytest.raises(LLMAuthError) as info:
        provider(server, api_key_env="MY_KEY").complete(CHAT)
    assert "MY_KEY" in str(info.value)
    assert SECRET not in str(info.value)


def test_unknown_model_is_a_config_error():
    server = Server(error(404, "not_found_error", "model: nope"))
    with pytest.raises(LLMConfigError, match="not found"):
        provider(server, model="nope").complete(CHAT)


def test_rate_limit_is_retried_a_bounded_number_of_times():
    limited = error(429, "rate_limit_error", "slow down", {"retry-after-ms": "0"})
    server = Server(limited, message([{"type": "text", "text": "ok"}]))
    assert provider(server, retries=1).complete(CHAT) == "ok"
    assert len(server.requests) == 2

    always = Server(limited)
    with pytest.raises(LLMRateLimitError, match="1 retries"):
        provider(always, retries=1).complete(CHAT)
    assert len(always.requests) == 2


def test_network_failure_becomes_connection_error():
    server = Server(httpx2.ConnectError("boom"))
    with pytest.raises(LLMConnectionError):
        provider(server).complete(CHAT)


def test_missing_key_names_the_variable(monkeypatch):
    monkeypatch.delenv("XCAL_TEST_KEY", raising=False)
    config = ProviderConfig(provider="anthropic", model="m", api_key_env="XCAL_TEST_KEY")
    with pytest.raises(LLMConfigError, match="XCAL_TEST_KEY is not set"):
        create_provider(config).complete(CHAT)


def test_key_is_read_from_the_configured_variable(monkeypatch):
    monkeypatch.setenv("XCAL_TEST_KEY", SECRET)
    config = ProviderConfig(provider="anthropic", model="m", api_key_env="XCAL_TEST_KEY")
    assert create_provider(config).client.api_key == SECRET


def test_unsupported_provider():
    with pytest.raises(LLMConfigError, match="Unsupported provider"):
        create_provider(ProviderConfig(provider="openai", model="m"))
