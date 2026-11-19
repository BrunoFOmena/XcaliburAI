<!--
Work PRs target `homolog`.
PRs into `main` come only from `homolog`, merged with a merge commit (never squash).
-->

## Issue
<!-- Required for every PR. Use a closing keyword so the issue closes automatically. -->
Closes #

## What and why

## How was it tested

## Checklist (PR to `homolog`)
- [ ] Linked to an open issue with `Closes #N`
- [ ] Targets `homolog`
- [ ] Tests added/updated and `make check` passes
- [ ] `CHANGELOG.md` updated under `Unreleased` (user-visible changes)
- [ ] No code copied from other projects; license of anything adapted was checked
- [ ] No secrets, credentials, or genomic data committed

## Checklist (promotion PR to `main`)
- [ ] Lists `Closes #N` for every issue carried by the promoted commits
- [ ] Head is `homolog` and it will be merged with a merge commit (not squash)
- [ ] Everything currently in `homolog` was tested and approved
- [ ] Version and CHANGELOG are updated (releases only; otherwise N/A)
