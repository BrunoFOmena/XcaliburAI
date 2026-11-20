"""Core tools every agent can use: discover domains, plan, run and inspect runs.

Domain packages add their own tools through ``DomainPlugin.tools``; they are registered
as ``<domain>__<tool>`` so they never collide with these.
"""

from __future__ import annotations

import shlex
import uuid
from pathlib import Path
from typing import Any

from pydantic import Field

from xcalibur.agents.tools import Tool, ToolContext, ToolError, ToolInput, ToolRegistry
from xcalibur.execution.base import EngineNotFoundError, RunSpec, WorkflowEngine
from xcalibur.execution.nextflow import NextflowEngine
from xcalibur.manifest import ManifestError
from xcalibur.provenance import RunRecord
from xcalibur.registry import DomainPlugin, DomainRegistry
from xcalibur.workspace import Workspace

RUN_ID_PATTERN = r"^[A-Za-z0-9_-]+$"
MAX_FILES_PER_OUTPUT = 50
LOG_TAIL_LINES = 20

ParamValue = str | int | float | bool


class NoArgs(ToolInput):
    pass


class DomainArgs(ToolInput):
    domain: str = Field(description="Domain name, as list_domains returns it.")


class RunArgs(ToolInput):
    domain: str = Field(description="Domain name, as list_domains returns it.")
    params: dict[str, ParamValue] = Field(
        default_factory=dict, description="Workflow parameters declared in the domain manifest."
    )
    inputs: dict[str, str] = Field(
        default_factory=dict,
        description="Input name -> path. Relative paths are resolved against the workspace.",
    )
    run_id: str = Field(
        default_factory=lambda: uuid.uuid4().hex[:12],
        pattern=RUN_ID_PATTERN,
        max_length=64,
        description="Run identifier; leave empty to generate one.",
    )
    resume: bool = Field(default=False, description="Resume an earlier run with the same id.")


class RunIdArgs(ToolInput):
    run_id: str = Field(pattern=RUN_ID_PATTERN, max_length=64)


# -- tools --------------------------------------------------------------------------------


def list_domains(args: NoArgs, ctx: ToolContext) -> list[dict[str, Any]]:
    return [
        {
            "name": p.name,
            "display_name": p.manifest.display_name,
            "version": p.manifest.version,
            "description": p.manifest.description.strip(),
        }
        for p in ctx.domains
    ]


def describe_domain(args: DomainArgs, ctx: ToolContext) -> dict[str, Any]:
    plugin = _plugin(ctx, args.domain)
    manifest = plugin.manifest
    return {
        "name": manifest.name,
        "display_name": manifest.display_name,
        "version": manifest.version,
        "description": manifest.description.strip(),
        "engine": manifest.workflow.engine,
        "inputs": [i.model_dump() for i in manifest.inputs],
        "params": [p.model_dump() for p in manifest.params],
        "outputs": [o.model_dump() for o in manifest.outputs],
        "agents": [{"name": a.name, "role": a.role, "description": a.description}
                   for a in plugin.agents],
    }


def plan_run(args: RunArgs, ctx: ToolContext) -> dict[str, Any]:
    spec = _spec(args, ctx, dry_run=True)
    return _describe(spec, _engine(ctx))


def preview_run(args: RunArgs, ctx: ToolContext) -> dict[str, Any]:
    spec = _spec(args, ctx, dry_run=False)
    return _describe(spec, _engine(ctx))


def run_pipeline(args: RunArgs, ctx: ToolContext) -> dict[str, Any]:
    spec = _spec(args, ctx, dry_run=False)
    try:
        result = _engine(ctx).run(spec)
    except EngineNotFoundError as exc:
        raise ToolError(str(exc), kind="engine_not_found") from None
    except ValueError as exc:
        raise ToolError(str(exc), kind="invalid_run") from None
    return {
        "run_id": result.run_id,
        "status": result.status,
        "returncode": result.returncode,
        "command": result.command,
        "run_dir": result.run_dir,
        "log_path": result.log_path,
        "record_path": result.record_path,
    }


def get_run_status(args: RunIdArgs, ctx: ToolContext) -> dict[str, Any]:
    run_dir, record, attempts = _latest_record(ctx, args.run_id)
    status: dict[str, Any] = {"run_id": args.run_id, "run_dir": run_dir, "attempts": attempts}
    if record is None:
        status["status"] = "no_record"
        status["message"] = "No provenance record yet: the run may still be in progress."
    else:
        status.update(
            status=record.status,
            attempt=record.attempt,
            domain=record.domain,
            returncode=record.returncode,
            created_at=record.created_at,
            finished_at=record.finished_at,
            command=record.command,
        )
    log = run_dir / "nextflow.log"
    if log.is_file():
        lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
        status["log_path"] = log
        status["log_tail"] = lines[-LOG_TAIL_LINES:]
    return status


def read_results(args: RunIdArgs, ctx: ToolContext) -> dict[str, Any]:
    run_dir, record, _ = _latest_record(ctx, args.run_id)
    if record is None:
        raise ToolError(
            f"Run {args.run_id!r} has no provenance record yet; check get_run_status.",
            kind="run_not_finished",
        )
    plugin = _plugin(ctx, record.domain)
    root = run_dir.resolve()
    outputs: list[dict[str, Any]] = []
    missing: list[str] = []
    for output in plugin.manifest.outputs:
        files = sorted(
            p for p in run_dir.glob(output.pattern)
            if p.is_file() and p.resolve().is_relative_to(root)
        )
        if not files:
            missing.append(output.name)
        outputs.append(
            {
                "name": output.name,
                "kind": output.kind,
                "description": output.description,
                "files": [
                    {"path": p, "size_bytes": p.stat().st_size}
                    for p in files[:MAX_FILES_PER_OUTPUT]
                ],
                "more_files": max(0, len(files) - MAX_FILES_PER_OUTPUT),
            }
        )
    return {
        "run_id": args.run_id,
        "status": record.status,
        "run_dir": run_dir,
        "outputs": outputs,
        "missing_outputs": missing,
    }


CORE_TOOLS: tuple[Tool, ...] = (
    Tool("list_domains", "List the installed domain packages (workflows).", NoArgs, list_domains),
    Tool(
        "describe_domain",
        "Show a domain's inputs, parameters (types, defaults, choices) and outputs.",
        DomainArgs,
        describe_domain,
    ),
    Tool(
        "plan_run",
        "Validate parameters and inputs for a domain and return the exact command that "
        "would run, without running anything.",
        RunArgs,
        plan_run,
    ),
    Tool(
        "run_pipeline",
        "Run a domain's workflow in the workspace. The user must confirm first; call "
        "plan_run and explain the plan before using this.",
        RunArgs,
        run_pipeline,
        requires_confirmation=True,
        preview=preview_run,
    ),
    Tool(
        "get_run_status",
        "Status of a run: result of the latest attempt and the end of its log.",
        RunIdArgs,
        get_run_status,
    ),
    Tool(
        "read_results",
        "List the output files a finished run produced, per declared output.",
        RunIdArgs,
        read_results,
    ),
)


def domain_tool_name(domain: str, tool: str) -> str:
    return f"{domain}__{tool}"


def build_tool_registry(domains: DomainRegistry, core: bool = True) -> ToolRegistry:
    """Core tools plus every tool the installed domains contribute (prefixed)."""
    registry = ToolRegistry(CORE_TOOLS if core else ())
    for plugin in domains:
        for tool in plugin.tools:
            registry.register(tool, name=domain_tool_name(plugin.name, tool.name))
    return registry


# -- helpers ------------------------------------------------------------------------------


def _engine(ctx: ToolContext) -> WorkflowEngine:
    return ctx.engine if ctx.engine is not None else NextflowEngine()


def _workspace(ctx: ToolContext) -> Workspace:
    if ctx.workspace is None:
        raise ToolError("No workspace is configured, so runs cannot be used.", kind="no_workspace")
    return ctx.workspace


def _plugin(ctx: ToolContext, name: str) -> DomainPlugin:
    try:
        return ctx.domains.get(name)
    except KeyError as exc:
        raise ToolError(exc.args[0], kind="unknown_domain", available=ctx.domains.names()) from None


def _spec(args: RunArgs, ctx: ToolContext, dry_run: bool) -> RunSpec:
    plugin = _plugin(ctx, args.domain)
    manifest = plugin.manifest
    try:
        params = manifest.resolve_params(args.params)
    except ManifestError as exc:
        raise ToolError(str(exc), kind="invalid_params") from None

    declared = {i.name: i for i in manifest.inputs}
    unknown = sorted(set(args.inputs) - set(declared))
    if unknown:
        raise ToolError(
            f"unknown inputs for '{manifest.name}': {unknown}",
            kind="invalid_inputs",
            declared=sorted(declared),
        )
    missing = sorted(n for n, i in declared.items() if i.required and n not in args.inputs)
    if missing:
        raise ToolError(f"missing required inputs: {missing}", kind="invalid_inputs")

    base = ctx.workspace.root if ctx.workspace is not None else Path.cwd()
    inputs = {name: _resolve(base, raw) for name, raw in args.inputs.items()}
    workspace = None
    if not dry_run:
        workspace = _workspace(ctx)
        absent = sorted(n for n, p in inputs.items() if not p.exists())
        if absent:
            raise ToolError(
                f"input paths do not exist: {absent}",
                kind="invalid_inputs",
                paths={n: inputs[n] for n in absent},
            )
        run_dir = workspace.run_dir(args.run_id)
        if not args.resume and RunRecord.next_attempt(run_dir, args.run_id) > 1:
            raise ToolError(
                f"run {args.run_id!r} already exists; pass resume=true or use another run_id.",
                kind="run_exists",
            )
    elif ctx.workspace is not None:
        workspace = ctx.workspace
    return RunSpec(
        plugin=plugin,
        params=params,
        inputs=inputs,
        workspace=workspace,
        run_id=args.run_id,
        dry_run=dry_run,
        resume=args.resume,
    )


def _resolve(base: Path, raw: str) -> Path:
    path = Path(raw).expanduser()
    return (path if path.is_absolute() else base / path).absolute()


def _describe(spec: RunSpec, engine: WorkflowEngine) -> dict[str, Any]:
    try:
        command = engine.build_command(spec)
    except ValueError as exc:
        raise ToolError(str(exc), kind="invalid_params") from None
    return {
        "domain": spec.plugin.name,
        "run_id": spec.run_id,
        "params": spec.params,
        "inputs": {k: str(v) for k, v in spec.inputs.items()},
        "resume": spec.resume,
        "engine": engine.name,
        "command": command,
        "command_text": shlex.join(command),
        "run_dir": spec.run_dir.absolute() if spec.workspace is not None else None,
    }


def _latest_record(ctx: ToolContext, run_id: str) -> tuple[Path, RunRecord | None, int]:
    run_dir = _workspace(ctx).run_dir(run_id)
    if not run_dir.is_dir():
        raise ToolError(f"There is no run {run_id!r} in the workspace.", kind="unknown_run")
    attempts = RunRecord.next_attempt(run_dir, run_id) - 1
    if attempts == 0:
        return run_dir, None, 0
    path = run_dir / RunRecord.filename_for(run_id, attempts)
    try:
        record = RunRecord.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ToolError(f"Could not read {path.name}: {exc}", kind="bad_record") from None
    return run_dir, record, attempts
