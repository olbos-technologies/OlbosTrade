#!/bin/bash
# Remove regenerable caches so a full disk does not stop the desk.
#
#   scripts/clean_caches.sh            dry run: list what WOULD be removed, remove nothing
#   scripts/clean_caches.sh --apply    remove it
#
# Allowlist, not a pattern hunt. Only things that regenerate on their own:
#   Python bytecode and tool caches   __pycache__, .pytest_cache, .mypy_cache, .ruff_cache (backend/)
#   Vite's dependency cache           frontend/node_modules/.vite
#   Build and test output             frontend/dist, frontend/test-results, frontend/playwright-report
#
# Never touched, by design:
#   backend/ml/model_registry   trained models: NOT in git, not recoverable if deleted
#   .env, .env.*                credentials and configuration
#   node_modules, .venv, venv   reinstalling needs network and time (only node_modules/.vite goes)
#   logs/                       start.sh already truncates the two live logs on each start
#   databases and exported research data
#
# Safe to run while the app is stopped (start.sh runs it after stopping the old processes).
# Written for both macOS and Linux: POSIX find/du/rm only.

set -u

PROJECT="$(cd "$(dirname "$0")/.." && pwd)"
APPLY=0
case "${1:-}" in
  "")        ;;
  --apply)   APPLY=1 ;;
  *)         echo "usage: $0 [--apply]" >&2; exit 64 ;;
esac

# A wrong path must never turn this into `rm -rf` somewhere else.
if [ ! -f "$PROJECT/backend/app/main.py" ] || [ ! -f "$PROJECT/frontend/package.json" ]; then
  echo "refusing: $PROJECT does not look like an OlbosTrade checkout" >&2
  exit 2
fi

TOTAL_KB=0
COUNT=0

remove() {
  # $1 is a path found by the allowlist below. Symlinks are skipped, never followed.
  local p="$1"
  [ -n "$p" ] && [ -d "$p" ] && [ ! -L "$p" ] || return 0
  local kb
  kb=$(du -sk "$p" 2>/dev/null | cut -f1)
  kb=${kb:-0}
  if [ "$APPLY" = "1" ]; then
    rm -rf "$p" && echo "removed  ${kb} KB  ${p#"$PROJECT"/}" || echo "FAILED   ${p#"$PROJECT"/}" >&2
  else
    echo "would remove  ${kb} KB  ${p#"$PROJECT"/}"
  fi
  TOTAL_KB=$((TOTAL_KB + kb))
  COUNT=$((COUNT + 1))
}

# Python caches under backend/, skipping anything that must not be entered.
while IFS= read -r -d '' dir; do
  remove "$dir"
done < <(find "$PROJECT/backend" \
           \( -name node_modules -o -name .venv -o -name venv -o -name model_registry \) -prune -o \
           -type d \( -name __pycache__ -o -name .pytest_cache -o -name .mypy_cache -o -name .ruff_cache \) \
           -prune -print0 2>/dev/null)

for rel in frontend/node_modules/.vite frontend/dist frontend/test-results frontend/playwright-report; do
  remove "$PROJECT/$rel"
done

MB=$((TOTAL_KB / 1024))
if [ "$APPLY" = "1" ]; then
  echo "Freed ${MB} MB in ${COUNT} cache folder(s)"
else
  echo "Dry run: ${MB} MB in ${COUNT} cache folder(s) would be freed (re-run with --apply)"
fi
