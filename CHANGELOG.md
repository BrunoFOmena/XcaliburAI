# Changelog

All notable changes are documented here. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versioning: [Semantic Versioning](https://semver.org).

## [Unreleased]

### Added
- `xcalibur chat` and `xcalibur run` (#10). `chat` talks to the agent runtime: it shows the full plan (domain, parameters, inputs, run directory, command) and asks before running (`--yes` skips the question), has the session commands `/help`, `/status`, `/runs` and `/exit`, survives Ctrl+C during an answer, records tool calls in `<workspace>/sessions/<id>/audit.jsonl` and prints one JSON object per answer with `--json`. `run` validates and runs a domain without the LLM, streams the workflow log to stderr while it runs, and with `--json` prints `run_id`, status and paths; `--dry-run` is the same as `plan`. Exit codes: 0 success, 1 execution failure, 2 wrong usage or invalid parameters, 130 interrupted. Terminal colour follows the project's red palette and is off with `--no-color`, `NO_COLOR` or when output is not a terminal. The CLI stays on `argparse` (no new dependency).
- Agent runtime (#8): `AgentLoop` with tool calling, a step limit with a stated stop reason, tool errors returned to the model as structured results, long results truncated and saved to a file, and every tool call recorded in the session (`audit.jsonl` when the session has a directory). Typed tools (`Tool`, Pydantic input models that reject unknown arguments) and a `ToolRegistry`; core tools `list_domains`, `describe_domain`, `plan_run`, `run_pipeline`, `get_run_status` and `read_results`; domains contribute tools through `DomainPlugin.tools`. `run_pipeline` runs only after the confirmation policy approves the exact command (denied by default), and refuses if the command changed after approval. An interrupted tool call still gets a result, so the history stays valid. `LLMRouter` with fallback to the `KeywordRouter`. `FakeProvider` for tests without network. Contract documented in `docs/AGENT_RUNTIME.md`.
- Anthropic LLM provider (optional extra `xcalibur-ai[anthropic]`, official SDK `anthropic>=2.11,<3`): system prompt, tool calls and streaming in the `LLMProvider` interface, bounded retries, clear auth/limit/network errors without the key, refusal handling with server-side fallback (#7).
- `xcalibur init` (interactive or `--non-interactive`) saves the provider, model and the NAME of the key variable to `xcalibur.yaml`; `xcalibur check-llm` tests the connection (#7).
- ECHOLAS pipeline imported into `xcalibur-echolas` (`main.nf`, 5 modules, scripts, `nextflow.config`) with its MIT notice. Input directories are now the `raw_data` and `reference` params.
- `issue-link` check: every PR must close an open issue with a closing keyword (required on `main` and `homolog`).
- Nextflow adapter executes runs for real: per-run directory `runs/<run_id>/`, log and HTML report, `run-<id>.json` provenance (engine version, container, return code), `-resume`, SIGINT/SIGTERM cancellation and a clear error when `nextflow` is missing (#5).
- Versioning rules (`docs/VERSIONING.md`) and a test that enforces version consistency.
- Issue template following the project standard, and the issue title-prefix convention in `CONTRIBUTING.md`.

### Changed
- `xcalibur plan` shares the validation of `run` (inputs resolved against `--workspace`) and gains `--json`, `--run-id`, `--workspace` and `--nextflow`; a malformed `KEY=VALUE` now exits with code 2 and the CLI never prints a traceback (#10).
- `Message` carries tool calls (assistant) and tool results (user), sent to Anthropic as `tool_use`/`tool_result` blocks; `AgentSpec` gains `instructions` and `tools`; `RunRecord.filename_for` is public (#8).
- Every PR (work and promotion) is merged with a merge commit; squash and rebase merges are no longer used (#35).
- Promotion flow: `main` now receives a PR directly from `homolog`, merged with a merge commit; the `promote/*` branches and the cherry-pick `-x` requirement are gone. The `branch-policy` check enforces it (#26).
- `run_id` is validated (`^[A-Za-z0-9_-]+$`) in `RunSpec`, `RunRecord` and `Workspace.run_dir`; invalid ids raise `ValueError` before touching the disk (#21).
- Each attempt of a run keeps its own provenance record (`run-<id>.json`, then `run-<id>.attempt-<n>.json`, with an `attempt` field); the previous HTML report is removed only when a resume actually starts; Nextflow no longer inherits stdin; `inputs` records absolute paths (#21).
- Governance: the CTO (@BrunoFOmena) is the sole code owner; the CEO (@MCamposDA) needs the CTO's approval. README, CONTRIBUTING and CODEOWNERS updated.

### Removed
- Placeholder YAML issue forms, replaced by the standard issue template.

## [0.0.0] - 2026-11-19

### Added
- Repository foundation: AGPL-3.0-or-later license, `main`/`homolog` branch model, mandatory pull requests, CODEOWNERS for both cofounders, CI and branch-policy workflows.
- Core skeleton: domain manifest and parameter validation, entry-point registry, supervisor with keyword router, execution layer with a Nextflow adapter (dry run), provenance records, workspace layout, and a minimal CLI (`version`, `domains`, `plan`).
- Domain package skeleton for ECHOLAS (manifest and agent declarations; pipeline import pending).
- Documentation: README, architecture, domain-package guide, contributing guide, security policy.
