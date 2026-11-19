#!/usr/bin/env bash
# Every PR must close at least one open issue of this repository via a closing keyword.
# Reads the PR body from $PR_BODY. Env: REPO (owner/name), GH (gh binary, default "gh").
set -euo pipefail

GH="${GH:-gh}"
REPO="${REPO:?REPO is required}"
BODY="${PR_BODY:-}"

# closes/closed/close, fixes/fixed/fix, resolves/resolved/resolve  +  #N
refs=$(printf '%s' "$BODY" \
  | grep -oiE '\b(close[sd]?|fix(e[sd])?|resolve[sd]?):?[[:space:]]+#[0-9]+' \
  | grep -oE '[0-9]+$' | sort -un || true)

if [ -z "$refs" ]; then
  echo "::error::This PR must reference an issue with a closing keyword, e.g. 'Closes #12', in its description."
  exit 1
fi

fail=0
for n in $refs; do
  if ! info=$("$GH" api "repos/$REPO/issues/$n" --jq '[.state, (.pull_request != null)] | @tsv' 2>/dev/null); then
    echo "::error::#$n does not exist in $REPO."; fail=1; continue
  fi
  state=$(printf '%s' "$info" | cut -f1)
  is_pr=$(printf '%s' "$info" | cut -f2)
  if [ "$is_pr" = "true" ]; then
    echo "::error::#$n is a pull request, not an issue."; fail=1; continue
  fi
  if [ "$state" != "open" ]; then
    echo "::error::Issue #$n is not open."; fail=1; continue
  fi
  echo "ok: closes issue #$n"
done
exit $fail
