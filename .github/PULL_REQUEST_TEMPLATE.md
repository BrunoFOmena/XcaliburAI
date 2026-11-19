<!--
Work PRs target `homolog`.
PRs into `main` come only from a `promote/*` branch of `git cherry-pick -x` commits already in `homolog`.
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
- [ ] Branch is `promote/*`, cut from `main`
- [ ] Every commit was brought with `git cherry-pick -x` from `homolog`
- [ ] The change was tested in `homolog` and that result was approved
- [ ] Version and CHANGELOG are updated if this is a release
