"""Agent loop: the model reads the conversation, calls tools, reads results and answers.

Safety properties the loop guarantees:

* only registered tools run, and only with arguments that pass their Pydantic model;
* model text is never executed; a tool's function is the only thing that acts;
* a tool that requires confirmation runs only after the policy approves its preview;
* failures come back to the model as structured results, so it can correct itself;
* the loop stops after ``max_steps`` model calls and says why;
* every tool call is recorded in the session for audit;
* the history stays valid for the provider: every tool call gets a result, even when
  execution is interrupted, and an empty answer is never stored as an empty turn;
* a call whose tool may have acted is never reported as not run: when only the audit
  write fails the model gets the real result, and a call interrupted after its tool
  started is reported as possibly run.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ValidationError

from xcalibur.agents.base import AgentRequest, AgentResponse, AgentSpec
from xcalibur.agents.confirmation import ConfirmationPolicy, ConfirmationRequest, DenyAll
from xcalibur.agents.grounding import ungrounded_numbers
from xcalibur.agents.session import Session, ToolCallRecord, ToolCallStatus, now
from xcalibur.agents.tools import Tool, ToolContext, ToolError, ToolRegistry
from xcalibur.llm.base import LLMProvider, Message, ToolCall, ToolResult
from xcalibur.llm.errors import LLMError

StopReason = Literal["completed", "max_steps", "llm_error"]

EMPTY_ANSWER = "(The model returned an empty answer.)"
DEFAULT_MAX_STEPS = 8
DEFAULT_MAX_RESULT_CHARS = 8000

DEFAULT_SYSTEM_PROMPT = """\
You are XcaliburAI, an assistant that runs and explains bioinformatics workflows.
Act only through the tools you are given; never write shell commands for the user to run.
Before running a pipeline, call plan_run, then tell the user what will run: the domain, \
every parameter, the inputs and the exact command. run_pipeline needs the user's \
confirmation; if it is declined, say so and do not retry on your own.
If a tool returns an error, read it, fix the arguments and try again, or explain the problem.
Every number you state about data or results must be copied from a tool result; never \
compute, estimate or recall one yourself.
Answer in the user's language."""


@dataclass
class LoopResult:
    text: str
    stop_reason: StopReason
    steps: int
    """Model calls made in this turn."""
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    """Tool calls made in this turn (the session keeps them all)."""
    error: str | None = None
    ungrounded_numbers: list[str] = field(default_factory=list)
    """Numbers in ``text`` that no grounded tool result of this turn supports (after one
    correction); the text then ends with a warning."""


GROUNDING_RETRY = (
    "[grounding check] Your answer states numbers that are not in any tool result of this "
    "turn: {numbers}. Rewrite the answer using only numbers copied from the tool results "
    "(call a tool if you need another value), or leave those numbers out. Do not mention "
    "this check."
)
GROUNDING_WARNING = (
    "⚠ These numbers could not be traced to a tool result and may be wrong: {numbers}. "
    "Check them against the output files before using them."
)


class AgentLoop:
    def __init__(
        self,
        provider: LLMProvider,
        tools: ToolRegistry,
        context: ToolContext,
        *,
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
        max_steps: int = DEFAULT_MAX_STEPS,
        max_result_chars: int = DEFAULT_MAX_RESULT_CHARS,
        confirmation: ConfirmationPolicy | None = None,
        allowed_tools: Sequence[str] | None = None,
    ) -> None:
        if max_steps < 1:
            raise ValueError("max_steps must be at least 1")
        if max_result_chars < 200:
            raise ValueError("max_result_chars must be at least 200")
        self.provider = provider
        self.tools = tools
        self.context = context
        self.system_prompt = system_prompt
        self.max_steps = max_steps
        self.max_result_chars = max_result_chars
        self.confirmation = confirmation or DenyAll()
        if allowed_tools is not None:
            unknown = [n for n in allowed_tools if n not in tools]
            if unknown:
                raise ValueError(f"allowed_tools lists unknown tools: {unknown}")
        self.allowed_tools = None if allowed_tools is None else list(allowed_tools)
        self._grounded_results: list[str] = []
        """Full text of this turn's successful grounded results."""
        self._tool_started = False
        """Whether the call being executed reached its tool function."""
        self._confirmation: Literal["approved", "declined"] | None = None
        """Confirmation outcome of the call being executed, if it was asked."""

    def tool_names(self) -> list[str]:
        return self.tools.names() if self.allowed_tools is None else list(self.allowed_tools)

    def run(self, message: str, session: Session | None = None) -> LoopResult:
        session = session if session is not None else Session()
        context = replace(self.context, session=session)
        self._grounded_results = []  # only this turn's results ground this turn's answer
        _add_user_text(session, message)
        specs = self.tools.specs(self.tool_names())
        first_record = len(session.tool_calls)

        last_text = ""
        retried = False
        for step in range(1, self.max_steps + 1):
            history = [Message(role="system", content=self.system_prompt), *session.messages]
            try:
                completion = self.provider.generate(history, specs)
            except LLMError as exc:
                return LoopResult(
                    text=f"The language model failed: {exc}",
                    stop_reason="llm_error",
                    steps=step,
                    tool_calls=session.tool_calls[first_record:],
                    error=str(exc),
                )
            last_text = completion.text
            calls = tuple(completion.tool_calls)
            if not calls:
                text = completion.text if completion.text.strip() else EMPTY_ANSWER
                session.messages.append(Message(role="assistant", content=text))
                missing = self._ungrounded(text, message)
                if missing and not retried and step < self.max_steps:
                    retried = True
                    _add_user_text(session, GROUNDING_RETRY.format(numbers=", ".join(missing)))
                    continue
                if missing:
                    text = f"{text}\n\n{GROUNDING_WARNING.format(numbers=', '.join(missing))}"
                return LoopResult(
                    text=text,
                    stop_reason="completed",
                    steps=step,
                    tool_calls=session.tool_calls[first_record:],
                    ungrounded_numbers=missing,
                )
            session.messages.append(
                Message(role="assistant", content=completion.text, tool_calls=calls)
            )
            self._run_calls(step, calls, context, session)

        note = (
            f"Stopped after {self.max_steps} steps (the configured limit) before the "
            "assistant finished; the last tool results were not answered."
        )
        return LoopResult(
            text=f"{last_text}\n\n{note}" if last_text else note,
            stop_reason="max_steps",
            steps=self.max_steps,
            tool_calls=session.tool_calls[first_record:],
        )

    def _ungrounded(self, text: str, message: str) -> list[str]:
        """Numbers of ``text`` not found in this turn's grounded results (nor the message).

        Nothing is checked when the turn used no grounded tool."""
        if not self._grounded_results:
            return []
        return ungrounded_numbers(text, [message, *self._grounded_results])

    # -- tool execution ---------------------------------------------------------------

    def _run_calls(
        self, step: int, calls: tuple[ToolCall, ...], context: ToolContext, session: Session
    ) -> None:
        """Run the calls and append their results; on any interruption (Ctrl+C, a failing
        audit write) the unfinished calls still get an error result before re-raising, so
        the history never holds a tool call without its result.

        A call's result is kept before its audit entry is written, so when only the audit
        write fails the model still gets what the tool really returned. A call interrupted
        after its tool function started is reported as possibly run, never as not run."""
        outcomes: list[tuple[ToolResult, ToolCallRecord]] = []
        # Ids of records whose audit write finished, or failed with OSError (not retried).
        written: set[int] = set()
        current: tuple[ToolResult, ToolCallRecord] | None = None
        attempted = 0
        self._tool_started = False
        self._confirmation = None
        try:
            for call in calls:
                # Reset before counting the call, so an interruption before _execute never
                # reports the previous call's state for this one.
                self._tool_started = False
                self._confirmation = None
                attempted += 1
                current = None
                current = self._execute(step, call, context, session)
                outcomes.append(current)
                session.remember(current[1])
                try:
                    session.write_audit(current[1])
                except OSError:
                    written.add(id(current[1]))
                    raise
                written.add(id(current[1]))
        finally:
            # A Ctrl+C can land between any two lines above: a call that returned always
            # keeps its real result and its record, and its audit line is written again if
            # its write did not finish (at worst it appears twice, never zero times).
            if current is not None and (not outcomes or outcomes[-1] is not current):
                outcomes.append(current)
            results = [result for result, _ in outcomes]
            done = len(outcomes)
            pending = calls[done:]
            contents = [
                _serialize(_interrupted(started=index < attempted and self._tool_started))
                for index in range(done, len(calls))
            ]
            for call, content in zip(pending, contents, strict=True):
                results.append(ToolResult(call.id, content, is_error=True))
            session.messages.append(Message(role="user", tool_results=tuple(results)))
            for _, record in outcomes:
                if id(record) not in written:
                    _audit_quietly(session, record)
            # The pending calls never got a record: the one interrupted had not reached it
            # (it keeps the confirmation it got), and the others did not start.
            for index, (call, content) in enumerate(zip(pending, contents, strict=True), done):
                confirmation = self._confirmation if index < attempted else None
                _record_quietly(session, step, call, content, confirmation)

    def _execute(
        self, step: int, call: ToolCall, context: ToolContext, session: Session
    ) -> tuple[ToolResult, ToolCallRecord]:
        """Run one call; return the result for the model and its audit record (written by
        the caller, after the result is safe)."""
        started = now()
        self._tool_started = False
        self._confirmation = None
        tool: Tool | None = None
        confirmation: Literal["approved", "declined"] | None = None
        status: ToolCallStatus
        call_context = context
        try:
            tool = self._tool(call.name)
            args = _validate(tool, call.input)
            if tool.requires_confirmation:
                approved, plan = self._confirm(call.name, tool, args, context)
                confirmation = "approved" if approved else "declined"
                self._confirmation = confirmation
                if not approved:
                    raise ToolError(
                        "The user did not confirm; nothing was executed.",
                        kind="confirmation_declined",
                        plan=plan,
                    )
                # The tool can check that what it runs is exactly what was approved.
                call_context = replace(context, approved_plan=plan)
            self._tool_started = True  # from here on the tool may have acted
            payload: Any = {"result": tool.func(args, call_context)}
            status = "ok"
        except _UnknownTool as exc:
            status, payload = "unknown_tool", _error("unknown_tool", str(exc), available=exc.names)
        except _InvalidArguments as exc:
            status, payload = "invalid_arguments", _error(
                "invalid_arguments", "Arguments do not match the tool's schema.", errors=exc.errors
            )
        except ToolError as exc:
            status = "declined" if exc.kind == "confirmation_declined" else "error"
            payload = _error(exc.kind, str(exc), **exc.details)
        except Exception as exc:  # noqa: BLE001 - any tool bug must reach the model, not crash
            status = "internal_error"
            payload = _error("internal_error", f"{type(exc).__name__}: {exc}")

        try:
            text = _serialize(payload)
        except Exception as exc:  # noqa: BLE001 - circular, too deep (RecursionError), bad type
            status = "internal_error"
            text = _serialize(
                _error(
                    "internal_error",
                    f"Result is not serializable: {type(exc).__name__}: {str(exc)[:300]}",
                )
            )
        content, truncated, saved = self._bounded(step, call.id, text, session)
        record = ToolCallRecord(
            step=step,
            call_id=call.id,
            tool=call.name,
            arguments=dict(call.input),
            status=status,
            started_at=started,
            finished_at=now(),
            confirmation=confirmation,
            truncated=truncated,
            result_path=None if saved is None else str(saved),
            summary=content[:500],
        )
        if status == "ok" and tool is not None and tool.grounded:
            self._grounded_results.append(text)
        result = ToolResult(tool_call_id=call.id, content=content, is_error=status != "ok")
        return result, record

    def _tool(self, name: str) -> Tool:
        names = self.tool_names()
        if name not in names:
            raise _UnknownTool(f"There is no tool named {name!r}.", names)
        return self.tools.get(name)

    def _confirm(
        self, name: str, tool: Tool, args: BaseModel, context: ToolContext
    ) -> tuple[bool, dict[str, Any]]:
        assert tool.preview is not None  # enforced by Tool
        plan = _plain(tool.preview(args, context))
        if not isinstance(plan, dict):
            plan = {"preview": plan}
        summary = plan.get("command_text") or f"{name}({args.model_dump_json()})"
        # The policy gets a copy: whatever it does to it, the tool is compared against
        # the plan as it was shown.
        request = ConfirmationRequest(
            tool=name,
            arguments=args.model_dump(mode="json"),
            summary=str(summary),
            details=copy.deepcopy(plan),
        )
        return bool(self.confirmation.confirm(request)), plan

    def _bounded(
        self, step: int, call_id: str, text: str, session: Session
    ) -> tuple[str, bool, Path | None]:
        """Fit ``text`` in ``max_result_chars``: a long result is saved to a file and the
        model gets a note with a preview. The note always fits: the preview shrinks first,
        then the path is made relative to the session directory, then left out (the audit
        record keeps the full path)."""
        if len(text) <= self.max_result_chars:
            return text, False, None
        saved: Path | None = None
        save_error: str | None = None
        try:
            saved = session.save_result(step, call_id, text)
        except OSError as exc:  # the result still reaches the model, as a preview
            save_error = f"{type(exc).__name__}: {exc}"[:300]
        extras: list[dict[str, Any]] = []
        if saved is not None:
            extras.append({"full_result_path": str(saved)})
            if session.directory is not None:
                relative = saved.relative_to(session.directory).as_posix()
                extras.append({"full_result_path": relative, "relative_to": "session directory"})
        elif save_error is not None:
            extras.append({"full_result_path": None, "save_error": save_error})
        extras.append({"full_result_path": None})
        for extra in extras:
            note = self._note(text, extra)
            if note is not None:
                return note, True, saved
        raise AssertionError("unreachable: the bare note fits in the minimum limit")

    def _note(self, text: str, extra: dict[str, Any]) -> str | None:
        """Truncation note with the longest preview that fits, or None if none does."""
        keep = self.max_result_chars
        while True:
            note = _serialize(
                {"truncated": True, "total_chars": len(text), **extra, "preview": text[:keep]}
            )
            # Escaping can make the note longer than the preview; shrink until it fits.
            excess = len(note) - self.max_result_chars
            if excess <= 0:
                return note
            if keep == 0:
                return None
            keep = max(0, keep - max(excess, 1))


class LLMAgent:
    """An :class:`~xcalibur.agents.base.Agent` backed by the loop and one session."""

    def __init__(self, spec: AgentSpec, loop: AgentLoop, session: Session | None = None) -> None:
        self.spec = spec
        self.loop = loop
        self.session = session if session is not None else Session()

    @classmethod
    def from_spec(
        cls,
        spec: AgentSpec,
        provider: LLMProvider,
        tools: ToolRegistry,
        context: ToolContext,
        session: Session | None = None,
        **loop_options: Any,
    ) -> LLMAgent:
        """Build the agent's loop: its instructions extend the system prompt, and when the
        spec names tools only those are offered to the model."""
        prompt = loop_options.pop("system_prompt", DEFAULT_SYSTEM_PROMPT)
        if spec.instructions:
            prompt = f"{prompt}\n\n{spec.instructions}"
        allowed = list(spec.tools) if spec.tools else None
        loop = AgentLoop(
            provider, tools, context, system_prompt=prompt, allowed_tools=allowed, **loop_options
        )
        return cls(spec, loop, session)

    def handle(self, request: AgentRequest) -> AgentResponse:
        result = self.loop.run(request.message, self.session)
        return AgentResponse(
            text=result.text,
            data={
                "stop_reason": result.stop_reason,
                "steps": result.steps,
                "error": result.error,
                "tool_calls": [r.to_json() for r in result.tool_calls],
            },
        )


# -- helpers ------------------------------------------------------------------------------


class _UnknownTool(Exception):
    def __init__(self, message: str, names: list[str]) -> None:
        super().__init__(message)
        self.names = names


class _InvalidArguments(Exception):
    def __init__(self, errors: list[dict[str, Any]]) -> None:
        super().__init__("invalid arguments")
        self.errors = errors


def _validate(tool: Tool, arguments: Any) -> BaseModel:
    if not isinstance(arguments, dict):
        raise _InvalidArguments([{"loc": [], "msg": "arguments must be a JSON object"}])
    try:
        return tool.input_model.model_validate(arguments)
    except ValidationError as exc:
        errors = [
            {"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]} for e in exc.errors()
        ]
        raise _InvalidArguments(errors) from None


def _record_quietly(
    session: Session,
    step: int,
    call: ToolCall,
    content: str,
    confirmation: Literal["approved", "declined"] | None,
) -> None:
    """Audit entry for a call that got no result of its own, while an interruption is
    already propagating."""
    stamp = now()
    entry = ToolCallRecord(
        step=step,
        call_id=call.id,
        tool=call.name,
        arguments=dict(call.input),
        status="interrupted",
        started_at=stamp,
        finished_at=stamp,
        confirmation=confirmation,
        summary=content[:500],
    )
    _audit_quietly(session, entry)


def _audit_quietly(session: Session, record: ToolCallRecord) -> None:
    """Keep ``record`` in memory (once) and try to write it; a failing file write must not
    hide the interruption that is already propagating."""
    if not any(r is record for r in session.tool_calls):
        session.remember(record)
    try:
        session.write_audit(record)
    except OSError:
        pass


def _interrupted(*, started: bool) -> dict[str, Any]:
    if started:
        return _error(
            "interrupted",
            "The tool call was interrupted while it was running; it may have run partly or "
            "completely. Check its effects (for a run, get_run_status) before trying again.",
            started=True,
        )
    return _error(
        "interrupted",
        "The tool call was interrupted before the tool ran (e.g. during confirmation, or "
        "an earlier call of this step was interrupted); nothing was executed.",
        started=False,
    )


def _add_user_text(session: Session, text: str) -> None:
    """Append the user's message; merge into a trailing user turn (e.g. unanswered tool
    results after a step limit) so turns keep alternating."""
    if session.messages and session.messages[-1].role == "user":
        last = session.messages[-1]
        merged = f"{last.content}\n\n{text}" if last.content else text
        session.messages[-1] = Message(role="user", content=merged, tool_results=last.tool_results)
    else:
        session.messages.append(Message(role="user", content=text))


def _error(kind: str, message: str, **details: Any) -> dict[str, Any]:
    error: dict[str, Any] = {"type": kind, "message": message}
    error.update({k: v for k, v in details.items() if v is not None})
    return {"error": error}


def _plain(value: Any) -> Any:
    """JSON-compatible copy (Pydantic models and paths become plain data)."""
    return json.loads(_serialize(value))


def _serialize(value: Any) -> str:
    return json.dumps(value, default=_default, ensure_ascii=False)


def _default(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, set | frozenset | tuple):
        return list(value)
    return str(value)
