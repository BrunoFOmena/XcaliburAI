#!/usr/bin/env bash
# Validates a PR into homolog: `main` must never enter `homolog`, neither by
# name (head `main` or `promote/*`) nor through an intermediate branch that
# merged `main` (for example `git merge origin/main` on a work branch).
#
# A PR is rejected if it brings any commit that is in `main` but not in
# `homolog` (after each promotion, that is at least the promotion merge commit).
#
# Usage: check-homolog-pr.sh <base_sha> <head_sha> <head_ref> <main_ref>
set -euo pipefail

BASE_SHA="$1"; HEAD_SHA="$2"; HEAD_REF="$3"; MAIN_REF="$4"

case "$HEAD_REF" in
  main|promote/*)
    echo "::error::'$HEAD_REF' must never be merged into 'homolog'."; exit 1 ;;
esac

main_sha=$(git rev-parse --verify --quiet "$MAIN_REF^{commit}") || {
  echo "::error::Cannot resolve '$MAIN_REF'; fetch 'main' before running this check."; exit 1
}

# Commits the PR brings, and commits of main that homolog does not have.
pr_commits=$(git rev-list "$HEAD_SHA" "^$BASE_SHA" | sort)
main_only=$(git rev-list "$main_sha" "^$BASE_SHA" | sort)
found=$(comm -12 <(printf '%s\n' "$pr_commits") <(printf '%s\n' "$main_only") | sed '/^$/d')

if [ -n "$found" ]; then
  n=$(printf '%s\n' "$found" | wc -l | tr -d ' ')
  echo "::error::This PR brings $n commit(s) from 'main' into 'homolog'; 'main' must never be merged into 'homolog'. Rebuild the branch from 'homolog' without merging 'main'."
  # Newest first, in the order main lists them.
  for c in $(git rev-list "$main_sha" "^$BASE_SHA"); do
    if printf '%s\n' "$found" | grep -qx "$c"; then
      echo "  commit from main: $(git log -1 --format='%h %s' "$c")"
    fi
  done
  exit 1
fi

echo "ok: no commit from 'main' in this PR"
