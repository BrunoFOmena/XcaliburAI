#!/usr/bin/env bash
# Validates a promotion PR into main.
#   - head branch must be `homolog` in this repository (checked by the workflow)
#   - there must be something to promote (commits on homolog that main does not have)
# Usage: check-promotion.sh <base_sha> <head_sha> <head_ref>
set -euo pipefail

BASE_SHA="$1"; HEAD_SHA="$2"; HEAD_REF="$3"

if [ "$HEAD_REF" != "homolog" ]; then
  echo "::error::PRs into 'main' must come from the 'homolog' branch (got '$HEAD_REF')."; exit 1
fi

count=$(git rev-list --count "$BASE_SHA..$HEAD_SHA")
if [ "$count" -eq 0 ]; then
  echo "::error::Nothing to promote: 'main' already contains everything in 'homolog'."; exit 1
fi

echo "ok: promoting $count commit(s) from homolog"
