"""Terminal output for the CLI: the project's red palette and live progress from a run log."""

from __future__ import annotations

import os
import re
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import TextIO

from xcalibur.execution.base import RunResult, RunSpec, WorkflowEngine
from xcalibur.provenance import RunRecord

# 256-colour codes close to the project's palette (#cc2c18 / #8b1a0e).
_STYLES = {
    "brand": "1;38;5;160",  # bold red
    "accent": "38;5;160",  # red
    "deep": "38;5;88",  # dark red
    "error": "1;38;5;196",  # bright red
    "warn": "38;5;214",  # amber
    "ok": "38;5;71",  # muted green
    "dim": "2",
    "bold": "1",
}


def color_enabled(stream: TextIO, no_color: bool = False) -> bool:
    """Colour only on a terminal, and never with ``--no-color`` or ``NO_COLOR`` set."""
    if no_color or os.environ.get("NO_COLOR"):
        return False
    isatty = getattr(stream, "isatty", None)
    return bool(isatty and isatty())


class Style:
    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def __call__(self, name: str, text: str) -> str:
        if not self.enabled:
            return text
        return f"\033[{_STYLES[name]}m{text}\033[0m"


# Escape sequences a tool may write to its log (colours, charset switches, cursor moves).
_ANSI = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|[()][A-Za-z0-9]|[@-Z\\-_])")


def strip_ansi(text: str) -> str:
    return _ANSI.sub("", text)


LOG_NAME = "nextflow.log"
POLL_SECONDS = 0.2


class LogFollower:
    """Prints the lines appended to a log file, from a background thread.

    With ``from_end`` it skips what the file already holds (a resumed run appends to the
    same log); otherwise it reads from the start (a first attempt rewrites the log). It waits
    for the file to appear, and on ``stop()`` prints whatever is left.
    """

    def __init__(
        self,
        path: Path,
        emit: Callable[[str], None],
        from_end: bool = False,
        poll: float = POLL_SECONDS,
    ) -> None:
        self.path = path
        self.emit = emit
        self.poll = poll
        self._offset = path.stat().st_size if from_end and path.is_file() else 0
        self._partial = ""
        self._done = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="xcalibur-log", daemon=True)

    def start(self) -> LogFollower:
        self._thread.start()
        return self

    def stop(self) -> None:
        self._done.set()
        self._thread.join(timeout=5)
        self._read(final=True)

    def _loop(self) -> None:
        while not self._done.wait(self.poll):
            self._read()

    def _read(self, final: bool = False) -> None:
        try:
            if not self.path.is_file():
                return
            if self.path.stat().st_size < self._offset:  # truncated: a new log started
                self._offset = 0
            with self.path.open("r", encoding="utf-8", errors="replace") as fh:
                fh.seek(self._offset)
                chunk = fh.read()
                self._offset = fh.tell()
        except OSError:
            return
        text = self._partial + chunk
        lines = text.split("\n")
        self._partial = "" if final else lines.pop()
        for line in lines:
            if line or not final:
                self.emit(line.rstrip("\r"))


class FollowingEngine:
    """Wraps an engine so a real run streams its log to ``emit`` while it runs."""

    def __init__(self, engine: WorkflowEngine, emit: Callable[[str], None]) -> None:
        self.engine = engine
        self.emit = emit
        self.name = engine.name

    def build_command(self, spec: RunSpec) -> list[str]:
        return self.engine.build_command(spec)

    def run(self, spec: RunSpec) -> RunResult:
        if spec.dry_run or spec.workspace is None:
            return self.engine.run(spec)
        run_dir = spec.run_dir.absolute()
        resumed = RunRecord.next_attempt(run_dir, spec.run_id) > 1
        follower = LogFollower(run_dir / LOG_NAME, self.emit, from_end=resumed).start()
        try:
            return self.engine.run(spec)
        finally:
            follower.stop()


def stderr_printer(style: Style, stream: TextIO | None = None) -> Callable[[str], None]:
    """Progress lines go to stderr, so stdout stays clean for ``--json``. The log's own
    escape codes are dropped: only our style (or none, with ``--no-color``) is applied."""

    def emit(line: str) -> None:
        out = stream if stream is not None else sys.stderr
        print(style("dim", f"  │ {strip_ansi(line)}"), file=out, flush=True)

    return emit
