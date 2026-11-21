<div align="center">
  <img alt="XcaliburAI Banner" src="docs/assets/banner.svg" width="760">
</div>

<br/>

<div align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-AGPL--3.0-cc2c18?style=for-the-badge" alt="License"></a>
  <a href="CHANGELOG.md"><img src="https://img.shields.io/badge/version-0.0.0-e84d3a?style=for-the-badge" alt="Version"></a>
  <img src="https://img.shields.io/badge/status-pre--alpha-050505?style=for-the-badge&labelColor=cc2c18" alt="Status">
  <img src="https://img.shields.io/badge/python-3.11%2B-050505?style=for-the-badge&labelColor=e45c47" alt="Python">
  <a href="https://github.com/BrunoFOmena/XcaliburAI/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/BrunoFOmena/XcaliburAI/ci.yml?branch=main&style=for-the-badge&label=CI&labelColor=cc2c18&color=050505" alt="CI"></a>
</div>

<br/>

---

> **Status: pre-alpha (v0.0.0).** The core skeleton and the ECHOLAS domain contract exist; the pipeline itself and the LLM-driven agents are still being built. Nothing here is ready for production analysis.

# Quickstart

**1. Install (development):**
```bash
git clone https://github.com/BrunoFOmena/XcaliburAI.git && cd XcaliburAI
python -m venv .venv && source .venv/bin/activate
pip install -e . -e packages/xcalibur-echolas
```

**2. See what is installed:**
```bash
xcalibur version
xcalibur domains
```

**3. Plan a run (validates parameters, prints the workflow command, executes nothing):**
```bash
xcalibur plan echolas -i raw_data=./raw_data -i reference=./reference
```

**4. Configure your LLM provider (optional; needed by the upcoming agents):**
```bash
pip install -e ".[anthropic]"
export ANTHROPIC_API_KEY=...          # the key stays in the environment, never in a file
xcalibur init                         # asks provider, model and the NAME of the key variable
xcalibur check-llm                    # sends a tiny test message and reports the result
```
For scripts and CI: `xcalibur init --non-interactive --model claude-opus-5-5 --api-key-env ANTHROPIC_API_KEY`.
The configuration is saved in `~/.config/xcalibur/xcalibur.yaml` (or `<workspace>/xcalibur.yaml` with `--workspace`).
It is read from `--config PATH`, then `--workspace DIR`, then `./xcalibur.yaml` if present, then the user file.
A workspace file cannot set `base_url` nor a key variable other than the standard one (a cloned repository could otherwise send your key elsewhere, or another secret as the key).

**The goal** is to replace step 3 with a conversation:
```text
> Run the pipeline on my samples against the M2904 reference, show the population
  structure, and tell me which clade my new sample belongs to.
```

<br/>

# CLI Reference

```bash
xcalibur                                  # show help (also: xcalibur <command> -h)
xcalibur version                          # print the version
xcalibur domains                          # list installed domain packages
xcalibur init [--non-interactive]         # configure the LLM provider (no secrets saved)
xcalibur check-llm                        # test the configured provider

xcalibur chat [--workspace DIR] [--yes] [--json]
                                          # talk to the assistant; it plans, asks, runs, explains
xcalibur run <domain> -p KEY=VALUE -i NAME=PATH [--run-id ID] [--resume] [--json]
                                          # validate and run without the LLM, with provenance
xcalibur run <domain> ... --dry-run       # only plan (same as: xcalibur plan <domain> ...)
xcalibur plan <domain> -p KEY=VALUE -i NAME=PATH
                                          # validate and show the exact command; nothing runs
```

**`xcalibur chat`** opens a session with the configured LLM (`xcalibur init` first). The assistant uses tools to list domains, plan a run and read results. Before anything runs it shows the domain, every parameter, the inputs, the run directory and the exact command, and asks `Run it? [y/N]`; the default is always to ask, and only `--yes` (for automation) skips the question. Session commands: `/help`, `/status` (provider, model, workspace, what this session did), `/runs` (runs in the workspace and their status), `/exit` (or Ctrl+D). Ctrl+C stops the current answer and keeps the session. Every tool call is recorded in `<workspace>/sessions/<id>/audit.jsonl`. With `--json`, each answer is printed as one JSON object per line (prompts go to stderr). `--max-steps N` limits model calls per answer. The conversation is not saved for later resumption yet.

**`xcalibur run`** is the deterministic path for scripts and CI: no LLM, the same validation the assistant uses, and provenance in `<workspace>/runs/<run_id>/` (`run-<id>.json`, `nextflow.log`, `nextflow_report.html`). The workflow log is streamed to stderr while it runs; the result goes to stdout. `--json` prints `run_id`, `status`, `returncode`, `command`, `run_dir`, `log_path`, `record_path` and the files found for each declared output. An existing `--run-id` is refused unless `--resume` is given.

Common options: `--workspace DIR` (default: the current directory), `--nextflow PATH` (the Nextflow executable), `--no-color` (or `NO_COLOR=1`; colour is also off when the output is not a terminal).

| Exit code | Meaning |
|---|---|
| `0` | Success |
| `1` | The run failed or was cancelled, Nextflow is missing, or an unexpected error |
| `2` | Wrong usage or invalid parameters/inputs (nothing ran) |
| `130` | Interrupted (Ctrl+C) |

Errors are one line on stderr, never a traceback (`XCALIBUR_DEBUG=1` shows it).

<br/>

# 🧠 Architecture

XcaliburAI is a multi-agent system built as a **small core plus domain packages**. The core knows nothing about any specific domain; a domain is a package that plugs in through a Python entry point.

* **Supervisor:** routes each message to the right domain and agent.
* **Domain registry:** discovers installed packages through the `xcalibur.domains` entry point.
* **Manifest validator:** every domain declares its inputs, parameters, ranges, and safe defaults. Requests are validated *before* anything runs, so an agent cannot silently pass bad parameters.
* **Execution layer:** one adapter per workflow engine (Nextflow first). Agents ask to "run this package with these parameters"; they never build shell commands.
* **Provenance:** versions, parameters, and the exact command are recorded for every run.
* **Local-first:** data stays in the workspace on your machine. You bring your own LLM provider; keys are read from environment variables and never stored.

<div align="center">
  <img alt="Core Architecture" src="docs/assets/architecture-core.svg" width="88%">
</div>

<br/>

More detail in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). The architecture pattern is inspired by [Lobster AI](https://github.com/the-omics-os/lobster); XcaliburAI is an independent implementation and **shares no code** with it.

<br/>

# 🧬 Domains

| Order | Domain | Scope | Status |
|---|---|---|---|
| 1 | **ECHOLAS** | *Leishmania* population genomics from short reads | 🔴 in progress |
| 2 | Human & clinical variants | WGS/WES, annotation, prioritization | ⚪ proposed |
| 3 | Bulk RNA-seq | Quantification, differential expression, enrichment | ⚪ proposed |
| 4 | Metagenomics & microbiome | Taxonomic and functional profiling | ⚪ proposed |

### ECHOLAS (current scope)

*Evolutionary Comparison and Hierarchical Organization on Leishmania Analysis.* Adaptive QC and trimming → alignment → joint variant calling → filtering → PCA and maximum-likelihood phylogeny, plus diversity, Fst, and clade divergence. It originates from [MCamposDA/ECHOLAS](https://github.com/MCamposDA/ECHOLAS), authored by a cofounder. The pipeline is included in [`packages/xcalibur-echolas`](packages/xcalibur-echolas) under its original MIT License ([notice](packages/xcalibur-echolas/src/xcalibur_echolas/workflow/NOTICE.md)).

### Expansion principles

- **Depth before breadth:** a new domain starts only when the previous one is solid.
- **Own implementation first.** [nf-core](https://nf-co.re) pipelines may serve as a starting point for future domains through a generic adapter, but our preference is implementing pipelines and agents ourselves.
- Adding a domain means adding a package. The core does not change. See [docs/DOMAIN_PACKAGES.md](docs/DOMAIN_PACKAGES.md).

<br/>

# 🛠️ Build Your Own Domain

A domain package is a small Python package with a `manifest.yaml`, its workflow, and its agents, registered like this:

```toml
[project.entry-points."xcalibur.domains"]
echolas = "xcalibur_echolas:plugin"
```

[`packages/xcalibur-echolas`](packages/xcalibur-echolas) is the reference example.

<br/>

# FAQ

<details>
<summary><b>Why agents over workflow engines instead of agents that call libraries?</b></summary>

Genomics pipelines are heavy, containerized, and need to be reproducible. Putting the agent *in front of* a workflow engine keeps the rigor of the pipeline while removing the expertise barrier of configuring and interpreting it.
</details>

<details>
<summary><b>Which LLMs can I use?</b></summary>

The core defines a provider-agnostic interface ([`xcalibur/llm`](src/xcalibur/llm/base.py)). Concrete providers arrive with the agent layer.
</details>

<details>
<summary><b>Can the agent break my data or invent parameters?</b></summary>

Parameters are validated against the domain manifest (types, ranges, choices, unknown keys rejected) before execution, and every run writes a provenance record. Treat results as plausible-but-verify until the project leaves pre-alpha.
</details>

<details>
<summary><b>How do I contribute?</b></summary>

Read [CONTRIBUTING.md](CONTRIBUTING.md). Pull requests always target `homolog`; `main` receives a PR from `homolog`, merged with a merge commit.
</details>

<br/>

# 🌿 Branching & Versioning

```
work branch ──PR──▶ homolog ──(merge + test)──▶ PR ──▶ main
```

- `homolog` is the integration and testing branch. Every work branch opens its PR against `homolog`: approve → merge → test.
- `main` is the stable, released line. It only receives PRs from `homolog`, merged with a merge commit; CI rejects anything else.
- Pull requests are mandatory, must close an open issue (`Closes #N`), need approval from the code owner, and must pass CI. Full flow in [CONTRIBUTING.md](CONTRIBUTING.md).
- Versions follow `MAJOR.MINOR.PATCH` ([Semantic Versioning](https://semver.org)); the project starts at **0.0.0**. Rules in [docs/VERSIONING.md](docs/VERSIONING.md), history in [CHANGELOG.md](CHANGELOG.md).

<br/>

# 👥 Cofounders

XcaliburAI is co-founded by two cofounders.

| Cofounder | Role | GitHub |
|---|---|---|
| Bruno F. Omena | CTO, code owner of the repository | [@BrunoFOmena](https://github.com/BrunoFOmena) |
| MCamposDA | CEO | [@MCamposDA](https://github.com/MCamposDA) |

The CTO manages the repository and its branches and is the code owner: pull requests, including the CEO's, need the CTO's approval. See [CONTRIBUTING.md](CONTRIBUTING.md).

<br/>

# 📄 License

[GNU Affero General Public License v3.0 or later](LICENSE). The core and official domain packages are open source. If you run a modified version as a network service, the AGPL requires you to offer its source to your users.
