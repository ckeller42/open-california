"""LoRaWAN telemetry, the platform-free part (``lora.c``, R_FW_LORA).

.. test:: LoRa: AT parsing, airtime + budgets (confirmed vs charged), port 2/3/4 packing, the switch and the key store
   :id: T_FW_LORA
   :links: R_FW_LORA
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from calictl import protocol

ROOT = Path(__file__).resolve().parents[2]
CORE = ROOT / "firmware/components/cali_core"
FUNCS = protocol.load()
CHARS = [
    line.split('"')[1]
    for line in (ROOT / "csrc/codec_chars.h").read_text().splitlines()
    if line.strip().startswith('{"')
]


@pytest.fixture(scope="module")
def cli(tmp_path_factory):
    cc = shutil.which("cc") or pytest.skip("no C compiler")
    out = tmp_path_factory.mktemp("lora") / "lora_cli"
    subprocess.run(
        [
            cc,
            "-std=c99",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-I",
            str(CORE / "include"),
            "-I",
            str(ROOT / "csrc"),
            "-I",
            str(ROOT / "firmware/components/platform/include"),
        ]
        + [str(CORE / f) for f in ("lora.c", "json.c", "test/lora_cli.c")]
        + ["-lm", "-o", str(out)],
        check=True,
    )
    return out


def run(cli, *lines):
    out = subprocess.run(
        [str(cli)], input="\n".join(lines) + "\n", capture_output=True, text=True, check=True
    )
    return out.stdout.splitlines()


def test_parse(cli):
    out = run(
        cli,
        "parse OK",
        "parse AT_BUSY_ERROR",
        "parse AT_NO_NETWORK_JOINED",
        "parse AT_PARAM_ERROR",
        "parse +EVT:JOINED",
        "parse +EVT:JOIN_FAILED_RX_TIMEOUT",
        "parse +EVT:TX_DONE",
        "parse +EVT:LINKCHECK:0,12,2,-98,7",
        "parse +EVT:LINKCHECK:0:9:3:-101:5",
        "parse +EVT:RX_1:-87:6:UNICAST:10:01020304",
        "parse AT+DR=3",
        "parse RAKwireless RAK3172",
    )
    assert [x.split()[0] for x in out] == [
        "OK",
        "BUSY",
        "NOT_JOINED",
        "ERROR",
        "JOINED",
        "JOIN_FAILED",
        "TX_DONE",
        "LINKCHECK",
        "LINKCHECK",
        "RX",
        "VALUE",
        "NONE",
    ]
    assert out[7].split()[1:6] == ["0", "12", "2", "-98", "7"]
    assert out[8].split()[1:6] == ["0", "9", "3", "-101", "5"]  # RUI 4.2.4 sends colons
    assert out[9].split()[4:] == ["-87", "6", "-", "10", "01020304"]
    assert out[10].split()[6] == "3"


def test_outcome_confirmed_only_with_tx_done(cli):
    out = run(
        cli, *(f"outcome {k} {t}" for k in ("BUSY", "ERROR", "NOT_JOINED", "OK", "NONE") for t in (0, 1))
    )
    assert out == ["0 0"] * 6 + ["1 0", "1 1", "1 0", "1 1"]


def test_airtime(cli):
    assert run(cli, "airtime 36 7", "airtime 36 9", "airtime 36 12", "airtime 12 10") == [
        "77",
        "267",
        "1974",
        "288",
    ]
    assert run(cli, "maxpl 7", "maxpl 9", "maxpl 10", "maxpl 12") == ["222", "115", "51", "51"]


def test_budget_confirmed_and_charged(cli):
    out = run(
        cli,
        "up 0 700 1",
        "up 600 700 0",
        "up 1200 288 0",
        "used 1300",
        "conf 1300",
        "room 1300",
        "conf 86400",
    )
    assert out == ["1688", "700", str(25000 - 1688), "0"]
    # the room never underflows; a full budget waits for the oldest uplink to age out
    assert run(cli, "up 0 30000 0", "room 10")[-1] == "0"
    out = run(cli, *(f"up {k * 600} 2500 1" for k in range(10)), "interval 6000 7 23 100")
    assert out == [str(86400 - 6000)]
    assert run(cli, "interval 0 7 23 100", "interval 0 12 50 60") == ["600", str(-(-86400 * 2793 // 15000))]
    # a full ring has no room: one more would drop an unexpired uplink's airtime
    out = run(cli, *(f"up {k} 1 0" for k in range(320)), "room 400", "room 86400")
    assert out == ["0", str(25000 - 319)]


def test_downlinks_and_backoff(cli):
    out = run(cli, *(f"dl {k}" for k in range(9)), "dl 86400")
    assert out[:8] == [f"1 {k + 1}" for k in range(8)] and out[8] == "0 8" and out[9] == "1 8"
    assert run(cli, "backoff 0", "backoff 3", "backoff 9") == ["60", "480", "3600"]


ENERGY = bytes(range(1, 23))
WATER = bytes([0x11, 0x22, 0x33, 0x44, 0x55, 0x66])
GENERAL = bytes(range(0x30, 0x39))


def test_pack_energy_layout(cli):
    out = run(
        cli,
        f"frame energy {ENERGY.hex()}",
        f"frame water {WATER.hex()}",
        "mppt -3.5 13.2 3 0 0.4 118 1.25",
        "bsense 13.05",
        "ctr 1234 2345 -345",
        "boot 70000",
        "snap 5000",
        "pack2",
    )
    b = bytes.fromhex(out[-1])
    assert len(b) == 50 and b[0] == 1 and int.from_bytes(b[1:3], "big") == 70000 & 0xFFFF
    assert b[3:25] == ENERGY and b[25:31] == WATER
    assert int.from_bytes(b[31:33], "big", signed=True) == -35
    assert int.from_bytes(b[33:35], "big") == 118 and int.from_bytes(b[35:37], "big") == 125
    assert (b[37], b[38]) == (3, 0) and int.from_bytes(b[39:41], "big") == 1305
    assert [int.from_bytes(b[i : i + 3], "big", signed=(i == 47)) for i in (41, 44, 47)] == [1234, 2345, -345]


def test_pack_energy_missing_inputs_read_ff(cli):
    b = bytes.fromhex(run(cli, "frame water 1122", "snap 0", "pack2")[-1])
    assert (
        b[3:25] == b"\xff" * 22 and b[25:31] == bytes([0x11, 0x22]) + b"\xff" * 4 and b[31:41] == b"\xff" * 10
    )


def test_pack_energy_stale_sensor_reads_ff(cli):
    sensors = ["mppt -3.5 13.2 3 0 0.4 118 1.25", "bsense 13.05", "sensat 1000"]
    fresh = bytes.fromhex(run(cli, *sensors, "snap 61000", "pack2")[-1])  # 60 s old: still counts
    stale = bytes.fromhex(run(cli, *sensors, "snap 61001", "pack2")[-1])
    assert (
        int.from_bytes(fresh[31:33], "big", signed=True) == -35
        and int.from_bytes(fresh[39:41], "big") == 1305
    )
    assert stale[31:41] == b"\xff" * 10


def test_pack_daily(cli):
    b = bytes.fromhex(
        run(cli, f"frame general {GENERAL.hex()}", "boot 3", "snap 7200000", "pack4 a1b2c3d4e")[-1]
    )
    assert b[0] == 1 and int.from_bytes(b[1:3], "big") == 3 and int.from_bytes(b[3:7], "big") == 7200
    assert b[7:16] == GENERAL and b[16:24] == b"a1b2c3d4"


def entries(payload):
    out, i = [], 0
    while i < len(payload):
        n = payload[i + 1]
        out.append((CHARS[payload[i]], payload[i + 2 : i + 2 + n]))
        i += 2 + n
    return out


def test_tracker_sends_changes_once_and_commits_after_a_good_send(cli):
    setup = [
        "frame cooler 0180300000000000",
        "frame campingmode 03",
        f"frame energy {ENERGY.hex()}",
        "snap 0",
        "trk_init",
        "trk_update 0",
    ]
    out = run(cli, *setup, "trk_pack 51", "trk_pack 51", "trk_commit 10", "trk_update 20", "trk_pack 51")
    first = entries(bytes.fromhex(out[0]))
    assert sorted(f for f, _ in first) == ["campingmode", "cooler"]  # energy never: port 2 carries it
    assert out[1] == out[0]  # a dry pack consumes nothing
    assert out[2] == ""  # committed, unchanged
    # a change, then the 12-h refresh
    out = run(
        cli,
        *setup,
        "trk_pack 51",
        "trk_commit 10",
        "frame campingmode 01",
        "snap 0",
        "trk_update 20",
        "trk_pack 51",
        "trk_commit 30",
        "trk_update 43210",
        "trk_pack 51",
    )
    assert entries(bytes.fromhex(out[1])) == [("campingmode", b"\x01")]
    assert sorted(f for f, _ in entries(bytes.fromhex(out[2]))) == ["cooler"]


def test_tracker_whole_entries_within_the_limit(cli):
    out = run(
        cli,
        "frame cooler " + "11" * 8,
        "frame lighting " + "22" * 16,
        "frame roof " + "33" * 4,
        "snap 0",
        "trk_init",
        "trk_update 0",
        "trk_pack 20",
    )
    got = entries(bytes.fromhex(out[-1]))
    assert sum(2 + len(f) for _, f in got) <= 20 and ("lighting", b"\x22" * 16) not in got and got


def test_status_json(cli):
    (line,) = run(cli, "stjson 1 1 0 10 900 4210 4500 1 2 1000")
    st = json.loads(line)["lora"]
    assert st == {
        "module": "present",
        "joined": True,
        "at_unanswered": 0,
        "sf": 10,
        "last_s": 100,
        "airtime_ms_24h": 4210,
        "airtime_charged_ms_24h": 4500,
        "unconfirmed_sends": 1,
        "downlinks_24h": 2,
        "uplinks": [0, 0, 0],
        "note": None,
    }
    # a silent module: no confirmed uplink ever, the counter says so
    st = json.loads(run(cli, "stjson 1 1 5 12 0 0 18070 26 0 9999")[0])["lora"]
    assert st["at_unanswered"] == 5 and st["last_s"] is None and st["airtime_ms_24h"] == 0


def test_switch_and_keys_never_printed(cli):
    out = run(cli, "exp", "console on", "exp", "console off", "exp")
    assert out == ["0", "LOG exp: lora on", "1", "LOG exp: lora off", "0"]
    key = bytes(range(0, 0x100, 0x11)).hex()  # a dummy AppKey, built so no scanner takes it for a real one
    out = run(cli, f"console keys ac1f09fffe27799a 0000000000000000 {key}", "kv")
    assert out[0] == "LOG lora: keys stored (used at the next join attempt)" and key not in out[0]
    assert out[1] == f"lora_keys ac1f09fffe27799a0000000000000000{key}"
    assert run(cli, "console keys 12 34 56")[0].startswith("LOG lora: keys: 16 + 16 + 32")


def test_tick_hands_a_snapshot_over_and_a_switch_change_at_once(cli):
    out = run(cli, "tick 0", "tick 5000", "tick 10000", "console on", "tick 10500")
    assert out == ["POST 0", "POST 0", "LOG exp: lora on", "POST 1"]


def formatter(port, data):
    """Decode one uplink with the generated TTN formatter (node)."""
    import os

    node = shutil.which("node")
    if not node:  # a missing node must not silently skip the gate in CI
        (pytest.fail if os.environ.get("CI") else pytest.skip)("node not available")
    js = ROOT / "firmware/components/cali_lora/ttn_formatter.js"
    code = f"const f=require({json.dumps(str(js))});console.log(JSON.stringify(f.decodeUplink({{bytes:{json.dumps(list(data))},fPort:{port}}})))"
    return json.loads(subprocess.run([node, "-e", code], capture_output=True, text=True, check=True).stdout)


def test_formatter_round_trip_gives_calictl_raw_fields(cli):
    import random

    rnd = random.Random(3)
    for _ in range(20):
        en, wa = bytes(rnd.randrange(255) for _ in range(22)), bytes(rnd.randrange(255) for _ in range(6))
        out = run(
            cli,
            f"frame energy {en.hex()}",
            f"frame water {wa.hex()}",
            "mppt 2.3 13.5 5 0 0 87 0.42",
            "bsense 13.05",
            "ctr 1234 2345 -345",
            "boot 7",
            "snap 0",
            "pack2",
        )
        d = formatter(2, bytes.fromhex(out[-1]))["data"]
        assert d["fn"]["energy"] == protocol.decode(FUNCS["energy"], en)
        assert d["fn"]["water"] == protocol.decode(FUNCS["water"], wa)
        assert d["counters"] == {"solar_wh": 123.4, "load_wh": 234.5, "batt_net_wh": -34.5}
        assert d["ext"]["victron_mppt"] == {
            "battery_current": 2.3,
            "pv_power": 87,
            "yield_today_kwh": 0.42,
            "charge_state": 5,
            "error_code": 0,
        }
        assert d["ext"]["victron_battery"] == {"battery_voltage": 13.05}
    # no frames, no sensors: nulls, never invented values
    d = formatter(2, bytes.fromhex(run(cli, "snap 0", "pack2")[-1]))["data"]
    assert d["fn"] == {} and d["ext"]["victron_mppt"]["battery_current"] is None
    # port 3 and 4
    out = run(
        cli,
        "frame cooler 0180300000000000",
        "frame campingmode 03",
        f"frame general {GENERAL.hex()}",
        "snap 3600000",
        "trk_init",
        "trk_update 0",
        "trk_pack 51",
        "pack4 fw1",
    )
    d3 = formatter(3, bytes.fromhex(out[0]))["data"]["fn"]
    assert d3 == {
        "cooler": protocol.decode(FUNCS["cooler"], bytes.fromhex("0180300000000000")),
        "campingmode": protocol.decode(FUNCS["campingmode"], b"\x03"),
    }
    d4 = formatter(4, bytes.fromhex(out[1]))["data"]
    assert (
        d4["uptime_s"] == 3600
        and d4["fw"] == "fw1"
        and d4["fn"]["general"] == protocol.decode(FUNCS["general"], GENERAL)
    )
    assert formatter(3, b"\x00\x05\x01")["errors"] and formatter(9, b"\x01")["errors"]
