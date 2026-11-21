# Architecture

XcaliburAI is a **small core** plus **domain packages**. The core is domain-agnostic; every piece of domain knowledge lives in a package that registers itself.

## Request flow

```
user message
   │
   ▼
Supervisor ──(Router: LLM, keyword fallback)──▶ chooses a domain
   │
   ▼
Agent loop (LLM + typed tools) ──▶ proposes tool calls with parameters
   │
   ▼
Tool argument validation (Pydantic) + user confirmation for run_pipeline
   │
   ▼
Manifest validator ──▶ rejects unknown/out-of-range values, applies safe defaults
   │
   ▼
Execution layer (Nextflow adapter) ──▶ runs the workflow
   │
   ▼
Provenance record  +  Interpretation agents ──▶ report
```

## Core modules (`src/xcalibur`)

| Module | Responsibility |
|---|---|
| `manifest.py` | Pydantic models for a domain manifest; `resolve_params` validates and applies defaults |
| `registry.py` | `DomainPlugin`, `DomainRegistry`; discovery via the `xcalibur.domains` entry-point group |
| `agents/base.py` | `AgentSpec`, request/response types, the `Agent` protocol |
| `agents/supervisor.py` | `Supervisor` + `Router` protocol; `LLMRouter` (one structured tool call) with fallback to the deterministic `KeywordRouter` |
| `agents/tools.py` | `Tool` (name, description, Pydantic input model, function), `ToolRegistry`, `ToolContext`, `ToolError` |
| `agents/core_tools.py` | Core tools (`list_domains`, `describe_domain`, `plan_run`, `run_pipeline`, `get_run_status`, `read_results`) and `build_tool_registry` (core + domain tools) |
| `agents/runtime.py` | `AgentLoop` (tool calls, step limit, structured errors, result truncation, confirmation) and `LLMAgent` |
| `agents/confirmation.py` | Confirmation policies (`DenyAll` default, `AllowAll`, `CallbackConfirmation`) |
| `agents/session.py` | `Session`: conversation history and the audit trail of tool calls |
| `execution/base.py` | Engine-neutral `RunSpec`, `RunResult`, `WorkflowEngine` protocol |
| `execution/nextflow.py` | Nextflow adapter; builds commands, supports dry runs |
| `llm/base.py` | Provider-agnostic `LLMProvider` (`complete`, `generate` with tool calls, `stream_text`), `Message` with tool calls/results, `ProviderConfig` (keys come from env vars) |
| `llm/fake.py` | `FakeProvider`: scripted replies for tests and offline development |
| `llm/anthropic_provider.py` | Anthropic provider on the official SDK (`xcalibur-ai[anthropic]`): system prompt, tools, streaming, bounded retries, clear errors, server-side refusal fallback |
| `config.py` | `xcalibur.yaml` (user or workspace): provider, model and the NAME of the key variable; never the key |
| `provenance.py` | `RunRecord`: versions, params, command, status |
| `workspace.py` | Local on-disk layout (`data/`, `runs/`, `reports/`) |
| `cli.py` | `version`, `domains`, `plan` (more with the agent layer) |

## Design rules

1. **The core never imports a domain by name.** Domains are discovered through entry points.
2. **Agents do not build shell commands.** They call typed tools; the tool's input model and the manifest validate the arguments and the engine adapter builds the command. Model text is never executed.
3. **Unknown parameters are errors,** not warnings.
4. **Everything that runs is recorded** (`RunRecord`).
5. **Local-first.** Data stays in the workspace; the LLM provider is the user's choice and keys are never written to config.
6. **Routing is a seam.** A keyword router keeps the system testable without any LLM and is the LLM router's fallback.
7. **Acting needs consent.** Tools that act (`run_pipeline`) run only after the confirmation policy approves the exact plan. See [AGENT_RUNTIME.md](AGENT_RUNTIME.md).

## Roles of domain agents

`qc`, `execution`, `interpretation`, `acquisition`, `reporting`, or `custom`. A domain contributes the subset it needs.

## Not built yet

Other LLM providers (OpenAI, Gemini, Ollama), long-term memory and session persistence (resuming a chat), shell completion, Docker/HPC/cloud execution profiles, the data-acquisition agent, and report generation.

## Prior art

The multi-agent, plugin-based pattern is also used by [Lobster AI](https://github.com/the-omics-os/lobster). XcaliburAI is an independent implementation; no code is shared.
