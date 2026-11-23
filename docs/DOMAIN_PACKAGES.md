# Domain packages

A domain is a Python package that adds one area of genomics to XcaliburAI without touching the core.

## Layout

```
packages/xcalibur-<domain>/
├── pyproject.toml                 # declares the entry point
└── src/xcalibur_<domain>/
    ├── __init__.py                # exposes plugin() -> DomainPlugin
    ├── manifest.yaml              # the contract (see below)
    └── workflow/                  # the Nextflow pipeline
```

## Registration

```toml
[project.entry-points."xcalibur.domains"]
echolas = "xcalibur_echolas:plugin"
```

`plugin()` returns a `DomainPlugin(manifest, root, agents, tools, validate_inputs)`. `tools` is optional: domain tools follow the contract in [AGENT_RUNTIME.md](AGENT_RUNTIME.md) and agents see them as `<domain>__<tool>`.

## Input validation hook

`validate_inputs` is optional. It is a function that receives the resolved inputs (`name -> absolute Path`, exactly as the user gave them) and returns an `xcalibur.inputs.InputReport`:

- `errors` and `warnings`: lists of `InputIssue(code, message, input, path, sample)`. The message names the file or sample and says how to fix it.
- `summary`: what was detected (for ECHOLAS: samples, read layout, sizes, reference).
- `overview`: the summary in one line.

The core calls it in `plan_run`, `preview_run` and `run_pipeline` (so in `xcalibur plan`, `xcalibur run` and the chat) after the manifest checks. Any error stops the plan or the run with `invalid_inputs` (exit code 2 in the CLI) and the issues as structured details; nothing runs and nothing is written. Without errors, the plan carries the report as `input_check`, and the CLI prints the overview and the warnings to stderr.

A validator must be pure: it reads names and metadata only (at most a few header bytes), never whole files, and never writes. The hook lives in `plugin()` and not in `manifest.yaml` because it is code, like `tools`. See [`xcalibur_echolas/validation.py`](../packages/xcalibur-echolas/src/xcalibur_echolas/validation.py).

## Manifest (schema_version 1)

| Field | Meaning |
|---|---|
| `name`, `display_name`, `version`, `description` | Identity |
| `keywords` | Terms the router uses to pick this domain |
| `workflow` | `engine` (nextflow), `entrypoint` (relative to the package), `profile`, `container` |
| `inputs[]` | `name`, `kind`, `pattern`, `required`, `multiple` |
| `params[]` | `name`, `type` (string, integer, number, boolean, path, enum), `default`, `required`, `choices`, `minimum`, `maximum` |
| `outputs[]` | `name`, `kind`, `pattern` (glob relative to the run dir) |

Validation rules enforced by the core: unknown params are rejected; types are coerced strictly; enum choices and numeric ranges are checked; duplicate names are errors; an unsupported `schema_version` is an error.

## Checklist for a new domain

1. Write `manifest.yaml` with safe defaults for every parameter.
2. Provide the workflow and a container/environment recipe.
3. Declare agents by role (execution, interpretation, ...): `AgentSpec(name, role, description, instructions, tools)`. Keep the instructions in files next to the code (ECHOLAS: `prompts/*.md`, shipped as package data) and list only the tools each agent needs. Tools that read results should be read-only, take a `run_id` (never a free path), stay inside the run directory and set `grounded=True`, so the numbers an answer gives must come from them ([AGENT_RUNTIME.md](AGENT_RUNTIME.md#grounded-numbers-agentsgroundingpy)).
4. Add tests: the manifest loads and `resolve_params({})` returns the expected defaults.
5. If wrong inputs could make the workflow fail late or produce wrong results silently, add a `validate_inputs` hook that mirrors how the workflow reads them.
6. Open a PR against `homolog`.

See [`packages/xcalibur-echolas`](../packages/xcalibur-echolas) for a reference.
