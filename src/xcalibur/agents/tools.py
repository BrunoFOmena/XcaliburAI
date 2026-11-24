"""Typed tools: what an agent may do, with validated arguments.

A tool is a name, a description, a Pydantic model for its arguments and a function. The
model only ever chooses a tool and proposes arguments; the runtime validates them against
the model before the function runs, so no model text is ever executed as a command.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict

from xcalibur.llm.base import ToolSpec

if TYPE_CHECKING:
    from xcalibur.agents.session import Session
    from xcalibur.execution.base import WorkflowEngine
    from xcalibur.registry import DomainRegistry
    from xcalibur.workspace import Workspace

_TOOL_NAME = re.compile(r"[A-Za-z0-9_-]{1,64}")


class ToolInput(BaseModel):
    """Base for tool argument models: unknown arguments are errors, not ignored."""

    model_config = ConfigDict(extra="forbid")


class ToolError(Exception):
    """An expected failure a tool reports to the model (it can correct and retry).

    ``kind`` is a short machine-readable label; ``details`` is extra structured data.
    """

    def __init__(self, message: str, kind: str = "tool_error", **details: Any) -> None:
        super().__init__(message)
        self.kind = kind
        self.details = details


@dataclass
class ToolContext:
    """What tools may use. Built by the caller; the runtime adds the session."""

    domains: DomainRegistry
    workspace: Workspace | None = None
    engine: WorkflowEngine | None = None
    session: Session | None = None
    approved_plan: dict[str, Any] | None = None
    """Set by the runtime for a confirmed tool: the preview the user approved."""
    extra: dict[str, Any] = field(default_factory=dict)
    """Room for domain-specific services, keyed by name."""


ToolFunc = Callable[[Any, ToolContext], Any]


@dataclass(frozen=True)
class Tool:
    """A capability exposed to the model.

    ``func`` receives the validated ``input_model`` instance and the context, and returns
    anything JSON-serializable (Pydantic models and paths included). When
    ``requires_confirmation`` is set, the runtime asks the confirmation policy first and
    shows it ``preview(args, context)``: what exactly will run. ``grounded`` marks tools
    whose numbers are the only ones the answer may state (see ``AgentLoop``).
    """

    name: str
    description: str
    input_model: type[BaseModel]
    func: ToolFunc
    requires_confirmation: bool = False
    preview: ToolFunc | None = None
    grounded: bool = False
    """The result holds facts about data: after a turn that used it, every number in the
    answer must match a number in such results (see xcalibur.agents.grounding)."""

    def __post_init__(self) -> None:
        check_tool_name(self.name)
        if self.input_model.model_config.get("extra") != "forbid":
            raise ValueError(
                f"tool '{self.name}': input model must forbid extra fields (subclass ToolInput)"
            )
        if self.requires_confirmation and self.preview is None:
            raise ValueError(f"tool '{self.name}': a confirmed tool needs a preview")

    def spec(self, name: str | None = None) -> ToolSpec:
        return ToolSpec(
            name=name or self.name,
            description=self.description,
            input_schema=self.input_model.model_json_schema(),
        )


def check_tool_name(name: str) -> str:
    if not isinstance(name, str) or _TOOL_NAME.fullmatch(name) is None:
        raise ValueError(f"invalid tool name {name!r}: use 1-64 letters, digits, '_' or '-'")
    return name


class ToolRegistry:
    """Tools available to agents, by the name the model sees."""

    def __init__(self, tools: Iterable[Tool] = ()) -> None:
        self._tools: dict[str, Tool] = {}
        for tool in tools:
            self.register(tool)

    def register(self, tool: Tool, name: str | None = None) -> None:
        name = check_tool_name(name or tool.name)
        if name in self._tools:
            raise ValueError(f"tool '{name}' is already registered")
        self._tools[name] = tool

    def get(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError:
            raise KeyError(f"unknown tool '{name}'") from None

    def __contains__(self, name: object) -> bool:
        return name in self._tools

    def names(self) -> list[str]:
        return list(self._tools)

    def specs(self, names: Iterable[str] | None = None) -> list[ToolSpec]:
        selected = self.names() if names is None else [n for n in names if n in self._tools]
        return [self._tools[n].spec(n) for n in selected]

    def __iter__(self) -> Iterator[tuple[str, Tool]]:
        return iter(self._tools.items())

    def __len__(self) -> int:
        return len(self._tools)
