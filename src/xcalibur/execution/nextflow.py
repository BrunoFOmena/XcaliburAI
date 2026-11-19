"""Nextflow adapter: plans and executes runs with logs and provenance."""

from __future__ import annotations

import shutil
import subprocess
from datetime import UTC, datetime

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
        cmd = [self.executable, "run", str(spec.plugin.workflow_path)]
        if workflow.profile:
            cmd += ["-profile", workflow.profile]
        if spec.resume:
            cmd.append("-resume")
        for key in sorted(spec.params):
            cmd += [f"--{key}", _render(spec.params[key])]
        for key in sorted(spec.inputs):
            cmd += [f"--{key}", str(spec.inputs[key])]
        cmd += ["-with-report", str(spec.run_dir / "nextflow_report.html")]
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
        for line in proc.stdout.splitlines():
            if line.strip():
                return line.strip()
        return None

    def run(self, spec: RunSpec) -> RunResult:
        command = self.build_command(spec)
        if spec.dry_run:
            return RunResult(run_id=spec.run_id, status="planned", command=command)

        if shutil.which(self.executable) is None:
            raise EngineNotFoundError(
                f"Nextflow executable '{self.executable}' was not found. Install Nextflow "
                "(https://www.nextflow.io/docs/latest/install.html) and make sure it is on "
                "PATH, or pass the full path to the executable."
            )

        run_dir = spec.run_dir
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
            inputs={k: str(v) for k, v in spec.inputs.items()},
            resumed=spec.resume,
        )

        status = "failed"
        returncode: int | None = None
        with log_path.open("a" if spec.resume else "w", encoding="utf-8") as log:
            try:
                proc = subprocess.Popen(  # noqa: S603 - built from manifest + validated params
                    command, cwd=run_dir, stdout=log, stderr=subprocess.STDOUT
                )
            except OSError as exc:
                log.write(f"xcalibur: could not start Nextflow: {exc}\n")
            else:
                try:
                    returncode = proc.wait()
                    status = "succeeded" if returncode == 0 else "failed"
                except KeyboardInterrupt:
                    returncode = _terminate(proc)
                    status = "cancelled"
                    log.write("xcalibur: run cancelled by user (SIGINT)\n")

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


def _terminate(proc: subprocess.Popen[bytes]) -> int:
    proc.terminate()
    try:
        return proc.wait(timeout=_CANCEL_GRACE)
    except subprocess.TimeoutExpired:
        proc.kill()
        return proc.wait()


def _render(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)
