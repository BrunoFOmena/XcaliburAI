"""Confirmation policy: who approves a tool that acts (e.g. running a pipeline).

The runtime, not the tool, enforces it: a tool marked ``requires_confirmation`` only runs
after the policy approves the exact plan its preview produced. The default denies.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class ConfirmationRequest:
    tool: str
    arguments: dict[str, Any]
    """Validated arguments."""
    summary: str
    """One line for the user, e.g. the command that will run."""
    details: dict[str, Any] = field(default_factory=dict)
    """The tool's preview: parameters, inputs, command, run directory."""


class ConfirmationPolicy(Protocol):
    def confirm(self, request: ConfirmationRequest) -> bool: ...


class DenyAll:
    """Default: nothing that needs confirmation runs unless a user interface approves it."""

    def confirm(self, request: ConfirmationRequest) -> bool:
        return False


class AllowAll:
    """Approve everything (explicit opt-in, e.g. a ``--yes`` flag or tests)."""

    def confirm(self, request: ConfirmationRequest) -> bool:
        return True


class CallbackConfirmation:
    """Ask a function, typically a prompt in the chat interface."""

    def __init__(self, ask: Callable[[ConfirmationRequest], bool]) -> None:
        self.ask = ask

    def confirm(self, request: ConfirmationRequest) -> bool:
        return bool(self.ask(request))
