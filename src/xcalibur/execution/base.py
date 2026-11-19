"""Engine-neutral run contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

from xcalibur.registry import DomainPlugin

RunStatus = Literal["planned", "succeeded", "failed"]


@dataclass
class RunSpec:
    plugin: DomainPlugin
    params: dict[str, Any]
    """Already validated by ``DomainManifest.resolve_params``."""
    inputs: dict[str, Path] = field(default_factory=dict)
    workdir: Path = Path(".")
    dry_run: bool = True


@dataclass
class RunResult:
    status: RunStatus
    command: list[str]
    returncode: int | None = None
    log_path: Path | None = None


class WorkflowEngine(Protocol):
    name: str

    def build_command(self, spec: RunSpec) -> list[str]: ...

    def run(self, spec: RunSpec) -> RunResult: ...
