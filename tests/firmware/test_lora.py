"""LoRaWAN telemetry, the platform-free part (``lora.c``, R_FW_LORA).

.. test:: LoRa: AT parsing, airtime + budgets (confirmed vs charged), port 2/3/4 packing, the switch and the key store
   :id: T_FW_LORA
   :links: R_FW_LORA
"""

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from calictl import protocol

from .test_lora_setup import key, plaintext

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
        + [str(CORE / f) for f in ("lora.c", "lora_setup.c", "json.c", "test/lora_cli.c")]
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


def test_redact_hides_the_keys_and_euis_both_ways(cli):
    """What lora_esp.c logs for each AT line, sent (>>) and received (<<)."""
    eui, key = "AC1F09FFFE27799A", bytes(range(0, 0x100, 0x11)).hex().upper()  # dummy values
    lines = [f"AT+DEVEUI={eui}", f"AT+APPEUI={eui}", f"AT+APPKEY={key}", "AT+DEVEUI=?", "AT+DR=?", "AT+DR=5"]
    lines += ["AT+SEND=2:0102", "+EVT:JOINED", "OK"]
    lines += [
        "+EVT:RX_C:-70:8:UNICAST:12:" + "AB" * 51,
        "+EVT:RX_1:-70:8:UNICAST:2:0102",
    ]  # a fragment, a command
    assert run(cli, *(f"redact {line}" for line in lines)) == [
        "AT+DEVEUI=<redacted>",
        "AT+APPEUI=<redacted>",
        "AT+APPKEY=<redacted>",
        "AT+DEVEUI=?",
        "AT+DR=?",
        "AT+DR=5",
        "AT+SEND=2:0102",
        "+EVT:JOINED",
        "OK",
        "+EVT:RX_C:-70:8:UNICAST:12:<redacted>",
        "+EVT:RX_1:-70:8:UNICAST:2:0102",
    ]


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
    assert run(cli, "interval 0 7 23 100") == ["600"]
    # a full ring has no room: one more would drop an unexpired uplink's airtime
    out = run(cli, *(f"up {k} 1 0" for k in range(320)), "room 400", "room 86400")
    assert out == ["0", str(25000 - 319)]


def test_port2_interval_keeps_its_share_in_every_rolling_day(cli):
    """An uplink leaves the ring when now - t >= 86400, so packets at 0, I, 2I, ... put
    floor(86399 / I) + 1 of them in one window; that many times the airtime stays within port 2's
    60 % of 25 s at every SF (with an empty ring, so only the steady-state spacing counts)."""
    for sf in range(7, 13):
        air = int(run(cli, f"airtime {50 + 13} {sf}")[0])
        interval = int(run(cli, f"interval 0 {sf} 50 60")[0])
        assert interval >= 600 and (86399 // interval + 1) * air <= 15000, (sf, air, interval)
    assert run(cli, "interval 0 12 50 60") == [str(86400 // (15000 // 2793))]  # SF12: 5 a day, 17280 s


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
        "setup": "none",
        "has_ttn_keys": False,
        "dev_eui": None,
    }
    st = json.loads(run(cli, "stsetup 3 1 AC1F09FFFE27799A", "stjson 1 1 0 7 1 2 3 4 5 100")[0])["lora"]
    assert (st["setup"], st["has_ttn_keys"], st["dev_eui"]) == ("ttn_keys", True, "AC1F09FFFE27799A")
    st = json.loads(run(cli, "stsetup 2 0", "stjson 1 1 0 7 1 2 3 4 5 100")[0])["lora"]
    assert (st["setup"], st["dev_eui"]) == ("joined", None)
    # a silent module: no confirmed uplink ever, the counter says so
    st = json.loads(run(cli, "stjson 1 1 5 12 0 0 18070 26 0 9999")[0])["lora"]
    assert st["at_unanswered"] == 5 and st["last_s"] is None and st["airtime_ms_24h"] == 0


def test_switch_and_keys_never_printed(cli):
    out = run(cli, "exp", "console on", "exp", "console off", "exp")
    assert out == ["0", "LOG exp: lora on", "1", "LOG exp: lora off", "0"]
    key = bytes(range(0, 0x100, 0x11)).hex()  # a dummy AppKey, built so no scanner takes it for a real one
    out = run(cli, f"console keys ac1f09fffe27799a 0000000000000000 {key}", "kv lora_keys")
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


def test_setup_open_makes_keys_once(cli):
    """The AppKey is made once, the transfer key anew on every open; the state follows the store.

    .. test:: lora setup open: lora_dev once, lora_tk each time, the setup state
       :id: T_FW_LORA_SETUP_OPEN
       :links: R_FW_LORA_SETUP
    """
    out = run(
        cli,
        "setupstate",
        "setupopen",
        "kv lora_dev",
        "kv lora_tk",
        "setupstate",
        "setupopen",
        "kv lora_dev",
        "kv lora_tk",
        "kvset lora_ttn " + plaintext().hex(),
        "setupstate",
    )
    assert out[:2] == ["0", "0"] and out[4] == "1" and out[5] == "0" and out[8] == "3"
    dev1, tk1, dev2, tk2 = (out[i].split()[1] for i in (2, 3, 6, 7))
    assert len(bytes.fromhex(dev1)) == 24 and dev1.endswith("00" * 8) and dev1 == dev2
    assert len(bytes.fromhex(tk1)) == 16 and tk1 != tk2


def test_forget_erases_everything(cli):
    """forget erases the four keys and switches the LoRa switch off.

    .. test:: lora forget erases lora_dev, lora_tk, lora_ttn and lora_keys, exp_lora 0
       :id: T_FW_LORA_FORGET
       :links: R_FW_LORA_SETUP
    """
    dummy = bytes(range(0, 0x100, 0x11)).hex()
    out = run(
        cli,
        "console on",
        "setupopen",
        f"kvset lora_ttn {dummy}",
        f"kvset lora_keys {dummy}",
        "forget",
        "kv lora_dev",
        "kv lora_tk",
        "kv lora_ttn",
        "kv lora_keys",
        "exp",
        "setupstate",
    )
    assert out == [
        "LOG exp: lora on",
        "0",
        "LOG exp: lora off",
        "0",
        "lora_dev -",
        "lora_tk -",
        "lora_ttn -",
        "lora_keys -",
        "0",
        "0",
    ]


def test_kv_errors_never_regenerate_the_appkey(cli):
    """A corrupt lora_dev is an error, not "missing": setup_open returns -1 and keeps it; state is "error".

    .. test:: lora setup: only a MISSING lora_dev is made; corrupt reads and failing writes return -1
       :id: T_FW_LORA_SETUP_KV_ERR
       :links: R_FW_LORA_SETUP
    """
    out = run(
        cli,
        "setupopen",
        "kv lora_dev",
        "kv lora_tk",
        "kvcorrupt lora_dev",
        "setupstate",
        "setupopen",
        "kv lora_dev",
        "kv lora_tk",
        "kvcorrupt -",
        "setupstate",
    )
    assert out[0] == "0" and out[3] == "-1" and out[4] == "-1"
    assert out[5] == out[1] and out[6] == out[2]  # AppKey and transfer key unchanged
    assert out[7] == "1"
    # a failing store write is -1 too, and the status names the error
    assert run(cli, "setfail 1", "setupopen", "setupstate")[0] == "-1"
    st = json.loads(run(cli, "stsetup -1 0", "stjson 1 0 0 7 1 2 3 4 5 100")[0])["lora"]
    assert st["setup"] == "error" and st["has_ttn_keys"] is False
    assert run(cli, "kvset lora_dev 00", "setupopen")[0] == "-1"  # a wrong-sized AppKey is an error as well


def test_setup_state_checks_the_records_shape(cli):
    """A readable record of the wrong shape is "error", never "keys ready": a lora_dev that is not 24 bytes, a
    lora_ttn that is not a plaintext the repair QR can show.

    .. test:: lora setup state: a wrong-sized lora_dev or an unusable lora_ttn is error (-1)
       :id: T_FW_LORA_SETUP_SHAPE
       :links: R_FW_LORA_SETUP
    """
    dummy = bytes(range(0, 0x100, 0x11)).hex()
    out = run(
        cli, f"kvset lora_ttn {dummy}", "setupstate", f"kvset lora_ttn {plaintext().hex()}", "setupstate"
    )
    assert out == ["-1", "3"]  # kvset prints nothing
    assert run(cli, f"kvset lora_dev {dummy}", "setupstate") == ["-1"]


def test_console_status_names_the_setup_state(cli):
    out = run(cli, "console status", "setupopen", "console status")
    assert "setup none" in out[0] and "setup keys_made" in out[2]


def test_qr_source_by_state(cli):
    """The display's QR text: the setup QR from the module's DevEUI and the stored keys until the TTN keys
    arrive, then the repair QR from them; nothing while the switch is off, without a module, a DevEUI or a key.

    .. test:: lora QR text by setup state: OCS1 while keys_made, OCR1 with ttn_keys, none when off or incomplete
       :id: T_FW_LORA_QR_SOURCE
       :links: R_FW_LORA_SETUP
    """
    eui = "70B3D57ED0000001"
    dummy = bytes(range(0, 0x100, 0x11)).hex()
    out = run(
        cli,
        f"qr 1 1 {eui}",
        f"kvset lora_dev {dummy}{dummy[:16]}",
        f"qr 1 1 {eui}",
        "setupopen",
        "kv lora_dev",
        "kv lora_tk",
        f"qr 1 1 {eui}",
        f"qr 0 1 {eui}",
        f"qr 1 0 {eui}",
        "qr 1 1 -",
        "qr 1 1 70B3D57ED00000ZZ",
    )
    assert out[0] == "QR -"  # no lora_dev: nothing to show
    assert out[1] == "QR -"  # an AppKey without a transfer key: nothing
    appkey = out[3].split()[1][:32].upper()
    tk = out[4].split()[1].upper()
    assert out[5] == f"QR OCS1*{eui}*{appkey}*{tk}"
    assert out[6:10] == ["QR -"] * 4  # switch off, module absent, DevEUI unknown or not hex
    ttn = plaintext()
    out = run(
        cli,
        "setupopen",
        f"kvset lora_ttn {ttn.hex()}",
        f"qr 1 1 {eui}",
        f"qr 0 1 {eui}",
        "kvcorrupt lora_ttn",
        f"qr 1 1 {eui}",
    )
    want = f"OCR1*EU1.CLOUD.THETHINGS.NETWORK*MY-VAN-APP*VAN-000001*{key(3)}*{key(7)}"
    assert out[1:] == [f"QR {want}", "QR -", "QR -"]


def test_qr_text_too_small_leaves_no_key_bytes(cli):
    """A buffer too small for the QR text comes back zeroed, never with part of a key in it.

    .. test:: lora QR text: a too-small buffer is zeroed
       :id: T_FW_LORA_QR_WIPE
       :links: R_FW_LORA_SETUP
    """
    eui = "70B3D57ED0000001"
    out = run(
        cli, "setupopen", f"qr 1 1 {eui} 40", f"kvset lora_ttn {plaintext().hex()}", f"qr 1 1 {eui} 100"
    )
    assert out[1:] == ["QR -", "QR -"]


def test_setup_qr_text_regardless_of_state(cli):
    """The rotation's setup QR: OCS1 from the DevEUI, the AppKey and the transfer key even after the TTN
    keys arrived (the selector then shows the repair QR); nothing without a transfer key or on a store
    error, and the buffer comes back zeroed.

    .. test:: lora setup QR text: OCS1 also with ttn_keys, none (zeroed) without a transfer key or on a kv error
       :id: T_FW_LORA_SETUP_QR
       :links: R_FW_LORA_SETUP, R_FW_LORA_SCREEN
    """
    eui = "70B3D57ED0000001"
    dummy = bytes(range(0, 0x100, 0x11)).hex()
    out = run(
        cli,
        f"sqr 1 1 {eui}",
        f"kvset lora_dev {dummy}{dummy[:16]}",
        f"sqr 1 1 {eui}",
        "setupopen",
        "kv lora_dev",
        "kv lora_tk",
        f"sqr 1 1 {eui}",
        f"kvset lora_ttn {plaintext().hex()}",
        f"qr 1 1 {eui}",
        f"sqr 1 1 {eui}",
        f"sqr 0 1 {eui}",
        f"sqr 1 0 {eui}",
        "sqr 1 1 -",
        f"sqr 1 1 {eui} 40",
        "kvcorrupt lora_tk",
        f"sqr 1 1 {eui}",
    )
    assert out[0:2] == ["QR -", "QR -"]  # no AppKey; an AppKey without a transfer key
    appkey = out[3].split()[1][:32].upper()
    tk = out[4].split()[1].upper()
    setup = f"QR OCS1*{eui}*{appkey}*{tk}"
    assert out[5] == setup
    assert out[6].startswith("QR OCR1*")  # the selector: repair QR once the TTN keys are stored
    assert out[7] == setup  # the setup QR regardless of the state
    assert out[8:12] == ["QR -"] * 4  # switch off, module absent, DevEUI unknown, buffer too small
    assert out[12] == "QR -"  # transfer key unreadable


def test_ttn_plaintext_checked_before_storing(cli):
    """Only a plaintext the repair QR can show, and that fits lora_ttn, is stored.

    .. test:: lora key transfer: the plaintext must fit lora_ttn and encode as the repair QR
       :id: T_FW_LORA_TTN_OK
       :links: R_FW_LORA_SETUP
    """
    good = plaintext()
    bad = [plaintext(ver=2), good + b"\x00", plaintext(app="a*b"), good[:20]]
    big = plaintext(cluster="a" * 64, app="b" * 36, dev="c" * 36) + bytes(80)
    out = run(cli, f"ttnok {good.hex()}", *(f"ttnok {b.hex()}" for b in bad), f"ttnok {big.hex()}")
    assert out == ["1", "0", "0", "0", "0", "0"]


def test_ttn_plaintext_of_256_bytes_accepted(cli):
    """The longest plaintext (cluster 64, app 36, dev 36 characters) is exactly CALI_LORA_TTN_MAX and is stored.

    .. test:: lora key transfer: a 256-byte plaintext (the longest IDs) is accepted for lora_ttn
       :id: T_FW_LORA_TTN_MAX
       :links: R_FW_LORA_SETUP
    """
    longest = plaintext(cluster="a" * 64, app="b" * 36, dev="c" * 36)
    assert len(longest) == 256
    assert run(cli, f"ttnok {longest.hex()}", f"ttnok {(longest + bytes(1)).hex()}") == ["1", "0"]


def test_fragment_without_transfer_key_repeats_the_lost_confirmation(cli):
    """A port-12 fragment after the transfer finished (lora_tk erased, lora_ttn stored) is the app's probe after a
    lost status-0 confirmation: the van answers 00|sha256(lora_ttn)[0..3] again. Without lora_ttn: 03.

    .. test:: lora key transfer: a fragment without a transfer key gets 00|sha256(lora_ttn) when the keys are stored, else 03
       :id: T_FW_LORA_NO_TK
       :links: R_FW_LORA_SETUP
    """
    plain = plaintext()
    out = run(
        cli,
        "notk",
        f"kvset lora_ttn {plain.hex()}",
        "notk",
        f"kvset lora_ttn {plaintext(ver=2).hex()}",  # unusable bytes are never confirmed as success
        "notk",
    )
    assert out == ["03", "00" + hashlib.sha256(plain).digest()[:4].hex(), "03"]


def web_decode(port, data):
    """Decode one uplink with the web UI's generated decoder (``lora_decode.js``, a classic script)."""
    import os

    node = shutil.which("node")
    if not node:
        (pytest.fail if os.environ.get("CI") else pytest.skip)("node not available")
    js = ROOT / "calictl/webui/lora_decode.js"
    code = (
        'const fs=require("fs"),vm=require("vm"),c=vm.createContext({Uint8Array});'
        f"vm.runInContext(fs.readFileSync({json.dumps(str(js))},'utf8'),c);"
        f"console.log(JSON.stringify(vm.runInContext('loraDecodeUplink({port},Uint8Array.from({json.dumps(list(data))}))',c)))"
    )
    return json.loads(subprocess.run([node, "-e", code], capture_output=True, text=True, check=True).stdout)


def test_c_pack_js_decode_round_trip(cli):
    """C pack_energy / pack_changed / pack_daily -> the web UI's decoder -> calictl.protocol.decode's raw fields."""
    import random

    rnd = random.Random(9)
    for _ in range(10):
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
        d = web_decode(2, bytes.fromhex(out[-1]))
        assert d["port"] == 2 and d["version"] == 1 and d["boot"] == 7
        assert d["fn"] == {
            "energy": protocol.decode(FUNCS["energy"], en),
            "water": protocol.decode(FUNCS["water"], wa),
        }
        assert d["counters"] == {"solar_wh": 123.4, "load_wh": 234.5, "batt_net_wh": -34.5}
        assert d["ext"]["victron_mppt"]["pv_power"] == 87 and d["ext"]["victron_battery"] == {
            "battery_voltage": 13.05
        }
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
    assert web_decode(3, bytes.fromhex(out[0]))["fn"] == {
        "cooler": protocol.decode(FUNCS["cooler"], bytes.fromhex("0180300000000000")),
        "campingmode": protocol.decode(FUNCS["campingmode"], b"\x03"),
    }
    d4 = web_decode(4, bytes.fromhex(out[1]))
    assert (
        d4["fw"] == "fw1"
        and d4["uptime_s"] == 3600
        and d4["fn"]["general"] == protocol.decode(FUNCS["general"], GENERAL)
    )
    # port 12 (key-transfer confirmation) is passed through, not interpreted
    assert web_decode(12, b"\x00\x01\x02\x03\x04") == {"port": 12, "confirm": [0, 1, 2, 3, 4]}
    assert "error" in web_decode(3, b"\x00\x05\x01") and "error" in web_decode(9, b"\x01")


def test_decode_refuses_unknown_version(cli):
    """A version this decoder does not know gives no fields, only the version (the app says "update the app")."""
    for port, length in ((2, 50), (4, 24)):
        d = web_decode(port, bytes([99]) + bytes(length - 1))
        assert d["version"] == 99 and d["fn"] == {} and set(d) == {"port", "version", "fn"}
        assert formatter(port, bytes([99]) + bytes(length - 1))["errors"]
