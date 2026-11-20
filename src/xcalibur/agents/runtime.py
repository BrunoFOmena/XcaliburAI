"""Agent loop: the model reads the conversation, calls tools, reads results and answers.

Safety properties the loop guarantees:

* only registered tools run, and only with arguments that pass their Pydantic model;
* model text is never executed; a tool's function is the only thing that acts;
* a tool that requires confirmation runs only after the policy approves its preview;
* failures come back to the model as structured results, so it can correct itself;
* the loop stops after ``max_steps`` model calls and says why;
* every tool call is recorded in the session for audit.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Literal

from pydantic import BaseModel, ValidationError

from xcalibur.agents.base import AgentRequest, AgentResponse, AgentSpec
from xcalibur.agents.confirmation import ConfirmationPolicy, ConfirmationRequest, DenyAll
from xcalibur.agents.session import Session, ToolCallRecord, ToolCallStatus, now
from xcalibur.agents.tools import Tool, ToolContext, ToolError, ToolRegistry
from xcalibur.llm.base import LLMProvider, Message, ToolCall, ToolResult
from xcalibur.llm.errors import LLMError

StopReason = Literal["completed", "max_steps", "llm_error"]

DEFAULT_MAX_STEPS = 8
DEFAULT_MAX_RESULT_CHARS = 8000

DEFAULT_SYSTEM_PROMPT = """\
You are XcaliburAI, an assistant that runs and explains bioinformatics workflows.
Act only through the tools you are given; never write shell commands for the user to run.
Before running a pipeline, call plan_run, then tell the user what will run: the domain, \
every parameter, the inputs and the exact command. run_pipeline needs the user's \
confirmation; if it is declined, say so and do not retry on your own.
If a tool returns an error, read it, fix the arguments and try again, or explain the problem.
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

    def tool_names(self) -> list[str]:
        return self.tools.names() if self.allowed_tools is None else list(self.allowed_tools)

    def run(self, message: str, session: Session | None = None) -> LoopResult:
        session = session if session is not None else Session()
        context = replace(self.context, session=session)
        _add_user_text(session, message)
        specs = self.tools.specs(self.tool_names())
        first_record = len(session.tool_calls)

        last_text = ""
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
            session.messages.append(
                Message(role="assistant", content=completion.text, tool_calls=calls)
            )
            if not calls:
                return LoopResult(
                    text=completion.text,
                    stop_reason="completed",
                    steps=step,
                    tool_calls=session.tool_calls[first_record:],
                )
            results = tuple(self._execute(step, call, context, session) for call in calls)
            session.messages.append(Message(role="user", tool_results=results))

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

    # -- tool execution ---------------------------------------------------------------

    def _execute(
        self, step: int, call: ToolCall, context: ToolContext, session: Session
    ) -> ToolResult:
        started = now()
        confirmation: Literal["approved", "declined"] | None = None
        status: ToolCallStatus
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
            payload: Any = {"result": tool.func(args, context)}
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

        text = _serialize(payload)
        content, truncated, saved = self._bounded(step, call.id, text, session)
        session.record(
            ToolCallRecord(
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
        )
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
        head = text[: self.max_result_chars - 200]
        note = {
            "truncated": True,
            "total_chars": len(text),
            "full_result_path": None if saved is None else str(saved),
            "preview": head,
        }
        return _serialize(note), True, saved


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
