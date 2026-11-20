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

`plugin()` returns a `DomainPlugin(manifest, root, agents, tools)`. `tools` is optional: domain tools follow the contract in [AGENT_RUNTIME.md](AGENT_RUNTIME.md) and agents see them as `<domain>__<tool>`.

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
3. Declare agents by role (execution, interpretation, ...).
4. Add tests: the manifest loads and `resolve_params({})` returns the expected defaults.
5. Open a PR against `homolog`.

See [`packages/xcalibur-echolas`](../packages/xcalibur-echolas) for a reference.
