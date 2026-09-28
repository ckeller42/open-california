#!/usr/bin/env bash
# Local mirror of most of CI (.github/workflows/ci.yml). Run before pushing — GitHub Actions
# may be gated (billing/spending limit), so this is the authoritative LOCAL gate.
#
# `tools/ci.sh` (= `ci`) runs: ruff (ci.yml `lint`), the web-UI tsc/node check (`lint`), the whole
# pytest suite on ONE local python (`test` runs it on 3.11/3.12/3.13), the signal audit, the
# screens.json freshness check and the import-clean guard (`test`), and the vendor-FILE check
# (first half of `no-vendor-material`). The pytest suite also covers `codec-parity` (C header +
# vector freshness always; the C parity tests only if a C compiler is present), the Bumble pairing
# harness (tests/test_pairing_link.py, if bumble is installed — requirements-dev pins it) and
# `gui-e2e` (tests/e2e, if Playwright + Chromium are installed; otherwise they SKIP), and the pure-C
# firmware tests (tests/firmware minus the `linux_only` NimBLE host e2e; `tools/ci.sh firmware` runs
# those = ci.yml `firmware-host-e2e`).
# Only GitHub runs: the committed-MAC/VIN git-grep over the whole tree (`no-vendor-material`; the
# pre-commit hook checks only the staged diff), `install-script` (sh -n + shellcheck install.sh),
# `firmware-build` + `firmware-qemu` (ESP-IDF container), `pairing-real-stack` (real BlueZ in a VM, tests/realstack/vm.sh; not a required check), and the
# push-to-main workflows: docs.yml (sphinx -W build + Pages deploy — never on a PR, so a -W failure
# first shows after merge; build locally with `sh docs/build_site.sh`) and screenshots.yml
# (re-renders docs/screenshots and commits them to main with [skip ci]).
#
#   tools/ci.sh              # run the local gate (see above for what it does NOT cover)
#   tools/ci.sh test|firmware|lint|webcheck|typecheck|audit|web-fresh|screenshots|import-clean|vendor-check
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
    # --dist loadgroup: the e2e module is one xdist_group (shared daemon + browser) -> single worker.
    # Tests marked linux_only (the NimBLE host e2e + bond store) need a 32-bit toolchain + a NimBLE
    # build — their own job (firmware-host-e2e / `tools/ci.sh firmware`); the rest of tests/firmware
    # (pure-C SM parity, runner and session fakes) runs here too, on any host with a C compiler.
    "$PY" -m pytest tests/ -q -n auto --dist loadgroup -m "not linux_only"
  else
    "$PY" -m pytest tests/ -q -m "not linux_only"
  fi
}
firmware() {   # CI's firmware-host-e2e job: NimBLE Linux host over TCP HCI to the Bumble fake unit.
               # Linux + gcc-multilib/g++-multilib (or CROSS_COMPILE=i686-linux-gnu-); the linux_only
               # host tier (test_host_e2e.py + test_ble_store_kv.py; see tests/firmware/conftest.py
               # _skip_reason()) SKIPS -- not errors -- off that environment, so a bare pytest run
               # exits 0 even though the host e2e it's presented as never ran. Fail loud instead: this
               # is a targeted check, not the general suite (which filters `-m "not linux_only"` and
               # keeps the quiet skip in test_suite() above).
  local f
  for f in tests/firmware/test_host_e2e.py tests/firmware/test_ble_store_kv.py; do
    [ -f "$f" ] || { echo "firmware: expected test file missing: $f" >&2; exit 1; }
  done
  local out status
  out=$("$PY" -m pytest tests/firmware -v -rs 2>&1)
  status=$?
  printf '%s\n' "$out"
  [ $status -eq 0 ] || exit $status
  # test_qemu_boot.py is a separate tier (gated on CALI_QEMU=1, its own CI job firmware-qemu) and
  # stays a quiet skip here; only the two linux_only host-tier files count as "not validated".
  local skip_lines reason
  skip_lines=$(printf '%s\n' "$out" \
    | grep -E '^SKIPPED \[[0-9]+\] tests/firmware/(test_host_e2e|test_ble_store_kv)\.py')
  if [ -n "$skip_lines" ]; then
    reason=$(printf '%s\n' "$skip_lines" | sed -E 's/^SKIPPED \[[0-9]+\] [^:]+(:[0-9]+)?: //' | sort -u | paste -sd '; ' -)
    echo "firmware: host tier NOT validated: $reason" >&2
    exit 1
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
webcheck() {   # hard gate: the web UI is un-built JS, so this is its only static check. jsconfig.json
               # has checkJs on; `tsc` catches undeclared identifiers ("Cannot find name") that
               # `node --check` (parse only) cannot — one of those once shipped to buspi. Needs node.
  command -v node >/dev/null || { echo "webcheck: node not found (install Node 22+)"; exit 1; }
  npx --yes -p typescript tsc --noEmit -p calictl/webui/jsconfig.json
  node --check calictl/webui/app.js && node --check calictl/webui/strings.de.js
  echo "web UI typecheck: OK"
}
dev() {
  "$PY" -m pip install -r requirements-dev.txt
  git config core.hooksPath .githooks
  echo "dev tooling installed; pre-commit hook active (.githooks/pre-commit)"
}

case "${1:-ci}" in
  ci)            lint; webcheck; test_suite; audit; web_fresh; import_clean; vendor_check; echo "local CI: OK";;
  test)          test_suite;;
  firmware)      firmware;;
  lint)          lint;;
  webcheck)      webcheck;;
  typecheck)     typecheck;;
  audit)         audit;;
  web-fresh)     web_fresh;;
  screenshots)   screenshots;;
  import-clean)  import_clean;;
  vendor-check)  vendor_check;;
  dev)           dev;;
  *) echo "usage: tools/ci.sh [ci|test|firmware|lint|webcheck|typecheck|audit|web-fresh|screenshots|import-clean|vendor-check|dev]"; exit 2;;
esac
