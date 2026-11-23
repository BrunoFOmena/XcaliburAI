"""Run provenance: everything needed to reproduce and audit an execution."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator

from xcalibur.version import __version__
from xcalibur.workspace import validate_run_id


class RunRecord(BaseModel):
    run_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    xcalibur_version: str = __version__
    domain: str
    domain_version: str
    engine: str
    command: list[str]
    params: dict[str, Any]
    inputs: dict[str, str] = Field(default_factory=dict)
    status: str = "planned"
    returncode: int | None = None
    engine_version: str | None = None
    container: str | None = None
    resumed: bool = False
    attempt: int = 1
    finished_at: datetime | None = None

    @field_validator("run_id")
    @classmethod
    def _safe_run_id(cls, value: str) -> str:
        return validate_run_id(value)

    @property
    def filename(self) -> str:
        """First attempt: ``run-<id>.json``; later ones: ``run-<id>.attempt-<n>.json``."""
        return self.filename_for(self.run_id, self.attempt)

    @staticmethod
    def next_attempt(directory: Path, run_id: str) -> int:
        """Number of the next attempt, from the records already in ``directory``."""
        validate_run_id(run_id)
        existing = {p.name for p in directory.glob(f"run-{run_id}*.json")}
        attempt = 1
        while RunRecord.filename_for(run_id, attempt) in existing:
            attempt += 1
        return attempt

    def reserve(self, directory: Path) -> Path:
        """Claim the next free attempt number in ``directory`` and write this record there.

        The file is created exclusively (``open(..., "x")``), so two runs of the same
        ``run_id`` started at the same time never get the same attempt. Sets ``attempt``
        and ``status="running"``; :meth:`write` later replaces the file with the outcome.
        """
        directory.mkdir(parents=True, exist_ok=True)
        attempt = self.next_attempt(directory, self.run_id)
        while True:
            path = directory / self.filename_for(self.run_id, attempt)
            try:
                handle = path.open("x", encoding="utf-8")
            except FileExistsError:
                attempt += 1
                continue
            self.attempt = attempt
            self.status = "running"
            with handle:
                handle.write(self.model_dump_json(indent=2))
            return path

    @staticmethod
    def filename_for(run_id: str, attempt: int) -> str:
        if attempt == 1:
            return f"run-{run_id}.json"
        return f"run-{run_id}.attempt-{attempt}.json"

    def write(self, directory: Path) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / self.filename
        path.write_text(self.model_dump_json(indent=2), encoding="utf-8")
        return path
