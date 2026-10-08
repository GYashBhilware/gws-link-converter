#!/usr/bin/env bash
# Build a customer-shareable zip from a git tag/branch (no deploy.env, no venv, no secrets).
#   ./package.sh [ref]     # default: HEAD  -> dist/gws-link-converter-<ref>.zip
set -euo pipefail
cd "$(dirname "$0")"
REF=${1:-HEAD}
NAME=$(git describe --tags --always "$REF" 2>/dev/null || echo "$REF")
mkdir -p dist
OUT="dist/gws-link-converter-${NAME}.zip"
git archive --format=zip --prefix=gws-link-converter/ -o "$OUT" "$REF"
echo "$OUT"
unzip -l "$OUT" | tail -1
