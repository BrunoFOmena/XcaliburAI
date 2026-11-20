"""Contracts shared by every agent."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class AgentSpec:
    """Static description of an agent a domain package contributes."""

    name: str
    role: str
    """One of: qc, execution, interpretation, acquisition, reporting, custom."""
    description: str = ""
    instructions: str = ""
    """Extra system-prompt text for this agent (appended to the runtime's default)."""
    tools: tuple[str, ...] = ()
    """Names of the tools this agent may use; empty means every registered tool."""


@dataclass
class AgentRequest:
    message: str
    domain: str | None = None
    context: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentResponse:
    text: str
    data: dict[str, Any] = field(default_factory=dict)


class Agent(Protocol):
    """Anything that can answer a request. Real agents wrap an LLM and tools."""

    spec: AgentSpec

    def handle(self, request: AgentRequest) -> AgentResponse: ...
