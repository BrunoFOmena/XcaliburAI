# Contributing to XcaliburAI

## Branch model

```
work branch ──PR──▶ homolog ──(merge + test)──▶ PR ──▶ main
```

| Branch | Purpose | Receives PRs from |
|---|---|---|
| `main` | Stable, released line | **Only `homolog`** |
| `homolog` | Integration and testing | Every work branch (`feat/*`, `fix/*`, `docs/*`, `chore/*`) |

### Flow, step by step

1. **PR to `homolog`.** Cut a work branch from `homolog` and open the PR against `homolog`. Get it **approved**, then **merge with a merge commit**.
2. **Test on `homolog`.** The change is tested there (CI plus manual checks). Only when the test result is **approved** may it go to `main`.
3. **PR to `main`.** Open a PR from `homolog` into `main` (`gh pr create --base main --head homolog`). It carries **everything** that is in `homolog`, so confirm beforehand that all of it was tested and approved, and list `Closes #N` for every issue it carries.
4. **Merge with a merge commit.** After approval, merge using a *merge commit*, so `main` and `homolog` keep a shared history and the next promotion has no conflicts.

**Every PR is merged with a merge commit** (work PRs into `homolog` and promotion PRs into `main`). Never use *squash* or *rebase* merge.

### Rules enforced by CI (`branch-policy`)

- PRs into `main` must come from the `homolog` branch of this repository, and must carry at least one new commit.
- `main` and `promote/*` can never be merged into `homolog`. The only exception was a one-time sync (PR #29, `git merge -s ours`, no content change) that realigned the history left by the old cherry-pick flow.
- Never push directly to `main` or `homolog`; both are protected.
- Never commit directly to `main` (not even hotfixes): everything goes through `homolog` first, so the next promotion has no conflicts.
- Branches are not deleted automatically; `main` and `homolog` are never deleted.

## Every PR closes an issue

**Every pull request must reference an open issue with a closing keyword** in its description (`Closes #12`, `Fixes #12` or `Resolves #12`). The `issue-link` check fails the PR otherwise, and it is required on `main` and `homolog`.

- The issue must exist, be an issue (not a PR) and be open.
- GitHub closes the issue automatically only when the change reaches the default branch (`main`). So the issue stays open while the work is in `homolog`, and the **promotion PR into `main` must list `Closes #N` for every issue it carries**.
- Dependabot PRs are exempt.
- Need a task with no issue yet? Open the issue first (use the [issue template](.github/ISSUE_TEMPLATE/issue.md)), then the PR.

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

### End-to-end test

`tests/e2e/` runs the whole ECHOLAS pipeline on a tiny synthetic dataset (6 samples, 3 contigs,
7.5 kb, known SNPs and indels) and checks the final VCF, the PCA and the tree. It needs
Nextflow, Java and micromamba, downloads the conda environments the first time and is not part
of `pytest`/`make check`:

```bash
export NXF_CONDA_CACHEDIR=~/.cache/xcalibur-e2e/conda   # keeps the environments between runs
make e2e                                               # = pytest -m e2e -v
```

The pipeline runs with the `test` profile (`nextflow.config`), which only caps CPU, memory and
time so it fits a CI runner; it is a test profile, not for real data. The data never goes to the
repository: `tests/data/make_synthetic.py` writes it with a fixed seed (byte-identical on every
run) and `tests/data/synthetic_truth.tsv` lists the inserted variants. The `e2e` CI job runs after
`lint` and `test`, is skipped for PRs into `homolog` that only change documentation, always runs
for `homolog` → `main`, and uploads the run directory when it fails.

## Originality

XcaliburAI is an independent implementation. Do not copy code from other projects (including Lobster AI or nf-core) into this repository. Architectural ideas are fine; code is not. If you adapt anything, say so in the PR and check the license first.
