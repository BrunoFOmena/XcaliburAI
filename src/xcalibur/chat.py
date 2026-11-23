"""Interactive chat: the user talks to the agent runtime from the terminal.

The agent plans with its tools; anything that acts (``run_pipeline``) is shown in full and
runs only after the user answers yes, unless ``--yes`` was given explicitly.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO

from xcalibur.agents.confirmation import AllowAll, CallbackConfirmation, ConfirmationRequest
from xcalibur.agents.core_tools import build_tool_registry
from xcalibur.agents.runtime import DEFAULT_SYSTEM_PROMPT, AgentLoop, LoopResult
from xcalibur.agents.session import Session
from xcalibur.agents.tools import ToolContext
from xcalibur.execution.base import WorkflowEngine
from xcalibur.llm.base import LLMProvider
from xcalibur.provenance import RunRecord
from xcalibur.registry import DomainRegistry
from xcalibur.terminal import Style
from xcalibur.version import __version__
from xcalibur.workspace import Workspace

EXIT_WORDS = {"/exit", "/quit"}

HELP = """\
Commands:
  /help     show this help
  /status   provider, model, workspace and what this session did
  /runs     runs in the workspace and their status
  /exit     leave the chat (also Ctrl+D)
Anything else is sent to the assistant. Ctrl+C stops the current answer."""

Reader = Callable[[str], str]


def chat_system_prompt(domains: DomainRegistry) -> str:
    """The runtime's prompt plus the instructions of every installed domain's agents.

    The chat runs one assistant with every tool, so it follows each domain agent's
    instructions when it does that agent's job (running, interpreting, ...)."""
    parts = [DEFAULT_SYSTEM_PROMPT]
    for plugin in domains:
        for agent in plugin.agents:
            if agent.instructions:
                parts.append(
                    f"## When acting as {agent.name} ({agent.role}) for the "
                    f"{plugin.name} domain\n\n{agent.instructions}"
                )
    return "\n\n".join(parts)


@dataclass
class ChatApp:
    provider: LLMProvider
    domains: DomainRegistry
    workspace: Workspace
    engine: WorkflowEngine
    style: Style
    auto_confirm: bool = False
    json_mode: bool = False
    max_steps: int | None = None
    read: Reader | None = None
    """Reads one line; defaults to ``input`` (looked up at call time)."""
    out: TextIO = field(default_factory=lambda: sys.stdout)
    err: TextIO = field(default_factory=lambda: sys.stderr)
    session: Session = field(init=False)
    loop: AgentLoop = field(init=False)
    turns: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        self.session = Session()
        self.session.directory = self.workspace.root / "sessions" / self.session.id
        confirmation = AllowAll() if self.auto_confirm else CallbackConfirmation(self._ask_run)
        options: dict[str, Any] = {
            "confirmation": confirmation,
            "system_prompt": chat_system_prompt(self.domains),
        }
        if self.max_steps is not None:
            options["max_steps"] = self.max_steps
        self.loop = AgentLoop(
            self.provider,
            build_tool_registry(self.domains),
            ToolContext(domains=self.domains, workspace=self.workspace, engine=self.engine),
            **options,
        )

    # -- the loop ---------------------------------------------------------------------

    def run(self) -> int:
        self._banner()
        while True:
            try:
                line = self._input(self._prompt())
            except EOFError:
                self._info("")
                return 0
            except KeyboardInterrupt:
                self._info("")
                return 130
            text = line.strip()
            if not text:
                continue
            if text.startswith("/"):
                if text.split()[0] in EXIT_WORDS:
                    return 0
                self.command(text)
                continue
            self.turn(text)

    def turn(self, text: str) -> LoopResult | None:
        self.turns += 1
        try:
            result = self.loop.run(text, self.session)
        except KeyboardInterrupt:
            self._info(self.style("warn", "interrupted; the session continues"))
            return None
        self._show(result)
        return result

    def command(self, text: str) -> None:
        name = text.split()[0]
        if name == "/help":
            self._info(HELP)
        elif name == "/status":
            self._info(self._status())
        elif name == "/runs":
            self._info(self._runs())
        else:
            self._info(self.style("warn", f"unknown command {name}; type /help"))

    # -- confirmation -----------------------------------------------------------------

    def _ask_run(self, request: ConfirmationRequest) -> bool:
        self._info(self._plan_text(request))
        try:
            answer = self._input(self.style("brand", "Run it? [y/N] "))
        except (EOFError, KeyboardInterrupt):
            self._info("")
            answer = ""
        approved = answer.strip().lower() in {"y", "yes", "s", "sim"}
        if not approved:
            self._info(self.style("warn", "not run"))
        return approved

    def _plan_text(self, request: ConfirmationRequest) -> str:
        d = request.details
        s = self.style
        lines = [s("brand", f"The assistant wants to run {d.get('domain', request.tool)}:")]
        params = d.get("params") or {}
        inputs = d.get("inputs") or {}
        for label, values in (("params", params), ("inputs", inputs)):
            if values:
                lines.append(f"  {s('accent', label)}")
                lines += [f"    {k} = {v}" for k, v in values.items()]
        if d.get("run_id"):
            lines.append(f"  {s('accent', 'run id')}   {d['run_id']}" +
                         ("  (resume)" if d.get("resume") else ""))
        if d.get("run_dir"):
            lines.append(f"  {s('accent', 'run dir')}  {d['run_dir']}")
        if d.get("profile"):
            lines.append(f"  {s('accent', 'profile')}  {d['profile']}")
        check = d.get("input_check") or {}
        if check.get("overview"):
            lines.append(f"  {s('accent', 'detected')} {check['overview']}")
        lines += [s("warn", f"  warning  {w['message']}") for w in check.get("warnings", [])]
        lines.append(f"  {s('accent', 'command')}  {request.summary}")
        return "\n".join(lines)

    # -- output -----------------------------------------------------------------------

    def _show(self, result: LoopResult) -> None:
        if self.json_mode:
            payload = {
                "text": result.text,
                "stop_reason": result.stop_reason,
                "steps": result.steps,
                "error": result.error,
                "tool_calls": [r.to_json() for r in result.tool_calls],
            }
            print(json.dumps(payload, ensure_ascii=False, default=str), file=self.out, flush=True)
            return
        for record in result.tool_calls:
            mark = "✓" if record.status == "ok" else "✗"
            self._info(self.style("dim", f"  {mark} {record.tool} ({record.status})"))
        if result.stop_reason == "llm_error":
            print(self.style("error", result.text), file=self.out, flush=True)
            return
        print(f"{self.style('brand', 'xcalibur ›')} {result.text}", file=self.out, flush=True)
        if result.stop_reason == "max_steps":
            self._info(self.style("warn", "(stopped at the step limit)"))

    def _banner(self) -> None:
        config = self.provider.config
        s = self.style
        self._info(
            f"{s('brand', 'XcaliburAI')} {__version__}  "
            f"{s('dim', f'{config.provider} · {config.model} · workspace {self.workspace.root}')}"
        )
        confirm = "runs need your confirmation" if not self.auto_confirm else "--yes: runs start"
        self._info(s("dim", f"Type /help for commands, /exit to leave; {confirm}."))

    def _prompt(self) -> str:
        return self.style("accent", "you › ")

    def _status(self) -> str:
        config = self.provider.config
        runs = [r for r in self.session.tool_calls if r.tool == "run_pipeline" and r.status == "ok"]
        lines = [
            f"provider   {config.provider}",
            f"model      {config.model}",
            f"workspace  {self.workspace.root}",
            f"session    {self.session.id} ({self.session.directory})",
            f"turns      {self.turns}",
            f"tool calls {len(self.session.tool_calls)}",
            f"runs       {len(runs)} started in this session",
            "confirm    " + ("automatic (--yes)" if self.auto_confirm else "ask before running"),
        ]
        return "\n".join(lines)

    def _runs(self) -> str:
        rows = list_runs(self.workspace)
        if not rows:
            return f"no runs in {self.workspace.runs}"
        return "\n".join(
            f"{r['run_id']:<14} {r['status']:<10} {r['domain'] or '-':<12} {r['created_at'] or ''}"
            for r in rows
        )

    def _input(self, prompt: str) -> str:
        read = self.read or input
        if not self.json_mode:
            return read(prompt)
        print(prompt, end="", file=self.err, flush=True)  # keep stdout for JSON only
        return read("")

    def _info(self, text: str) -> None:
        stream = self.err if self.json_mode else self.out
        print(text, file=stream, flush=True)


def list_runs(workspace: Workspace) -> list[dict[str, Any]]:
    """Every run directory with the status of its latest attempt, newest first."""
    if not workspace.runs.is_dir():
        return []
    rows: list[dict[str, Any]] = []
    for run_dir in workspace.runs.iterdir():
        if not run_dir.is_dir():
            continue
        row: dict[str, Any] = {
            "run_id": run_dir.name,
            "status": "no_record",
            "domain": None,
            "created_at": None,
            "attempts": 0,
            "mtime": run_dir.stat().st_mtime,
        }
        try:
            attempts = RunRecord.next_attempt(run_dir, run_dir.name) - 1
        except ValueError:
            continue  # not a run directory xcalibur created
        if attempts:
            record = _read_record(run_dir / RunRecord.filename_for(run_dir.name, attempts))
            row["attempts"] = attempts
            if record is not None:
                row.update(
                    status=record.status,
                    domain=record.domain,
                    created_at=record.created_at.isoformat(timespec="seconds"),
                )
        rows.append(row)
    rows.sort(key=lambda r: r["mtime"], reverse=True)
    for row in rows:
        del row["mtime"]
    return rows


def _read_record(path: Path) -> RunRecord | None:
    try:
        return RunRecord.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
