#!/usr/bin/env bash
# Local mirror of CI (.github/workflows/ci.yml's `test` job). Run before pushing — GitHub Actions
# may be gated (billing/spending limit), so this is the authoritative LOCAL gate.
#
#   tools/ci.sh              # run the full gate (what CI runs, minus gui-e2e)
#   tools/ci.sh test|lint|typecheck|audit|web-fresh|import-clean|vendor-check
#   tools/ci.sh dev          # install dev tooling + activate the pre-commit hook
#
# Runtime is stdlib-only; dev tools are in requirements-dev.txt.
set -euo pipefail
cd "$(dirname "$0")/.."

# Prefer a python that meets the project floor (3.11+); the bare `python3` may be an EOL system 3.9
# (as on stock macOS). Override with PY=... Prefers 3.13 to match buspi. Ordered newest-first.
if [ -z "${PY:-}" ]; then
  for _p in python3.13 python3.12 python3.11 python3; do
    if command -v "$_p" >/dev/null 2>&1 \
       && "$_p" -c 'import sys; raise SystemExit(0 if sys.version_info>=(3,11) else 1)' 2>/dev/null; then
      PY="$_p"; break
    fi
  done
  : "${PY:?need Python >= 3.11 (project floor); only found $(python3 --version 2>&1)}"
fi

test_suite() {   # parallel when pytest-xdist is present (tools/ci.sh dev), else serial
  if "$PY" -c 'import xdist' 2>/dev/null; then
    "$PY" -m pytest tests/ -q -n auto
  else
    "$PY" -m pytest tests/ -q
  fi
}
audit()        { "$PY" -m tools.audit_signals --report; }
import_clean() { "$PY" -m tools.check_import_clean; }
web_fresh() {
  "$PY" -m tools.build_web --out /tmp/oc_screens.json
  diff -q /tmp/oc_screens.json calictl/webui/screens.json \
    || { echo "screens.json stale — run: $PY -m tools.build_web"; exit 1; }
}
vendor_check() {
  local bad
  bad=$(git ls-files | grep -iE '\.(apk|xapk|dex|jar|aar|aab|cvr|pcap|pcapng|pklg|btsnoop)$|(^|/)decompile/|(^|/)manuals/|ui/assets/svg/' || true)
  [ -z "$bad" ] || { echo "vendor/binary material committed:"; echo "$bad"; exit 1; }
  echo "no vendor material committed"
}
screenshots() {   # regenerate docs/screenshots from the live UI over the mock (needs Playwright +
                  # Chromium). Local stand-in for the screenshots.yml workflow while Actions is unused.
  "$PY" -m tools.ux_gallery --out docs/screenshots
}
lint()      { "$PY" -m ruff check .; }           # hard gate — the codebase is ruff-green
typecheck() { "$PY" -m mypy calictl || true; }   # best-effort (None-safety / bad returns)
dev() {
  "$PY" -m pip install -r requirements-dev.txt
  git config core.hooksPath .githooks
  echo "dev tooling installed; pre-commit hook active (.githooks/pre-commit)"
}

case "${1:-ci}" in
  ci)            lint; test_suite; audit; web_fresh; import_clean; vendor_check; echo "local CI: OK";;
  test)          test_suite;;
  lint)          lint;;
  typecheck)     typecheck;;
  audit)         audit;;
  web-fresh)     web_fresh;;
  screenshots)   screenshots;;
  import-clean)  import_clean;;
  vendor-check)  vendor_check;;
  dev)           dev;;
  *) echo "usage: tools/ci.sh [ci|test|lint|typecheck|audit|web-fresh|screenshots|import-clean|vendor-check|dev]"; exit 2;;
esac
