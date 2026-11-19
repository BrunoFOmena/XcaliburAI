# Changelog

All notable changes are documented here. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versioning: [Semantic Versioning](https://semver.org).

## [Unreleased]

### Added
- Anthropic LLM provider (optional extra `xcalibur-ai[anthropic]`, official SDK `anthropic>=2.11,<3`): system prompt, tool calls and streaming in the `LLMProvider` interface, bounded retries, clear auth/limit/network errors without the key, refusal handling with server-side fallback (#7).
- `xcalibur init` (interactive or `--non-interactive`) saves the provider, model and the NAME of the key variable to `xcalibur.yaml`; `xcalibur check-llm` tests the connection (#7).
- ECHOLAS pipeline imported into `xcalibur-echolas` (`main.nf`, 5 modules, scripts, `nextflow.config`) with its MIT notice. Input directories are now the `raw_data` and `reference` params.
- `issue-link` check: every PR must close an open issue with a closing keyword (required on `main` and `homolog`).
- Nextflow adapter executes runs for real: per-run directory `runs/<run_id>/`, log and HTML report, `run-<id>.json` provenance (engine version, container, return code), `-resume`, SIGINT/SIGTERM cancellation and a clear error when `nextflow` is missing (#5).
- Versioning rules (`docs/VERSIONING.md`) and a test that enforces version consistency.
- Issue template following the project standard, and the issue title-prefix convention in `CONTRIBUTING.md`.

### Changed
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
