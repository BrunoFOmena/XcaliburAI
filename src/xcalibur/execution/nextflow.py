"""Nextflow adapter: plans and executes runs with logs and provenance."""

from __future__ import annotations

import contextlib
import os
import shutil
import signal
import subprocess
import threading
from datetime import UTC, datetime
from pathlib import Path

from xcalibur.execution.base import EngineNotFoundError, RunResult, RunSpec
from xcalibur.provenance import RunRecord

_VERSION_TIMEOUT = 30
_CANCEL_GRACE = 10


class NextflowEngine:
    name = "nextflow"

    def __init__(self, executable: str = "nextflow") -> None:
        self.executable = executable

    def build_command(self, spec: RunSpec) -> list[str]:
        """Build the argument list (never a shell string) from validated parts."""
        workflow = spec.plugin.manifest.workflow
        cmd = [self.executable, "run", str(spec.plugin.workflow_path.absolute())]
        if workflow.profile:
            cmd += ["-profile", workflow.profile]
        if spec.resume:
            cmd.append("-resume")
        for key in sorted(spec.params):
            cmd += [f"--{key}", _render(spec.params[key])]
        for key in sorted(spec.inputs):
            # The subprocess runs inside the run directory, so paths must be absolute.
            cmd += [f"--{key}", str(spec.inputs[key].absolute())]
        cmd += ["-with-report", str(_report_path(spec))]
        return cmd

    def version(self) -> str | None:
        """Version string reported by the executable, or None if unavailable."""
        try:
            proc = subprocess.run(  # noqa: S603 - fixed argument list, no shell
                [self.executable, "-version"],
                capture_output=True,
                text=True,
                timeout=_VERSION_TIMEOUT,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        # Real Nextflow prints an ASCII banner ("N E X T F L O W") before the version line.
        for line in proc.stdout.splitlines():
            if "version" in line.lower():
                return line.strip()
        return None

    def run(self, spec: RunSpec) -> RunResult:
        command = self.build_command(spec)
        if spec.dry_run:
            return RunResult(run_id=spec.run_id, status="planned", command=command)
        if spec.workspace is None:
            raise ValueError("A workspace is required to execute a run (dry_run=False).")

        if shutil.which(self.executable) is None:
            raise EngineNotFoundError(
                f"Nextflow executable '{self.executable}' was not found. Install Nextflow "
                "(https://www.nextflow.io/docs/latest/install.html) and make sure it is on "
                "PATH, or pass the full path to the executable."
            )

        run_dir = spec.run_dir.absolute()
        run_dir.mkdir(parents=True, exist_ok=True)
        log_path = run_dir / "nextflow.log"
        manifest = spec.plugin.manifest
        attempt = RunRecord.next_attempt(run_dir, spec.run_id)
        record = RunRecord(
            run_id=spec.run_id,
            domain=manifest.name,
            domain_version=manifest.version,
            engine=self.name,
            engine_version=self.version(),
            container=manifest.workflow.container,
            command=command,
            params=spec.params,
            inputs={k: str(v.absolute()) for k, v in spec.inputs.items()},
            resumed=spec.resume,
            attempt=attempt,
        )

        status = "failed"
        returncode: int | None = None
        with log_path.open("a" if attempt > 1 else "w", encoding="utf-8") as log:
            # Nextflow refuses to overwrite a report; drop the old one only now that we start.
            _report_path(spec).unlink(missing_ok=True)
            try:
                proc = subprocess.Popen(  # noqa: S603 - built from manifest + validated params
                    command,
                    cwd=run_dir,
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,  # own process group, so children can be stopped too
                )
            except OSError as exc:
                log.write(f"xcalibur: could not start Nextflow: {exc}\n")
            else:
                with _sigterm_as_interrupt():
                    try:
                        returncode = proc.wait()
                        status = "succeeded" if returncode == 0 else "failed"
                    except KeyboardInterrupt:
                        returncode = _terminate(proc)
                        status = "cancelled"
                        log.write("xcalibur: run cancelled (SIGINT/SIGTERM)\n")

        record.status = status
        record.returncode = returncode
        record.finished_at = datetime.now(UTC)
        record_path = record.write(run_dir)
        return RunResult(
            run_id=spec.run_id,
            status=status,  # type: ignore[arg-type]
            command=command,
            returncode=returncode,
            log_path=log_path,
            run_dir=run_dir,
            record_path=record_path,
        )


def _report_path(spec: RunSpec) -> Path:
    return spec.run_dir.absolute() / "nextflow_report.html"


def _signal_group(proc: subprocess.Popen[bytes], sig: int) -> None:
    try:
        os.killpg(proc.pid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def _terminate(proc: subprocess.Popen[bytes]) -> int:
    _signal_group(proc, signal.SIGTERM)
    try:
        return proc.wait(timeout=_CANCEL_GRACE)
    except subprocess.TimeoutExpired:
        _signal_group(proc, signal.SIGKILL)
        return proc.wait()


@contextlib.contextmanager
def _sigterm_as_interrupt():
    """Treat SIGTERM like Ctrl+C while a run is in flight (main thread only)."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return

    def handler(signum, frame):
        raise KeyboardInterrupt

    previous = signal.signal(signal.SIGTERM, handler)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)


def _render(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value)
    if text.startswith("-") and not _is_number(text):
        raise ValueError(f"Parameter value {text!r} would be read as an engine option.")
    return text


def _is_number(text: str) -> bool:
    try:
        float(text)
    except ValueError:
        return False
    return True
