"""Run provenance: everything needed to reproduce and audit an execution."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from xcalibur.version import __version__


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
    finished_at: datetime | None = None

    def write(self, directory: Path) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"run-{self.run_id}.json"
        path.write_text(self.model_dump_json(indent=2), encoding="utf-8")
        return path
