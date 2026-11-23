"""Nextflow adapter: plans and executes runs with logs and provenance."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

from xcalibur.execution.base import (
    EngineNotFoundError,
    RunResult,
    RunSpec,
    UnsupportedPlatformError,
)
from xcalibur.provenance import RunRecord

_VERSION_TIMEOUT = 30
_CANCEL_GRACE = 10
_POLL = 0.2
_SWEEP_GRACE = 5


class NextflowEngine:
    name = "nextflow"

    def __init__(self, executable: str = "nextflow") -> None:
        self.executable = executable

    def build_command(self, spec: RunSpec) -> list[str]:
        """Build the argument list (never a shell string) from validated parts."""
        profile = spec.profile or spec.plugin.manifest.workflow.profile
        cmd = [self.executable, "run", str(spec.plugin.workflow_path.absolute())]
        if profile:
            cmd += ["-profile", profile]
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
        if not supports_execution():
            raise UnsupportedPlatformError(
                "Running workflows is supported on Linux and macOS only (Nextflow does not run "
                "natively on Windows). Use WSL, or plan the run with --dry-run."
            )

        if shutil.which(self.executable) is None:
            raise EngineNotFoundError(
                f"Nextflow executable '{self.executable}' was not found. Install Nextflow "
                "(https://www.nextflow.io/docs/latest/install.html) and make sure it is on "
                "PATH, or pass the full path to the executable."
            )

        # From here on SIGINT/SIGTERM only request cancellation; they never raise, so no
        # signal can leave Nextflow running or skip the provenance record.
        with _CancelRequest() as cancel:
            return self._execute(spec, command, cancel)

    def _execute(self, spec: RunSpec, command: list[str], cancel: _CancelRequest) -> RunResult:
        run_dir = spec.run_dir.absolute()
        run_dir.mkdir(parents=True, exist_ok=True)
        log_path = run_dir / "nextflow.log"
        manifest = spec.plugin.manifest
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
        )
        record.reserve(run_dir)  # claims the attempt number atomically (status "running")
        attempt = record.attempt

        try:
            status, returncode = self._launch(spec, command, cancel, run_dir, log_path, attempt)
        except BaseException:
            # Anything unexpected (an OSError on the log or the report, a bug while waiting):
            # the record must not stay "running" forever. Mark it failed, then re-raise.
            record.status = "failed"
            record.finished_at = datetime.now(UTC)
            record.write(run_dir)
            raise

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


    def _launch(
        self,
        spec: RunSpec,
        command: list[str],
        cancel: _CancelRequest,
        run_dir: Path,
        log_path: Path,
        attempt: int,
    ) -> tuple[str, int | None]:
        """Start Nextflow and wait for it; return ``(status, returncode)``."""
        status = "failed"
        returncode: int | None = None
        with log_path.open("a" if attempt > 1 else "w", encoding="utf-8") as log:
            if cancel.requested:
                status = "cancelled"
                log.write("xcalibur: run cancelled before Nextflow started (SIGINT/SIGTERM)\n")
            else:
                # Nextflow refuses to overwrite a report: set the old one aside, and drop it
                # only once the new process has started (put it back if it cannot start).
                report = _report_path(spec)
                previous_report = report.with_name(report.name + ".previous")
                set_aside = report.exists()
                if set_aside:
                    report.replace(previous_report)
                try:
                    proc = subprocess.Popen(  # noqa: S603 - built from manifest + validated params
                        command,
                        cwd=run_dir,
                        stdin=subprocess.DEVNULL,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        start_new_session=True,  # own process group, so children can be stopped
                    )
                except OSError as exc:
                    if set_aside:
                        previous_report.replace(report)
                    log.write(f"xcalibur: could not start Nextflow: {exc}\n")
                else:
                    if set_aside:
                        previous_report.unlink(missing_ok=True)
                    try:
                        returncode, cancelled = _wait(proc, cancel)
                    finally:
                        # Any unexpected error while waiting: never leave the group behind.
                        if proc.poll() is None:
                            returncode = _terminate(proc)
                        # Nextflow can exit (failed, cancelled or even succeeded) while tasks
                        # it started, such as a conda environment build, are still running.
                        _sweep_group(proc)
                    if cancelled:
                        status = "cancelled"
                        log.write("xcalibur: run cancelled (SIGINT/SIGTERM)\n")
                    else:
                        status = "succeeded" if returncode == 0 else "failed"
        return status, returncode


def supports_execution() -> bool:
    """True where runs can be executed and cancelled as a process group (POSIX)."""
    return hasattr(os, "killpg") and hasattr(signal, "SIGKILL")


def _report_path(spec: RunSpec) -> Path:
    return spec.run_dir.absolute() / "nextflow_report.html"


def _signal_group(proc: subprocess.Popen[bytes], sig: int) -> None:
    try:
        os.killpg(proc.pid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def _wait(proc: subprocess.Popen[bytes], cancel: _CancelRequest) -> tuple[int, bool]:
    """Wait for the run; on a cancel request stop the group. Returns (returncode, cancelled)."""
    while True:
        if cancel.requested:
            return _terminate(proc), True
        try:
            return proc.wait(timeout=_POLL), False
        except subprocess.TimeoutExpired:
            continue


def _terminate(proc: subprocess.Popen[bytes]) -> int:
    _signal_group(proc, signal.SIGTERM)
    try:
        return proc.wait(timeout=_CANCEL_GRACE)
    except subprocess.TimeoutExpired:
        _signal_group(proc, signal.SIGKILL)
        return proc.wait()


def _group_alive(proc: subprocess.Popen[bytes]) -> bool:
    try:
        os.killpg(proc.pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _sweep_group(proc: subprocess.Popen[bytes]) -> None:
    """Stop whatever is left in the run's process group after Nextflow itself exited.

    The group is the run's own session, so anything still in it is an orphan of the run.
    """
    if not _group_alive(proc):
        return
    _signal_group(proc, signal.SIGTERM)
    deadline = time.monotonic() + _SWEEP_GRACE
    while time.monotonic() < deadline:
        if not _group_alive(proc):
            return
        time.sleep(0.1)
    _signal_group(proc, signal.SIGKILL)


class _CancelRequest:
    """Turns SIGINT/SIGTERM into a cancel request for the duration of a run.

    The handlers only set a flag, so a signal can arrive at any point (before the
    subprocess starts, right after it, or a second Ctrl+C while stopping it) without
    interrupting the code that stops Nextflow and writes the record. Signals can only be
    handled in the main thread; elsewhere this is a no-op. A signal the process already
    ignores stays ignored.
    """

    _SIGNALS = (signal.SIGINT, signal.SIGTERM)

    def __init__(self) -> None:
        self.requested = False
        self._previous: dict[int, object] = {}

    def _handler(self, signum, frame) -> None:
        self.requested = True

    def __enter__(self) -> _CancelRequest:
        if threading.current_thread() is threading.main_thread():
            for sig in self._SIGNALS:
                if signal.getsignal(sig) is signal.SIG_IGN:
                    continue
                self._previous[sig] = signal.signal(sig, self._handler)
        return self

    def __exit__(self, *exc: object) -> None:
        for sig, previous in self._previous.items():
            signal.signal(sig, previous if previous is not None else signal.SIG_DFL)
        self._previous.clear()


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
