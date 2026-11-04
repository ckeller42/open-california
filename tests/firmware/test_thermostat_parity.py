"""The ESP's thermostat decision equals calictl's (R_FW_THERMOSTAT_DECIDE).

``tools/gen_thermostat_vectors.py`` records ``calictl.thermostat.step`` (and ``heater_state``) over a
rule grid, a seeded random sweep and scripted sessions; ``firmware/components/cali_core/thermostat.c``
(driven by ``test/thermostat_cli.c``, host ``cc``; on Linux also as an i686 ``-m32`` binary) must
return the same commands, reason, mode, status and next state for every case.

.. test:: The C thermostat decision reproduces every Python vector
   :id: T_FW_THERMOSTAT_PARITY
   :links: R_FW_THERMOSTAT_DECIDE, R_THERMOSTAT_SESSION, R_THERMOSTAT_CONTROL, R_THERMOSTAT_STATE
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from calictl import thermostat as th
from tools import gen_thermostat_vectors as gen

ROOT = Path(__file__).resolve().parents[2]
CORE = ROOT / "firmware" / "components" / "cali_core"
V = json.loads(gen.OUT.read_text(encoding="utf-8"))
LONG_IS_4 = "typedef char long_is_4_bytes[sizeof(long) == 4 ? 1 : -1];\n"
UNREACHABLE = {"FAULT_1", "FAULT_4"}  # ErrorCode 1 reports BATTERY_LOW, ErrorCode 4 reports LOCKED


def test_checked_in_vectors_are_fresh():
    assert gen.OUT.read_text(encoding="utf-8") == gen.render()


def test_vectors_reach_every_reason():
    seen = {c[4][0] for c in V["cases"]}
    assert set(th.REASONS) - UNREACHABLE <= seen
    assert not UNREACHABLE & seen
    assert None in seen  # an idle step in mode OFF
    adopt = [c for c in V["cases"] if (c[4][0] or "").startswith("ADOPT_")]
    assert adopt and not [
        c for c in adopt if c[4][13] and c[4][14] != "level"
    ]  # an adopt never sends a command
    refused = [c[4][14:] for c in V["cases"] if (c[4][0] or "").startswith("AUTO_REFUSED_")]
    assert any(refused)  # a refusal sends nothing new; only a kept stop is re-sent
    assert all(cmds in ([], ["power", 0], ["permanent", 0]) for cmds in refused)


# Positions of the stop causes in a flat state (pending.why, stop_snapshot.why).
_PROBE = gen.flat_st(
    {
        **th.initial_state(),
        "pending": th._pend("power", "off", 0, why="P"),
        "stop_snapshot": {"normal": 1, "permanent": 0, "level": None, "why": "S"},
    }
)
PWHY, SWHY = _PROBE.index("P"), _PROBE.index("S")


def test_vectors_never_drop_a_safety_stop():
    prev, held = None, 0
    for c in V["cases"]:
        st = c[3] or prev
        prev = c[5]
        cause = st[PWHY] or (st[SWHY] if st[0] == "STOPPING" else None)
        if c[2][2] == "AUTO" and cause not in (None, "USER_OFF"):
            held += 1
            # the AUTO request is ignored; AUTO starts (or is refused) only when the readback of this
            # same step showed the stop done (no stop pending or waiting afterwards)
            done = c[5][PWHY] is None and c[5][0] != "STOPPING"
            assert done or not (c[4][0] or "").startswith(("ADOPT_", "AUTO_REFUSED_")), c
            assert c[4][1] != "AUTO" or (c[4][0], c[4][2]) == ("START", "OFF"), c
    assert held  # the vectors reach the case


def test_vectors_have_the_sweep_and_the_sessions():
    groups = [c[0] for c in V["cases"]]
    assert groups.count("sweep") >= 2000
    assert len({g for g in groups if g.startswith("session:")}) >= 20
    assert len(V["heater"]) >= 50


def test_vectors_cover_the_control_branches():
    assert {
        "windup_low",
        "windup_min_level",
        "windup_high",
        "outside_lost",
        "outside_found",
        "opposite_sign_integrates",
        "gap_frozen",
        "cur_level_none",
        "neg_half_p",
        "neg_half_level",
        "pos_half_level",
        "adopt_to_demand",
        "night_preset",
        "night_wait_adopt",
        "night_unconfirmed",
        "night_confirm_past_wait",
        "night_wait_timeout",
        "night_user_off",
        "night_skips_warm",
        "continuous_hold_min",
        "continuous_hold_overheat",
        "blocked_known_frames",
        "wait_left_ceil",
        *(
            "blocked_" + c
            for c in ("NOT_INSTALLED", "FAULT", "TIMER_ARMED", "NO_CAMPING", "IGNITION", "NO_REFERENCE")
        ),
    } <= gen.coverage()


def _t(v):
    return "-" if v is None else str(int(v)) if isinstance(v, bool) else str(v)


def _line(*parts):
    return " ".join(_t(v) for part in parts for v in part)


@pytest.fixture(scope="module", params=["native", pytest.param("m32", marks=pytest.mark.linux_only)])
def cli(request, tmp_path_factory):
    tmp = tmp_path_factory.mktemp("thermo")
    if request.param == "native":
        cmd = [shutil.which("cc") or pytest.skip("no C compiler")]
    else:
        cmd = [os.environ.get("CROSS_COMPILE", "") + "gcc", "-m32"]
        probe = tmp / "long_is_4.c"
        probe.write_text(LONG_IS_4)
        subprocess.run(cmd + ["-c", str(probe), "-o", str(tmp / "probe.o")], check=True)  # really 32-bit
    out = tmp / "thermostat_cli"
    subprocess.run(
        cmd + [
            "-std=c99", "-Wall", "-Wextra", "-Werror",
            "-I", str(CORE / "include"),
            str(CORE / "thermostat.c"), str(CORE / "test" / "thermostat_cli.c"),
            "-o", str(out),
        ],
        check=True,
    )  # fmt: skip
    return out


def test_c_defaults_equal_python(cli):
    got = subprocess.run([str(cli), "defaults"], capture_output=True, text=True, check=True)
    assert got.stdout.split() == [str(v) for v in th.THERMO_DEFAULTS.values()]


def test_c_twin_reproduces_every_vector(cli):
    cfgs = [list(c.values()) for c in V["cfgs"]]
    lines = ["H " + _line(cfgs[0], h_in) for h_in, _ in V["heater"]]
    prev = None
    for _, k, inp, st, _, st2 in V["cases"]:  # st null = the previous case's st2
        lines.append("S " + _line(cfgs[k], inp, st or prev))
        prev = st2
    got = subprocess.run(
        [str(cli)], input="\n".join(lines) + "\n", capture_output=True, text=True, check=True
    )
    outs = got.stdout.splitlines()
    assert len(outs) == len(lines)
    for h, line in zip(V["heater"], outs):
        assert line == _line(h[1]), h[0]
    for n, (c, line) in enumerate(zip(V["cases"], outs[len(V["heater"]) :])):
        assert line == _line(c[4]) + " | " + _line(c[5]), n
