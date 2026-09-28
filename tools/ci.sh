#!/usr/bin/env bash
# Local mirror of most of CI (.github/workflows/ci.yml). Run before pushing — GitHub Actions
# may be gated (billing/spending limit), so this is the authoritative LOCAL gate.
#
# `tools/ci.sh` (= `ci`) runs: `pre-commit run --all-files` (ci.yml `pre-commit`: ruff + ruff
# format, markdownlint, gitleaks, whitespace/YAML checks, the vendor/MAC/VIN guard over every
# tracked file, import-clean, doc-offset, screens.json freshness, codec vector/C-header freshness
# and the web-UI tsc/node check), then the whole pytest suite on ONE local python (ci.yml `test`
# runs it on 3.11/3.12/3.13) and the signal audit. The pytest suite also covers `codec-parity` (C
# header + vector freshness always; the C parity tests only if a C compiler is present), the Bumble
# pairing harness (tests/test_pairing_link.py, if bumble is installed — requirements-dev pins it)
# and `gui-e2e` (tests/e2e, if Playwright + Chromium are installed; otherwise they SKIP), and the
# pure-C firmware tests (tests/firmware minus the `linux_only` NimBLE host e2e; `tools/ci.sh
# firmware` runs those = ci.yml `firmware-host-e2e`). pre-commit only sees git-TRACKED files:
# `git add` a new file before running this, or it gets a false green.
# Only GitHub runs: `install-script` (sh -n + shellcheck install.sh), `docs` (sphinx -W site build
# on PRs; locally `sh docs/build_site.sh`), `firmware-build` + `firmware-qemu` (ESP-IDF container),
# `pairing-real-stack` (real BlueZ in a VM, tests/realstack/vm.sh; not a required check), and the
# push-to-main workflows: docs.yml (the same build + Pages deploy) and screenshots.yml
# (re-renders docs/screenshots and commits them to main with [skip ci]).
#
#   tools/ci.sh              # run the local gate (see above for what it does NOT cover)
#   tools/ci.sh test|firmware|lint|webcheck|typecheck|audit|web-fresh|codec-fresh|doc-offset|screenshots|import-clean|vendor-check
#   tools/ci.sh dev          # install dev tooling + the pre-commit/pre-push hooks (pre-commit framework)
#
# The single-check subcommands are also the entries of the repo-local hooks in
# .pre-commit-config.yaml, so the hook, this script and CI run the same command.
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
vendor_check() {   # whole tracked tree: vendor paths, the real vehicle MAC, VINs (= CI no-vendor-material)
  "$PY" tools/check_vendor_material.py --all
  echo "no vendor material, vehicle MAC, or VIN committed"
}
doc_offset() { "$PY" -m pytest tests/test_doc_offset_consistency.py -q -p no:cacheprovider; }
codec_fresh() {   # golden vectors + generated C headers match protocol/dictionary.yaml + overrides
  "$PY" -m tools.gen_codec_vectors --check \
    || { echo "golden codec vectors stale — run: $PY -m tools.gen_codec_vectors"; exit 1; }
  "$PY" -m tools.gen_c_dict --check \
    || { echo "generated C headers stale — run: $PY -m tools.gen_c_dict"; exit 1; }
}
screenshots() {   # regenerate docs/screenshots from the live UI over the mock (needs Playwright +
                  # Chromium). Local stand-in for the screenshots.yml workflow while Actions is unused.
  "$PY" -m tools.ux_gallery --out docs/screenshots
}
lint() {   # every pre-commit-stage hook over all tracked files (= ci.yml `pre-commit`)
  if command -v pre-commit >/dev/null 2>&1; then pre-commit run --all-files --show-diff-on-failure
  elif "$PY" -m pre_commit --version >/dev/null 2>&1; then "$PY" -m pre_commit run --all-files --show-diff-on-failure
  else echo "lint: pre-commit not installed — run: tools/ci.sh dev"; exit 1; fi
}
typecheck() { "$PY" -m mypy calictl || true; }   # best-effort (None-safety / bad returns)
webcheck() {   # hard gate: the web UI is un-built JS, so this is its only static check. jsconfig.json
               # has checkJs on; `tsc` catches undeclared identifiers ("Cannot find name") that
               # `node --check` (parse only) cannot — one of those once shipped to buspi. Needs node.
  command -v node >/dev/null || { echo "webcheck: node not found (install Node 22+)"; exit 1; }
  # typescript is pinned (bump deliberately; the 0-error baseline is per compiler version).
  npx --yes -p typescript@7.0.2 tsc --noEmit -p calictl/webui/jsconfig.json
  node --check calictl/webui/app.js && node --check calictl/webui/strings.de.js
  echo "web UI typecheck: OK"
}
dev() {
  "$PY" -m pip install -r requirements-dev.txt
  # The retired .githooks/ hook set core.hooksPath; pre-commit refuses to install while it is set.
  if [ "$(git config --get core.hooksPath || true)" = ".githooks" ]; then git config --unset core.hooksPath; fi
  "$PY" -m pre_commit install   # pre-commit + pre-push (default_install_hook_types)
  echo "dev tooling installed; pre-commit + pre-push hooks active (.pre-commit-config.yaml)"
}

case "${1:-ci}" in
  ci)            lint; test_suite; audit; echo "local CI: OK";;
  test)          test_suite;;
  firmware)      firmware;;
  lint)          lint;;
  webcheck)      webcheck;;
  typecheck)     typecheck;;
  audit)         audit;;
  web-fresh)     web_fresh;;
  codec-fresh)   codec_fresh;;
  doc-offset)    doc_offset;;
  screenshots)   screenshots;;
  import-clean)  import_clean;;
  vendor-check)  vendor_check;;
  dev)           dev;;
  *) echo "usage: tools/ci.sh [ci|test|firmware|lint|webcheck|typecheck|audit|web-fresh|codec-fresh|doc-offset|screenshots|import-clean|vendor-check|dev]"; exit 2;;
esac
