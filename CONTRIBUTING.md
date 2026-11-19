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

Every PR needs **one approving review** from someone other than its author, a review from a code owner (see [CODEOWNERS](.github/CODEOWNERS)), all conversations resolved, and green CI. The two cofounders have the same authority.

## Commits and versions

- Use clear, imperative commit messages (`Add manifest validator`). Conventional prefixes (`feat:`, `fix:`, `docs:`, `chore:`) are welcome.
- Versioning is [Semantic Versioning](https://semver.org). The single source of truth is `src/xcalibur/version.py`. The project starts at **0.0.0**; releases are tagged `vX.Y.Z` on `main`.
- Record user-visible changes in [CHANGELOG.md](CHANGELOG.md) under `Unreleased`.

## Development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]" -e packages/xcalibur-echolas
make check      # ruff + pytest
```

## Originality

XcaliburAI is an independent implementation. Do not copy code from other projects (including Lobster AI or nf-core) into this repository. Architectural ideas are fine; code is not. If you adapt anything, say so in the PR and check the license first.
