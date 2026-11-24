"""Session: the conversation history plus an audit trail of every tool call."""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from xcalibur.llm.base import Message
from xcalibur.workspace import validate_run_id

ToolCallStatus = Literal[
    "ok", "invalid_arguments", "unknown_tool", "declined", "error", "internal_error", "interrupted"
]


@dataclass
class ToolCallRecord:
    """One tool call, as the model asked for it and as the runtime handled it."""

    step: int
    call_id: str
    tool: str
    arguments: dict[str, Any]
    """Arguments as the model sent them (before validation)."""
    status: ToolCallStatus
    started_at: datetime
    finished_at: datetime
    confirmation: Literal["approved", "declined"] | None = None
    truncated: bool = False
    result_path: str | None = None
    """Where the full result was saved when it was too long to send back."""
    summary: str = ""
    """Start of the result sent to the model."""

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        data["started_at"] = self.started_at.isoformat()
        data["finished_at"] = self.finished_at.isoformat()
        return data


@dataclass
class Session:
    """In-memory conversation state; long-term persistence is out of scope for now.

    With a ``directory``, every tool call is also appended to ``audit.jsonl`` there, and
    tool results too long for the model are saved under ``tool-results/``.
    """

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    directory: Path | None = None
    messages: list[Message] = field(default_factory=list)
    tool_calls: list[ToolCallRecord] = field(default_factory=list)

    def __post_init__(self) -> None:
        validate_run_id(self.id)

    @property
    def audit_path(self) -> Path | None:
        return None if self.directory is None else self.directory / "audit.jsonl"

    def record(self, entry: ToolCallRecord) -> None:
        self.tool_calls.append(entry)
        if self.audit_path is not None:
            self.audit_path.parent.mkdir(parents=True, exist_ok=True)
            with self.audit_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry.to_json(), default=str) + "\n")

    def save_result(self, step: int, call_id: str, text: str) -> Path | None:
        if self.directory is None:
            return None
        safe_id = "".join(c if c.isalnum() or c in "_-" else "_" for c in call_id)[:64]
        folder = self.directory / "tool-results"
        folder.mkdir(parents=True, exist_ok=True)
        # Numbered across the whole session: steps restart each turn and ids may repeat.
        seq = len(list(folder.glob("*.json"))) + 1
        while True:
            path = folder / f"{seq:04d}-step{step:03d}-{safe_id}.json"
            try:
                # Exclusive create: two sessions sharing the folder never overwrite a file.
                with path.open("x", encoding="utf-8") as fh:
                    fh.write(text)
            except FileExistsError:
                seq += 1
                continue
            return path


def now() -> datetime:
    return datetime.now(UTC)
