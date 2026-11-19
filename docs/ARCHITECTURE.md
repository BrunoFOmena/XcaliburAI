# Architecture

XcaliburAI is a **small core** plus **domain packages**. The core is domain-agnostic; every piece of domain knowledge lives in a package that registers itself.

## Request flow

```
user message
   │
   ▼
Supervisor ──(Router)──▶ chooses a domain
   │
   ▼
Domain agents ──▶ propose parameters
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
| `agents/supervisor.py` | `Supervisor` + `Router` protocol; deterministic `KeywordRouter` first, LLM router later |
| `execution/base.py` | Engine-neutral `RunSpec`, `RunResult`, `WorkflowEngine` protocol |
| `execution/nextflow.py` | Nextflow adapter; builds commands, supports dry runs |
| `llm/base.py` | Provider-agnostic `LLMProvider` (`complete`, `generate` with tool calls, `stream_text`), `ProviderConfig` (keys come from env vars) |
| `llm/anthropic_provider.py` | Anthropic provider on the official SDK (`xcalibur-ai[anthropic]`): system prompt, tools, streaming, bounded retries, clear errors, server-side refusal fallback |
| `config.py` | `xcalibur.yaml` (user or workspace): provider, model and the NAME of the key variable; never the key |
| `provenance.py` | `RunRecord`: versions, params, command, status |
| `workspace.py` | Local on-disk layout (`data/`, `runs/`, `reports/`) |
| `cli.py` | `version`, `domains`, `plan` (more with the agent layer) |

## Design rules

1. **The core never imports a domain by name.** Domains are discovered through entry points.
2. **Agents do not build shell commands.** They produce parameters; the manifest validates them and the engine adapter builds the command.
3. **Unknown parameters are errors,** not warnings.
4. **Everything that runs is recorded** (`RunRecord`).
5. **Local-first.** Data stays in the workspace; the LLM provider is the user's choice and keys are never written to config.
6. **Routing is a seam.** A keyword router keeps the system testable without any LLM.

## Roles of domain agents

`qc`, `execution`, `interpretation`, `acquisition`, `reporting`, or `custom`. A domain contributes the subset it needs.

## Not built yet

Other LLM providers (OpenAI, Gemini, Ollama), the agent runtime (tool calling, memory), `xcalibur chat` / `run`, Docker/HPC/cloud execution profiles, the data-acquisition agent, and report generation.

## Prior art

The multi-agent, plugin-based pattern is also used by [Lobster AI](https://github.com/the-omics-os/lobster). XcaliburAI is an independent implementation; no code is shared.
