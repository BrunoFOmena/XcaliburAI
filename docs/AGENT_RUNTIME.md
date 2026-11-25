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
| `stop_reason` | `completed` (the model answered; an empty answer becomes a short placeholder), `max_steps` (limit reached; the text says so) or `llm_error` (the provider failed; `error` has the message) |
| `steps` | Model calls made in this turn |
| `tool_calls` | `ToolCallRecord`s of this turn |
| `ungrounded_numbers` | Numbers of the answer no grounded tool result supports (see below); empty when the check passed or did not apply |

Options:

| Option | Default | Effect |
|---|---|---|
| `system_prompt` | `DEFAULT_SYSTEM_PROMPT` | Tells the model to plan and explain before running, and to use only tools |
| `max_steps` | 8 | Maximum model calls per turn |
| `max_result_chars` | 8000 (minimum 200) | Longer tool results are cut; the full text is saved in the session directory and its path is sent instead |
| `confirmation` | `DenyAll()` | Policy for tools that act (see below) |
| `allowed_tools` | all | Subset of tool names offered to the model |

The history always stays valid for the provider: if tool execution is interrupted (Ctrl+C, a failing audit write), every unfinished call still gets an `interrupted` error result before the exception propagates, so the session can continue.

What `interrupted` guarantees: it never claims that a tool did not run when it may have.

- A call's result is kept before its audit entry is written. If only the audit write fails (e.g. `audit.jsonl` cannot be written), the model gets the tool's real result, the record stays in memory, and the exception still reaches the caller; only the calls after it get `interrupted`.
- A call interrupted after its tool function started gets `"started": true` and a message saying it may have run partly or completely (for a run, check `get_run_status` before retrying). A call interrupted before that (e.g. Ctrl+C at the confirmation prompt) or never reached gets `"started": false`: nothing was executed.
- Every such call gets its own `interrupted` audit entry, also when call ids repeat in a step; a call interrupted after its confirmation keeps it (`confirmation: "approved"`). A call that already returned keeps its real result and audit entry even when the interruption lands right after it; if the interruption cuts its line in `audit.jsonl`, the line is written again, so it may appear twice but is never missing.

`LLMAgent` wraps a loop and a session as an `Agent` (`handle(AgentRequest) -> AgentResponse`). `LLMAgent.from_spec(spec, provider, tools, context)` appends `AgentSpec.instructions` to the system prompt and offers only `AgentSpec.tools` when the spec lists them. Those are the names the model sees, so a domain tool is listed with its prefix (`echolas__count_samples`, not `count_samples`).

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
- **Input model:** a subclass of `ToolInput` (`extra="forbid"`). Its JSON schema is what the model sees; the runtime validates every call against it before the function runs. Only the top-level model is checked for `extra="forbid"`: nested models must also subclass `ToolInput` (or set `extra="forbid"`), otherwise unknown nested fields are silently dropped.
- **Function:** `func(args, context)` receives the validated model and a `ToolContext` (`domains`, `workspace`, `engine`, `session`, `approved_plan`, `extra`). It returns anything JSON-serializable; Pydantic models and paths are converted.
- **Expected failures:** raise `ToolError(message, kind=..., **details)`. The model receives `{"error": {"type": kind, "message": ..., ...details}}` and can correct itself.
- **Acting tools:** set `requires_confirmation=True` and give a `preview(args, context)` that returns the exact plan (for runs: parameters, inputs, `command`, `command_text`, `run_dir`). The runtime shows the preview to the confirmation policy and runs the function only if it approves, passing the approved preview as `context.approved_plan`. The function should rebuild what it is about to do and refuse if it differs from the approved plan (`run_pipeline` refuses with `plan_changed` when the command differs, and with `not_confirmed` when there is no approved plan).
- **Facts about data:** set `grounded=True` on tools whose results are facts (counts, statistics, support values). See *Grounded numbers* below.
- **Never** turn model text into a shell string. Build argument lists from validated values (the engine adapter does this for workflows).

### What the model receives

| Situation | Result content | `is_error` | Recorded status |
|---|---|---|---|
| Success | `{"result": ...}` | no | `ok` |
| Arguments fail the schema | `{"error": {"type": "invalid_arguments", "errors": [{loc, msg, type}]}}` | yes | `invalid_arguments` |
| Tool not offered | `{"error": {"type": "unknown_tool", "available": [...]}}` | yes | `unknown_tool` |
| `ToolError` | `{"error": {"type": <kind>, "message": ..., ...}}` | yes | `error` |
| Confirmation declined | `{"error": {"type": "confirmation_declined", "plan": {...}}}` | yes | `declined` |
| Unexpected exception, or a result that cannot be serialized (circular, nested too deep, a value whose text form fails) | `{"error": {"type": "internal_error", "message": "<Type>: <msg>"}}`; the loop continues | yes | `internal_error` |
| Interrupted (Ctrl+C, audit write failed) | `{"error": {"type": "interrupted", "started": true/false, ...}}`, then the exception propagates (see above) | yes | `interrupted` |
| Too long | `{"truncated": true, "total_chars": N, "full_result_path": ..., "preview": ...}`; the whole note, escaping included, always fits in `max_result_chars`: the preview shrinks first, then the path is given relative to the session directory (`"relative_to": "session directory"`), then left out (`null`); the audit record always keeps the full path. If the file cannot be saved, `full_result_path` is `null` and `save_error` says why | as above | as above |

Nothing runs when the arguments are invalid, the tool is unknown or the confirmation is declined.

## Grounded numbers (`agents/grounding.py`)

A model can write a convincing number that no file contains. When a turn used at least one tool with `grounded=True`, the loop checks the final answer before returning it:

1. Every number written in the answer (not identifiers like `PC1` or `SA2`, nor list markers) must match a number in that turn's successful grounded results, or in the user's message, at the precision shown: `78,2%` and `78%` match `78.18`. Decimal commas and thousands separators are understood.
2. If some do not match, the model gets one message, `[grounding check] ...`, naming them, and one more step to rewrite the answer (this uses one of `max_steps`).
3. If the rewritten answer still has untraced numbers, it is returned with a warning that lists them, and `LoopResult.ungrounded_numbers` holds them.

Turns that used no grounded tool are not checked. The check is a safety net, not a proof: numbers written as words, and numbers that happen to appear in a result for another reason (`95` when a result states the rule `UFBoot >= 95`), are not caught. The default system prompt also tells the model to copy every number from tool results.

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
| `plan_run` | `domain`, `params`, `inputs`, `run_id?`, `resume?`, `profile?` | Validates against the manifest and the domain's input check, and returns the exact command (plus `input_check` when the domain has one); runs nothing |
| `run_pipeline` | same as `plan_run` | Needs confirmation; checks that input paths exist and that `run_id` is new (unless `resume`), then runs in the workspace |
| `get_run_status` | `run_id` | Latest attempt's status, return code, command and the last log lines |
| `read_results` | `run_id` | Files matching each output declared in the manifest, with sizes |

Relative input paths are resolved against the workspace root (the current directory for `plan_run` without a workspace). Input paths are **not** confined to the workspace: absolute paths, `~` and `..` are accepted, because data often lives elsewhere. The command stays safe (paths are made absolute, normalised so `ws/../fastq` becomes `fastq`, and passed as separate arguments) and the confirmation shows every input path; checks specific to a domain's data belong to the domain package, through the `validate_inputs` hook ([DOMAIN_PACKAGES.md](DOMAIN_PACKAGES.md#input-validation-hook)); its errors come back as `invalid_inputs` with an `errors` list the model can act on. Normalisation is textual (`os.path.normpath`): symlinks are not followed, so the command and the run record keep the name the user gave, and `link/..` means the folder holding `link`. Parameters go through `DomainManifest.resolve_params` (unknown or out-of-range values are errors).

## Domain tools and agents

A domain package contributes tools through `DomainPlugin(tools=(...))` and agents through `DomainPlugin(agents=(AgentSpec(..., instructions=..., tools=(...)),))`. `build_tool_registry(domains)` registers the core tools and every domain tool as `<domain>__<tool>`; the core never imports a domain by name.

`xcalibur chat` runs one assistant with every tool. Its system prompt is the default one followed by the `instructions` of every installed domain agent, each under a heading naming the agent and its role (`chat.chat_system_prompt`). `LLMAgent.from_spec` builds a single agent with only its own instructions and tools.

The ECHOLAS package is the reference: [`xcalibur_echolas/__init__.py`](../packages/xcalibur-echolas/src/xcalibur_echolas/__init__.py) (agents), [`tools.py`](../packages/xcalibur-echolas/src/xcalibur_echolas/tools.py) (grounded result tools), [`results.py`](../packages/xcalibur-echolas/src/xcalibur_echolas/results.py) (readers) and [`prompts/`](../packages/xcalibur-echolas/src/xcalibur_echolas/prompts) (instructions, versioned as files).

## Routing (`agents/supervisor.py`)

`LLMRouter(provider)` asks the model to call a single `choose_domain` tool whose `domain` is one of the installed domains or `none`. When the model omits the confidence (or sends something that is not a number), the decision gets a neutral 0.5. If the provider fails, the model calls no tool or names an unknown domain, the decision comes from the `KeywordRouter` (or the `fallback` router given) and the reason says why.

## Audit (`agents/session.py`)

`Session` holds the message history and a `ToolCallRecord` per call: step, call id, tool, arguments as sent by the model, status, start and end times, confirmation outcome, whether the result was truncated and where it was saved, and `outcome`: for a successful call whose result is a mapping, its `run_id` and `status` (taken before truncation; the chat uses it to show a run's own status). With `Session(directory=...)`, each record is appended to `audit.jsonl` and long results go to `tool-results/<n>-step<step>-<call id>.json`, numbered across the session and created exclusively, so a file is never overwritten, not even by another session writing to the same directory at the same time. Long-term persistence of sessions is not built yet.

## Testing without network

`xcalibur.llm.fake.FakeProvider.scripted(...)` replays completions, plain strings (final answers) or exceptions in order, and records every request (`provider.calls`). `tool_call(name, call_id, **arguments)` builds a completion with one tool call. See `tests/test_agent_runtime.py`.
