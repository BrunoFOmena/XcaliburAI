#!/usr/bin/env bash
# Validates a promotion PR into main.
#   - head branch must be promote/* in this repository
#   - every commit must be a cherry-pick made with `git cherry-pick -x`
#     and its origin commit must already exist in origin/homolog
# Usage: check-promotion.sh <base_sha> <head_sha> <head_ref>
set -euo pipefail

BASE_SHA="$1"; HEAD_SHA="$2"; HEAD_REF="$3"
HOMOLOG_REF="${HOMOLOG_REF:-origin/homolog}"
fail=0

case "$HEAD_REF" in
  promote/*) ;;
  *) echo "::error::PRs into 'main' must come from a 'promote/*' branch of cherry-picks from 'homolog' (got '$HEAD_REF')."; exit 1 ;;
esac

commits=$(git rev-list --reverse "$BASE_SHA..$HEAD_SHA")
if [ -z "$commits" ]; then
  echo "::error::No commits to promote."; exit 1
fi

for c in $commits; do
  if [ "$(git rev-list --parents -n1 "$c" | wc -w)" -gt 2 ]; then
    echo "::error::$c is a merge commit; promote individual commits with cherry-pick."; fail=1; continue
  fi
  src=$(git log -1 --format=%B "$c" | grep -oE '\(cherry picked from commit [0-9a-f]{40}\)' | grep -oE '[0-9a-f]{40}' | head -1 || true)
  if [ -z "$src" ]; then
    echo "::error::$c has no '(cherry picked from commit ...)' line. Use 'git cherry-pick -x'."; fail=1; continue
  fi
  if ! git merge-base --is-ancestor "$src" "$HOMOLOG_REF"; then
    echo "::error::$c was cherry-picked from $src, which is not in homolog. Only tested commits can be promoted."; fail=1; continue
  fi
  echo "ok: $c <- $src (in homolog)"
done

exit $fail
