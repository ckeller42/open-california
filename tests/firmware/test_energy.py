"""Energy counters + house load (``energy.c``, R_FW_ENERGY_COUNTERS).

.. test:: Energy counters: the house load equals the web UI's formula over calictl's semantics, Wh integrate only fresh steps
   :id: T_FW_ENERGY_COUNTERS
   :links: R_FW_ENERGY_COUNTERS
"""

import json
import random
import shutil
import subprocess
from pathlib import Path

import pytest

from calictl import protocol, semantics

ROOT = Path(__file__).resolve().parents[2]
CORE = ROOT / "firmware/components/cali_core"
FUNCS = protocol.load()
EN_LEN = 22


@pytest.fixture(scope="module")
def cli(tmp_path_factory):
    cc = shutil.which("cc") or pytest.skip("no C compiler")
    out = tmp_path_factory.mktemp("energy") / "energy_cli"
    subprocess.run(
        [cc, "-std=c99", "-Wall", "-Wextra", "-Werror", "-I", str(CORE / "include"), "-I", str(ROOT / "csrc")]
        + [str(CORE / f) for f in ("energy.c", "json.c", "test/energy_cli.c")]
        + [str(ROOT / "csrc/codec.c"), "-lm", "-o", str(out)],
        check=True,
    )
    return out


def run(cli, *lines):
    out = subprocess.run(
        [str(cli)], input="\n".join(lines) + "\n", capture_output=True, text=True, check=True
    )
    return out.stdout.splitlines()


def frame(**fields):
    """A 22-byte energy frame with the given raw fields (others 0)."""
    return (
        protocol.encode_state(FUNCS["energy"], fields) if hasattr(protocol, "encode_state") else _set(fields)
    )


def _set(fields):
    bits = bytearray(EN_LEN)
    by_name = {f.name: f for f in FUNCS["energy"].state_fields if f.placed}
    for name, v in fields.items():
        f = by_name[name]
        for k in range(f.width):
            if v >> (f.width - 1 - k) & 1:
                i = f.offset + k
                bits[i // 8] |= 0x80 >> (i % 8)
    return bytes(bits)


def expected_load(raw: bytes, amb_sw, mppt_a):
    """The web UI's houseLoad() over calictl's own interpretation (A, or None for "—")."""
    st = {"energy": semantics.energy(protocol.decode(FUNCS["energy"], raw))}
    if amb_sw:
        st["general"] = {"amb_sw_version": amb_sw}
    e = semantics.apply_sw_corrections(st)["energy"]
    total = mppt_a
    for on, a in (
        (e["solar_installed"], e["solar_current"]),
        (e["dcdc_installed"], e["dcdc_current"]),
        (e["shore_installed"], e["shore_current"]),
    ):
        if on:
            total += a
    amps = total - e["batt2_current"]
    return None if amps < -0.5 else max(amps, 0.0)


def test_house_load_parity_with_semantics(cli):
    rnd = random.Random(7)
    cases = []
    for _ in range(300):
        raw = bytes(rnd.randrange(256) for _ in range(EN_LEN))
        amb = rnd.choice([None, "0410", "0409", "0411"])
        mppt = rnd.choice([0.0, 3.5, 12.3])
        cases.append((raw, amb, mppt))
    out = run(cli, *(f"load {r.hex()} {a or '-'} 1 {m}" for r, a, m in cases))
    for (raw, amb, mppt), got in zip(cases, out):
        want = expected_load(raw, amb, mppt)
        if want is None:
            assert got == "null", (raw.hex(), amb, mppt, got)
        else:
            assert got != "null" and abs(float(got) - want) < 1e-6, (raw.hex(), amb, mppt, got, want)


def test_dcdc_correction_and_stale_charger(cli):
    # 2026-10-16 evidence shape: MPPT 8.5 A, battery +7.8 A, idle DC-DC raw -2 on SW 0410 -> 0.7 A
    raw = frame(DcdcInstalled=1, IDcdcAfs=0x10000 - 2, ITwoBattBemAfs=78, UTwoBattBemAfs=131)
    assert run(cli, f"load {raw.hex()} 0410 1 8.5") == ["0.700"]
    assert run(cli, f"load {raw.hex()} - 1 8.5") == ["null"]  # no correction: -1.3 A is a mismatch
    assert run(cli, f"load {raw.hex()} 0410 0 8.5") == ["null"]  # a stale charger: unknown


def test_amb_sw_from_general(cli):
    gen = bytearray(9)
    f = next(x for x in FUNCS["general"].state_fields if x.name == "AmbSwVersion")
    v = int.from_bytes(b"0410", "big")
    for k in range(32):
        if v >> (31 - k) & 1:
            i = f.offset + k
            gen[i // 8] |= 0x80 >> (i % 8)
    assert run(cli, f"amb {bytes(gen).hex()}") == ["0410"]
    assert run(cli, "amb " + "00" * 9) == ["null"]


def test_integration_deci_wh(cli):
    # 13.0 V, battery +2.0 A, MPPT 5.0 A, nothing installed in the unit: load 3.0 A
    raw = frame(ITwoBattBemAfs=20, UTwoBattBemAfs=130)
    out = run(cli, f"step {raw.hex()} - 1 5.0 1 3600", "ctr")
    # one hour: solar 5 A x 13 V = 65.0 Wh, load 39.0 Wh, battery net 26.0 Wh (deci-Wh)
    assert out == ["650 390 260"], out
    # a discharging battery counts down, and 1 s steps lose nothing to rounding
    raw = frame(ITwoBattBemAfs=0x10000 - 15, UTwoBattBemAfs=125)
    assert run(cli, f"step {raw.hex()} - 1 0 1 7200", "ctr") == ["0 375 -375"]


def test_counters_wrap_at_24_bits(cli):
    raw = frame(ITwoBattBemAfs=1000, UTwoBattBemAfs=200)  # 100 A at 20 V: 20 deci-kWh per hour
    out = run(cli, f"step {raw.hex()} - 1 100 3600 1000", "ctr")
    solar, load, net = map(int, out[-1].split())
    assert solar == 20_000 * 1000 & 0xFFFFFF and load == 0
    assert -0x800000 <= net < 0x800000


def test_tick_needs_a_fresh_live_frame_and_no_gap(cli):
    raw = frame(ITwoBattBemAfs=20, UTwoBattBemAfs=130).hex()
    common = ["reset", f"fake {raw} - 1 now 0 0"]
    ticks = [f"tick {1000 + k * 1000}" for k in range(0, 3601)]  # one hour of 1 s ticks
    (e,) = [json.loads(x)["energy"] for x in run(cli, *common, *ticks, "json")]
    # +2 A into the battery with no source is a mismatch (-2 A): the load stays unknown, the battery counts
    assert (e["batt_net_wh"], e["load_wh"], e["boot"], e["load_a"]) == (26.0, 0, 7, None)
    # session's last frame older than 60 s: nothing integrates, the load is unknown
    out = run(cli, "reset", f"fake {raw} - 1 1000 0 0", "tick 70000", "tick 71000", "tick 72000", "json")
    assert json.loads(out[-1])["energy"] == {
        "boot": 7,
        "solar_wh": 0,
        "load_wh": 0,
        "batt_net_wh": 0,
        "load_a": None,
    }
    # not from this link: nothing
    out = run(cli, "reset", f"fake {raw} - 0 1000 0 0", "tick 1000", "tick 2000", "json")
    assert json.loads(out[-1])["energy"]["load_a"] is None
    # a 10 s gap is skipped, the next 1 s step counts
    out = run(cli, "reset", f"fake {raw} - 1 100000 0 0", "tick 100000", "tick 110000", "tick 111000", "json")
    assert json.loads(out[-1])["energy"]["batt_net_wh"] == 0.0  # 1 s at 26 W is 0.07 deci-Wh: still 0
    # a stale charger: solar and load stop, the battery still counts
    out = run(cli, "reset", f"fake {raw} - 1 now -1 0", *ticks, "json")
    e = json.loads(out[-1])["energy"]
    assert (e["solar_wh"], e["load_wh"], e["batt_net_wh"], e["load_a"]) == (0, 0, 26.0, None)
