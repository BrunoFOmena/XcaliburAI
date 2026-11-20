# Agent runtime and the tool contract

The agent runtime connects an LLM, typed tools and the execution layer. The model never runs anything itself: it chooses a tool and proposes arguments; the runtime validates them, asks for confirmation when the tool acts, runs the tool's function and sends the result back.

```
user message ─▶ AgentLoop ─▶ LLMProvider.generate(history, tool specs)
                   ▲                 │
                   │                 ├─ text only ──────────────▶ final answer
                   │                 └─ tool calls
                   │                        │
                   │        validate arguments (Pydantic, unknown fields rejected)
                   │        confirmation policy (tools that act, e.g. run_pipeline)
                   │        run the tool function; record the call in the session
                   └──────── tool results (structured, truncated if long) ◀┘
```

The implementation is our own (`src/xcalibur/agents/`); no orchestration library is used.

## The loop (`agents/runtime.py`)

`AgentLoop(provider, tools, context, ...)` runs one user turn with `run(message, session)` and returns a `LoopResult`:

| Field | Meaning |
|---|---|
| `text` | The answer to show the user |
| `stop_reason` | `completed` (the model answered), `max_steps` (limit reached; the text says so) or `llm_error` (the provider failed; `error` has the message) |
| `steps` | Model calls made in this turn |
| `tool_calls` | `ToolCallRecord`s of this turn |

Options:

| Option | Default | Effect |
|---|---|---|
| `system_prompt` | `DEFAULT_SYSTEM_PROMPT` | Tells the model to plan and explain before running, and to use only tools |
| `max_steps` | 8 | Maximum model calls per turn |
| `max_result_chars` | 8000 | Longer tool results are cut; the full text is saved in the session directory and its path is sent instead |
| `confirmation` | `DenyAll()` | Policy for tools that act (see below) |
| `allowed_tools` | all | Subset of tool names offered to the model |

`LLMAgent` wraps a loop and a session as an `Agent` (`handle(AgentRequest) -> AgentResponse`). `LLMAgent.from_spec(spec, provider, tools, context)` appends `AgentSpec.instructions` to the system prompt and offers only `AgentSpec.tools` when the spec lists them.

## Defining a tool (`agents/tools.py`)

```python
from pydantic import Field
from xcalibur.agents.tools import Tool, ToolContext, ToolError, ToolInput

class CountArgs(ToolInput):          # ToolInput forbids unknown arguments
    run_id: str = Field(pattern=r"^[A-Za-z0-9_-]+$")

def count_samples(args: CountArgs, ctx: ToolContext) -> dict:
    if ctx.workspace is None:
        raise ToolError("No workspace configured.", kind="no_workspace")
    return {"samples": 12}

COUNT = Tool("count_samples", "Count the samples of a run.", CountArgs, count_samples)
```

Rules:

- **Name:** 1 to 64 letters, digits, `_` or `-`.
- **Input model:** a subclass of `ToolInput` (`extra="forbid"`). Its JSON schema is what the model sees; the runtime validates every call against it before the function runs.
- **Function:** `func(args, context)` receives the validated model and a `ToolContext` (`domains`, `workspace`, `engine`, `session`, `extra`). It returns anything JSON-serializable; Pydantic models and paths are converted.
- **Expected failures:** raise `ToolError(message, kind=..., **details)`. The model receives `{"error": {"type": kind, "message": ..., ...details}}` and can correct itself.
- **Acting tools:** set `requires_confirmation=True` and give a `preview(args, context)` that returns the exact plan (for runs: parameters, inputs, `command`, `command_text`, `run_dir`). The runtime shows the preview to the confirmation policy and runs the function only if it approves.
- **Never** turn model text into a shell string. Build argument lists from validated values (the engine adapter does this for workflows).

### What the model receives

| Situation | Result content | `is_error` | Recorded status |
|---|---|---|---|
| Success | `{"result": ...}` | no | `ok` |
| Arguments fail the schema | `{"error": {"type": "invalid_arguments", "errors": [{loc, msg, type}]}}` | yes | `invalid_arguments` |
| Tool not offered | `{"error": {"type": "unknown_tool", "available": [...]}}` | yes | `unknown_tool` |
| `ToolError` | `{"error": {"type": <kind>, "message": ..., ...}}` | yes | `error` |
| Confirmation declined | `{"error": {"type": "confirmation_declined", "plan": {...}}}` | yes | `declined` |
| Unexpected exception | `{"error": {"type": "internal_error", "message": "<Type>: <msg>"}}` | yes | `internal_error` |
| Too long | `{"truncated": true, "total_chars": N, "full_result_path": ..., "preview": ...}` | as above | as above |

Nothing runs when the arguments are invalid, the tool is unknown or the confirmation is declined.

## Confirmation (`agents/confirmation.py`)

A policy implements `confirm(ConfirmationRequest) -> bool`. The request has the tool name, the validated arguments, a one-line `summary` (the command) and the full preview in `details`.

- `DenyAll` (default): nothing that acts runs. Safe for any context without a user.
- `CallbackConfirmation(ask)`: ask the user, e.g. a prompt in `xcalibur chat` showing the command.
- `AllowAll`: explicit opt-in (a `--yes` flag, tests).

The model is also instructed to call `plan_run` and explain the plan (domain, parameters, inputs, command) before calling `run_pipeline`.

## Core tools (`agents/core_tools.py`)

| Tool | Arguments | Does |
|---|---|---|
| `list_domains` | none | Installed domains (name, display name, version, description) |
| `describe_domain` | `domain` | Inputs, parameters (types, defaults, choices, limits), outputs and agents |
| `plan_run` | `domain`, `params`, `inputs`, `run_id?`, `resume?` | Validates against the manifest and returns the exact command; runs nothing |
| `run_pipeline` | same as `plan_run` | Needs confirmation; checks that input paths exist and that `run_id` is new (unless `resume`), then runs in the workspace |
| `get_run_status` | `run_id` | Latest attempt's status, return code, command and the last log lines |
| `read_results` | `run_id` | Files matching each output declared in the manifest, with sizes |

Relative input paths are resolved against the workspace root. Parameters go through `DomainManifest.resolve_params` (unknown or out-of-range values are errors). Domain-specific input checks belong to the domain package.

## Domain tools and agents

A domain package contributes tools through `DomainPlugin(tools=(...))` and agents through `DomainPlugin(agents=(AgentSpec(..., instructions=..., tools=(...)),))`. `build_tool_registry(domains)` registers the core tools and every domain tool as `<domain>__<tool>`; the core never imports a domain by name.

## Routing (`agents/supervisor.py`)

`LLMRouter(provider)` asks the model to call a single `choose_domain` tool whose `domain` is one of the installed domains or `none`. If the provider fails, the model calls no tool or names an unknown domain, the decision comes from the `KeywordRouter` (or the `fallback` router given) and the reason says why.

## Audit (`agents/session.py`)

`Session` holds the message history and a `ToolCallRecord` per call: step, call id, tool, arguments as sent by the model, status, start and end times, confirmation outcome, whether the result was truncated and where it was saved. With `Session(directory=...)`, each record is appended to `audit.jsonl` and long results go to `tool-results/`. Long-term persistence of sessions is not built yet.

## Testing without network

`xcalibur.llm.fake.FakeProvider.scripted(...)` replays completions, plain strings (final answers) or exceptions in order, and records every request (`provider.calls`). `tool_call(name, call_id, **arguments)` builds a completion with one tool call. See `tests/test_agent_runtime.py`.
