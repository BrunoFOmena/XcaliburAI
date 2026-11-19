# Contributing to XcaliburAI

## Branch model

```
feature/*  ──PR──▶  homolog  ──PR──▶  main
```

| Branch | Purpose | Who can target it with a PR |
|---|---|---|
| `main` | Stable, released line | **Only `homolog`** |
| `homolog` | Integration and testing before release | Every other branch |
| `feat/*`, `fix/*`, `docs/*`, `chore/*` | Work branches, cut from `homolog` | n/a |

Rules:

- **Never open a PR against `main` from a work branch.** CI fails it (`branch-policy`).
- **Never push directly** to `main` or `homolog`. Both are protected.
- Cut work branches from `homolog`, and open the PR back to `homolog`.
- Promote by opening a PR `homolog` → `main` after testing on `homolog`.
- Branches are not deleted automatically; `main` and `homolog` are never deleted.

## Reviews

Every PR needs **one approving review** from someone other than its author, a review from a code owner (see [CODEOWNERS](.github/CODEOWNERS)), all conversations resolved, and green CI.

- The **CTO** (@BrunoFOmena) is the code owner and manages the branches. Every PR from anyone else, including the CEO, needs the CTO's approval.
- The CTO can merge without a second approval (repository administrator).
- The **CEO** (@MCamposDA) and any future contributor need the CTO's approval.

## Issues

Issues follow the template in [.github/ISSUE_TEMPLATE/issue.md](.github/ISSUE_TEMPLATE/issue.md) and carry a title prefix, a priority label (`P0`, `P1`, `P2`) and a milestone.

| Prefix | Purpose |
|---|---|
| `[FEATURE]` | New feature |
| `[BUG]` | Bug fix |
| `[REFACTOR]` | Code refactoring |
| `[DOCS]` | Documentation |
| `[TEST]` | Tests: implementation or improvement |
| `[CHORE]` | Maintenance and operational tasks |
| `[IMPROVEMENT]` | Improvement of an existing feature |
| `[SECURITY]` | Security fix or improvement |

Example: `[FEATURE] Implement user authentication via JWT`

## Commits and versions

- Use clear, imperative commit messages (`Add manifest validator`). Conventional prefixes (`feat:`, `fix:`, `docs:`, `chore:`) are welcome.
- Versioning is `MAJOR.MINOR.PATCH` ([Semantic Versioning](https://semver.org)), starting at **0.0.0**. The single source of truth is `src/xcalibur/version.py`; releases are tagged `vX.Y.Z` on `main`. Full rules in [docs/VERSIONING.md](docs/VERSIONING.md).
- Record user-visible changes in [CHANGELOG.md](CHANGELOG.md) under `Unreleased`.

## Development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]" -e packages/xcalibur-echolas
make check      # ruff + pytest
```

## Originality

XcaliburAI is an independent implementation. Do not copy code from other projects (including Lobster AI or nf-core) into this repository. Architectural ideas are fine; code is not. If you adapt anything, say so in the PR and check the license first.
