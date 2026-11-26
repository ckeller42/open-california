"""The CI path filter (tools/ci_changes.py, ci.yml job ``changes``) picks the heavy jobs a change needs."""

import subprocess
import sys

import pytest

from tools.ci_changes import AREAS, areas

ALL = set(AREAS)


def hits(*paths):
    return {name for name, hit in areas(paths).items() if hit}


@pytest.mark.parametrize(
    "paths, expected",
    [
        (["docs/firmware.md"], set()),
        (["README.md", "docs/simulation-and-testing.md", "AGENTS.md"], set()),
        (["bridge/src/index.js"], set()),
        (["firmware/components/cali_lora/lora_esp.c"], {"firmware", "host_e2e"}),
        (["firmware/web/app_bundle_gen.h"], {"firmware", "host_e2e", "gui"}),
        (["csrc/codec.c"], {"firmware", "host_e2e", "ios"}),
        (["calictl/webui/x.js"], {"gui", "host_e2e"}),
        (["calictl/webui/semantics_tables.js"], {"gui", "host_e2e", "ios"}),
        (["calictl/pairing_bluez.py"], {"gui", "host_e2e", "realstack"}),
        (["tools/check_fw_build.py"], {"firmware", "host_e2e", "gui", "realstack"}),
        (["tests/e2e/test_gui.py"], {"gui"}),
        (["tests/firmware/test_thermostat_e2e.py"], {"host_e2e"}),
        (["tests/firmware/test_qemu_boot.py"], {"firmware", "host_e2e"}),
        (
            ["sim/coresim/devices.py"],
            {"firmware", "host_e2e"},
        ),  # firmware-sim; tests/firmware imports coresim
        (["firmware/host/sim/lora_sim_main.c"], {"firmware", "host_e2e"}),
        (["tests/firmware/test_lora_esp_sim.py"], {"firmware", "host_e2e"}),
        (["tests/realstack/rig.py"], {"realstack"}),
        (["ui/screens/air-heater.yaml"], {"gui"}),
        (["requirements-e2e.txt"], {"gui", "host_e2e"}),
        (["ios/Camper/App.swift"], {"ios"}),
        ([".github/workflows/ci.yml"], ALL),
        (["docs/x.md", "tools/ci_changes.py"], ALL),
        ([], set()),
    ],
)
def test_areas(paths, expected):
    assert hits(*paths) == expected


def test_cli_prints_github_outputs():
    out = subprocess.run(
        [sys.executable, "-m", "tools.ci_changes"],
        input="docs/a.md\nfirmware/main/main.c\n",
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert out.splitlines() == [
        "%s=%s" % (n, "true" if n in {"firmware", "host_e2e"} else "false") for n in AREAS
    ]
