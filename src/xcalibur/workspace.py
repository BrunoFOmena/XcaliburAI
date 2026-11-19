"""Workspace: the on-disk layout a project uses. Data stays local."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Workspace:
    root: Path

    @property
    def data(self) -> Path:
        return self.root / "data"

    @property
    def runs(self) -> Path:
        return self.root / "runs"

    @property
    def reports(self) -> Path:
        return self.root / "reports"

    def ensure(self) -> Workspace:
        for directory in (self.data, self.runs, self.reports):
            directory.mkdir(parents=True, exist_ok=True)
        return self

    def run_dir(self, run_id: str) -> Path:
        return self.runs / run_id
