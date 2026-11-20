"""Scripted provider for tests and offline development; it never touches the network."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field

from xcalibur.llm.base import Completion, Message, ProviderConfig, ToolCall, ToolSpec
from xcalibur.llm.errors import LLMError

Reply = Completion | str | Exception | Callable[[list[Message], list[ToolSpec]], Completion]


@dataclass(frozen=True)
class FakeCall:
    """One request the provider received (for assertions in tests)."""

    messages: list[Message]
    tools: list[ToolSpec]


def tool_call(name: str, call_id: str | None = None, **arguments: object) -> Completion:
    """A completion that asks for one tool call (handy for scripts)."""
    return Completion(
        text="",
        tool_calls=[ToolCall(id=call_id or f"call_{name}", name=name, input=dict(arguments))],
        stop_reason="tool_use",
        model="fake",
    )


@dataclass
class FakeProvider:
    """Answers with the scripted replies, in order.

    A reply is a :class:`Completion`, a plain string (a final text answer), an exception
    (raised, to simulate provider failures) or a callable that builds the completion from
    the request. When the script runs out, :class:`LLMError` is raised.
    """

    replies: list[Reply] = field(default_factory=list)
    config: ProviderConfig = field(default_factory=lambda: ProviderConfig("fake", "fake"))
    calls: list[FakeCall] = field(default_factory=list)

    @classmethod
    def scripted(cls, *replies: Reply) -> FakeProvider:
        return cls(replies=list(replies))

    def extend(self, replies: Iterable[Reply]) -> None:
        self.replies.extend(replies)

    def complete(self, messages: list[Message]) -> str:
        return self.generate(messages).text

    def generate(self, messages: list[Message], tools: list[ToolSpec] | None = None) -> Completion:
        tools = list(tools or [])
        self.calls.append(FakeCall(messages=list(messages), tools=tools))
        if not self.replies:
            raise LLMError("FakeProvider has no scripted reply left.")
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, str):
            return Completion(text=reply, stop_reason="end_turn", model="fake")
        if isinstance(reply, Completion):
            return reply
        return reply(list(messages), tools)

    def stream_text(self, messages: list[Message]) -> Iterator[str]:
        yield self.complete(messages)
