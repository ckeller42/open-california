"""Which heavy CI jobs a change needs (ci.yml job ``changes``): one place for the path filters.

Reads the changed paths (``git diff --name-only HEAD^1 HEAD``, one per line) on stdin and prints one
``<area>=true|false`` line per area for ``$GITHUB_OUTPUT``. Each area's regex lists what its jobs
build, import or serve; a change to ci.yml or to this file runs every area. ci.yml runs every job
except ``ios`` on a push to main regardless of these outputs, and runs a job whose ``changes``
output is missing (the ``changes`` job failed), so an error here never skips a required check.
"""

import re
import sys

ALWAYS = r"\.github/workflows/ci\.yml$|tools/ci_changes\.py$"
PY_DEPS = r"requirements[^/]*\.txt$|pyproject\.toml$"

AREAS = {
    # firmware-build + firmware-qemu: the ESP-IDF build of firmware/ (csrc/ is its codec component;
    # the generated headers in csrc/ and firmware/web/ come from protocol/ and calictl/webui/, whose
    # freshness codec-parity checks on every PR), the build check and the QEMU boot test.
    # firmware-sim: coresim (sim/: fakes, shims, its tests), the satellite's sim build (firmware/host/sim)
    # and its adapter + scenarios in tests/firmware.
    "firmware": r"firmware/|csrc/|protocol/|sim/|tools/check_fw_build\.py$"
    r"|tests/firmware/(conftest|test_qemu_boot|esp_sim|hil|test_lora_esp_sim|test_lora_scenarios)\.py$",
    # firmware-host-e2e: firmware/host builds firmware/components, csrc/ and firmware/web; the tests
    # import calictl and tools (the fake unit, mock, vectors) and tests/test_semantics_js_parity.py.
    "host_e2e": r"firmware/|csrc/|protocol/|sim/|calictl/|tools/|tests/(firmware|vectors)/|tests/conftest\.py$"
    r"|tests/test_semantics_js_parity\.py$|" + PY_DEPS,
    # gui-e2e: the daemon + web UI (calictl, protocol, the ui/ screens build_web reads), the remote app
    # and PWA (calictl/webui), the satellite page (firmware/web/app_bundle_gen.h via tools.ux_gallery).
    "gui": r"calictl/|tools/|ui/|protocol/|firmware/web/|tests/e2e/|tests/conftest\.py$|" + PY_DEPS,
    # pairing-real-stack: tests/realstack drives calictl's BluezTransport against tools' fake unit
    # (Python only: the web UI in calictl/webui/ is not part of it).
    "realstack": r"calictl/(?!webui/)|tools/|protocol/|tests/realstack/",
    # ios: the Swift sources, the C codec + control planner the Swift package links (Package.swift),
    # the golden vectors and semantics.js it runs. ci.yml keeps the filter on main pushes too.
    "ios": r"ios/|Package\.(swift|resolved)$|csrc/(codec\.c|ports\.c|[^/]*\.h|swiftpm/)"
    r"|firmware/components/cali_core/(control\.c$|include/|swiftpm/)|tests/vectors/"
    r"|calictl/webui/semantics[^/]*\.js$|tools/(swift_needs\.py|ios_sanitize\.sh)$",
}


def areas(paths):
    """``{area: bool}``: whether any of ``paths`` (repo-relative) touches the area."""
    return {name: any(re.match("(?:%s|%s)" % (ALWAYS, rx), p) for p in paths) for name, rx in AREAS.items()}


if __name__ == "__main__":
    for name, hit in areas([ln.strip() for ln in sys.stdin if ln.strip()]).items():
        print("%s=%s" % (name, "true" if hit else "false"))
