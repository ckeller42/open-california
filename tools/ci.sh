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
PY=${PY:-python3}
cd "$(dirname "$0")/.."

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
lint()      { "$PY" -m ruff check . || true; }   # NON-blocking until the codebase is ruff-green
typecheck() { "$PY" -m mypy calictl || true; }   # best-effort (None-safety / bad returns)
dev() {
  "$PY" -m pip install -r requirements-dev.txt
  git config core.hooksPath .githooks
  echo "dev tooling installed; pre-commit hook active (.githooks/pre-commit)"
}

case "${1:-ci}" in
  ci)            test_suite; audit; web_fresh; import_clean; vendor_check; echo "local CI: OK";;
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
