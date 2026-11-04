#!/usr/bin/env bash
# Exercise the real ignore rules without relying on tracked files or user Git config.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TEST_REPO="$(mktemp -d)"
trap 'rm -rf "$TEST_REPO"' EXIT
git -c init.defaultBranch=test init --quiet "$TEST_REPO"
cp "$ROOT/.gitignore" "$TEST_REPO/.gitignore"

for path in .env .env.local config/.env.production service.env config/service.env \
  owner.runner config/owner.runner .env.example.runner; do
  if ! git -C "$TEST_REPO" -c core.excludesFile=/dev/null \
    check-ignore --no-index --quiet -- "$path"; then
    echo "gitignore test failed: sensitive path is not ignored: $path" >&2
    exit 1
  fi
done

for path in .env.example config/.env.example app/example.py docs/example.md; do
  if git -C "$TEST_REPO" -c core.excludesFile=/dev/null \
    check-ignore --no-index --quiet -- "$path"; then
    echo "gitignore test failed: safe path is ignored: $path" >&2
    exit 1
  fi
done

echo "gitignore: sensitive files ignored; example files remain trackable"
