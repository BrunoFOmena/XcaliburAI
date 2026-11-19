"""Workspace: the on-disk layout a project uses. Data stays local."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_RUN_ID = re.compile(r"[A-Za-z0-9_-]+")


def validate_run_id(run_id: str) -> str:
    """Return ``run_id`` if it is safe to use in a path; raise ``ValueError`` otherwise."""
    if not isinstance(run_id, str) or _RUN_ID.fullmatch(run_id) is None:
        raise ValueError(f"Invalid run_id {run_id!r}: use only letters, digits, '_' and '-'.")
    return run_id


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
        return self.runs / validate_run_id(run_id)
