"""Input checks a domain runs before any workflow starts.

A domain may give its :class:`~xcalibur.registry.DomainPlugin` a ``validate_inputs``
function. The core calls it from ``plan`` and ``run`` with the resolved input paths and
refuses to go on when the report has errors, so wrong inputs fail early and with a clear
message instead of late (or, worse, with plausible but wrong results).

Validators only look at names and file metadata; they never read whole files and never
change anything on disk.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

Severity = Literal["error", "warning"]


@dataclass(frozen=True)
class InputIssue:
    """One problem found in the inputs.

    ``code`` is a short machine-readable label; ``message`` names the file or sample and
    says how to fix it.
    """

    code: str
    message: str
    input: str | None = None
    path: Path | None = None
    sample: str | None = None

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"code": self.code, "message": self.message}
        for key in ("input", "path", "sample"):
            value = getattr(self, key)
            if value is not None:
                out[key] = str(value)
        return out


@dataclass
class InputReport:
    """Result of an input check: errors block the run, warnings are only shown."""

    errors: list[InputIssue] = field(default_factory=list)
    warnings: list[InputIssue] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)
    """What was detected (for ECHOLAS: samples, read layout, file sizes, reference)."""
    overview: str = ""
    """The summary in one line, for people."""

    @property
    def ok(self) -> bool:
        return not self.errors

    def error(self, code: str, message: str, **where: Any) -> None:
        self.errors.append(InputIssue(code, message, **where))

    def warn(self, code: str, message: str, **where: Any) -> None:
        self.warnings.append(InputIssue(code, message, **where))

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "overview": self.overview,
            "errors": [i.as_dict() for i in self.errors],
            "warnings": [i.as_dict() for i in self.warnings],
            "summary": self.summary,
        }


InputValidator = Callable[[Mapping[str, Path]], InputReport]
"""Receives input name -> absolute path (as given by the user) and returns a report."""
