"""Nextflow adapter."""

from __future__ import annotations

import subprocess

from xcalibur.execution.base import RunResult, RunSpec


class NextflowEngine:
    name = "nextflow"

    def __init__(self, executable: str = "nextflow") -> None:
        self.executable = executable

    def build_command(self, spec: RunSpec) -> list[str]:
        workflow = spec.plugin.manifest.workflow
        cmd = [self.executable, "run", str(spec.plugin.workflow_path)]
        if workflow.profile:
            cmd += ["-profile", workflow.profile]
        for key in sorted(spec.params):
            cmd += [f"--{key}", _render(spec.params[key])]
        for key in sorted(spec.inputs):
            cmd += [f"--{key}", str(spec.inputs[key])]
        cmd += ["-with-report", str(spec.workdir / "nextflow_report.html")]
        return cmd

    def run(self, spec: RunSpec) -> RunResult:
        command = self.build_command(spec)
        if spec.dry_run:
            return RunResult(status="planned", command=command)

        spec.workdir.mkdir(parents=True, exist_ok=True)
        log_path = spec.workdir / "nextflow.log"
        with log_path.open("w", encoding="utf-8") as log:
            proc = subprocess.run(  # noqa: S603 - command is built from validated parts
                command, cwd=spec.workdir, stdout=log, stderr=subprocess.STDOUT, check=False
            )
        status = "succeeded" if proc.returncode == 0 else "failed"
        return RunResult(
            status=status, command=command, returncode=proc.returncode, log_path=log_path
        )


def _render(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)
