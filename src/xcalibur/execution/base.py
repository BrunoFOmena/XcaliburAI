"""Engine-neutral run contracts."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

from xcalibur.registry import DomainPlugin
from xcalibur.workspace import Workspace, validate_run_id

RunStatus = Literal["planned", "succeeded", "failed", "cancelled"]


class EngineNotFoundError(RuntimeError):
    """The workflow engine executable is not installed or not on PATH."""


@dataclass
class RunSpec:
    plugin: DomainPlugin
    params: dict[str, Any]
    """Already validated by ``DomainManifest.resolve_params``."""
    inputs: dict[str, Path] = field(default_factory=dict)
    workspace: Workspace | None = None
    workdir: Path = Path(".")
    """Used as the run directory only when no workspace is given."""
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    dry_run: bool = True
    resume: bool = False

    def __post_init__(self) -> None:
        validate_run_id(self.run_id)

    @property
    def run_dir(self) -> Path:
        if self.workspace is not None:
            return self.workspace.run_dir(self.run_id)
        return self.workdir


@dataclass
class RunResult:
    run_id: str
    status: RunStatus
    command: list[str]
    returncode: int | None = None
    log_path: Path | None = None
    run_dir: Path | None = None
    record_path: Path | None = None


class WorkflowEngine(Protocol):
    name: str

    def build_command(self, spec: RunSpec) -> list[str]: ...

    def run(self, spec: RunSpec) -> RunResult: ...
