"""Agent loop: the model reads the conversation, calls tools, reads results and answers.

Safety properties the loop guarantees:

* only registered tools run, and only with arguments that pass their Pydantic model;
* model text is never executed; a tool's function is the only thing that acts;
* a tool that requires confirmation runs only after the policy approves its preview;
* failures come back to the model as structured results, so it can correct itself;
* the loop stops after ``max_steps`` model calls and says why;
* every tool call is recorded in the session for audit;
* the history stays valid for the provider: every tool call gets a result, even when
  execution is interrupted, and an empty answer is never stored as an empty turn.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
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
        self._grounded_results: list[Any] = []
        """This turn's successful grounded results, as data (only their numbers count)."""

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
        the history never holds a tool call without its result."""
        results: list[ToolResult] = []
        try:
            for call in calls:
                results.append(self._execute(step, call, context, session))
        finally:
            pending = calls[len(results):]
            for call in pending:
                payload = _error(
                    "interrupted", "The tool call was interrupted and returned no result."
                )
                results.append(ToolResult(call.id, _serialize(payload), is_error=True))
            session.messages.append(Message(role="user", tool_results=tuple(results)))
            for call in pending:
                _record_quietly(session, step, call, "interrupted")

    def _execute(
        self, step: int, call: ToolCall, context: ToolContext, session: Session
    ) -> ToolResult:
        started = now()
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
                if not approved:
                    raise ToolError(
                        "The user did not confirm; nothing was executed.",
                        kind="confirmation_declined",
                        plan=plan,
                    )
                # The tool can check that what it runs is exactly what was approved.
                call_context = replace(context, approved_plan=plan)
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
        except ValueError as exc:  # e.g. a circular structure returned by a tool
            status = "internal_error"
            text = _serialize(_error("internal_error", f"Result is not serializable: {exc}"))
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
        session.record(record)
        if status == "ok" and tool is not None and tool.grounded:
            self._grounded_results.append(json.loads(text))
        return ToolResult(tool_call_id=call.id, content=content, is_error=status != "ok")

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
        request = ConfirmationRequest(
            tool=name,
            arguments=args.model_dump(mode="json"),
            summary=str(summary),
            details=plan,
        )
        return bool(self.confirmation.confirm(request)), plan

    def _bounded(
        self, step: int, call_id: str, text: str, session: Session
    ) -> tuple[str, bool, Any]:
        if len(text) <= self.max_result_chars:
            return text, False, None
        saved = session.save_result(step, call_id, text)
        keep = self.max_result_chars
        while True:
            note = _serialize(
                {
                    "truncated": True,
                    "total_chars": len(text),
                    "full_result_path": None if saved is None else str(saved),
                    "preview": text[:keep],
                }
            )
            # Escaping can make the note longer than the preview; shrink until it fits.
            excess = len(note) - self.max_result_chars
            if excess <= 0 or keep == 0:
                return note, True, saved
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


def _record_quietly(session: Session, step: int, call: ToolCall, status: ToolCallStatus) -> None:
    """Best-effort audit entry while an interruption is already propagating."""
    last = session.tool_calls[-1] if session.tool_calls else None
    if last is not None and last.step == step and last.call_id == call.id:
        return  # already recorded in memory; only writing the audit file failed
    stamp = now()
    entry = ToolCallRecord(
        step=step,
        call_id=call.id,
        tool=call.name,
        arguments=dict(call.input),
        status=status,
        started_at=stamp,
        finished_at=stamp,
    )
    try:
        session.record(entry)  # keeps the entry in memory even if the file write fails
    except OSError:
        pass


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
