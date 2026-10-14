"""sensors.c: type table, Govee + Victron decoders, seen/configured tables, window cadence, ext records.

.. test:: Sensor core (Govee, Victron)
   :id: T_FW_SENSORS_CORE
   :links: R_FW_SENSORS
"""

import json
import pathlib
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
CORE = ROOT / "firmware" / "components" / "cali_core"

GOVEE_H5075 = "88ec" + "00" + "03b8c5" + "64" + "00"  # 0xEC88 LE; 24-bit 0x03b8c5 -> 24.3 C 90.9 %, batt 100
GOVEE_H5074 = (
    "88ec" + "00" + "5c09" + "d816" + "5a" + "02"
)  # int16 0x095c=2396 -> 23.96 C; 0x16d8=5848 -> 58.48 %; 90
GOVEE_GV5179 = (
    "0100" + "0101" + "032317" + "32"
)  # company id 0x0001 LE; 24-bit 0x032317 -> 20.5 C 59.1 %, batt 50 (a live van advert)


@pytest.fixture(scope="module")
def cli(tmp_path_factory):
    out = tmp_path_factory.mktemp("b") / "sensors_cli"
    subprocess.run(
        ["cc", "-std=c99", "-Wall", "-Wextra", "-Werror", "-I", str(CORE / "include"),
         "-I", str(ROOT / "firmware/components/platform/include"), "-I", str(ROOT / "csrc"),
         str(CORE / "sensors.c"), str(CORE / "json.c"), str(CORE / "diag.c"),
         str(ROOT / "firmware/components/platform/host/platform_host.c"),
         str(CORE / "test/sensors_cli.c"), "-o", str(out), "-lm"],
        check=True,
    )  # fmt: skip
    return out


def run(cli, tmp_path, lines):
    p = subprocess.run(
        [str(cli), str(tmp_path)], input="\n".join(lines) + "\n", capture_output=True, text=True, check=True
    )
    return p.stdout.splitlines()


def test_govee_24bit_layout_decodes(cli, tmp_path):
    out = run(cli, tmp_path, ["init", f"adv A4:C1:38:00:00:01 1 -60 GVH5075_0001 {GOVEE_H5075}", "api 1000"])
    api = json.loads(out[-1][4:])
    assert api["seen"] == [{"id": api["seen"][0]["id"], "type": "govee", "model": "GVH5075", "rssi": -60}]
    assert api["configured"] == []


def test_govee_24bit_values_after_add(cli, tmp_path):
    out = run(cli, tmp_path, ["init", f"adv A4:C1:38:00:00:01 1 -60 GVH5075_0001 {GOVEE_H5075}", "api 1000"])
    sid = json.loads(out[-1][4:])["seen"][0]["id"]
    out = run(cli, tmp_path, ["init", f"adv A4:C1:38:00:00:01 1 -60 GVH5075_0001 {GOVEE_H5075}",
                              f"add {sid} - -", f"adv A4:C1:38:00:00:01 1 -60 GVH5075_0001 {GOVEE_H5075}", "state 0"])  # fmt: skip
    assert json.loads(out[-1][4:]) == {
        "govee": {"temp_c": 24.3, "humidity": 90.9, "battery": 100, "age_s": 0}
    }


def test_govee_7byte_layout_and_reading_after_add(cli, tmp_path):
    out = run(cli, tmp_path, ["init", f"adv A4:C1:38:00:00:02 1 -70 GVH5074_0002 {GOVEE_H5074}", "api 1000"])
    sid = json.loads(out[-1][4:])["seen"][0]["id"]
    out = run(cli, tmp_path, ["init", f"adv A4:C1:38:00:00:02 1 -70 GVH5074_0002 {GOVEE_H5074}",
                              f"add {sid} - -", f"adv A4:C1:38:00:00:02 1 -70 GVH5074_0002 {GOVEE_H5074}", "state 2000"])  # fmt: skip
    assert any(
        line.startswith(
            'REC "ev":"ext","dev":"govee","type":"govee","temp_c":23.96,"humidity":58.48,"battery":90'
        )
        for line in out
    )
    ext = json.loads(out[-1][4:])
    assert ext == {"govee": {"temp_c": 23.96, "humidity": 58.48, "battery": 90, "age_s": 2}}


def test_configured_list_persists_and_ids_are_boot_local(cli, tmp_path):
    out = run(cli, tmp_path, ["init", f"adv A4:C1:38:00:00:02 1 -70 GVH5074_0002 {GOVEE_H5074}", "api 1000"])
    sid = json.loads(out[-1][4:])["seen"][0]["id"]
    run(
        cli,
        tmp_path,
        ["init", f"adv A4:C1:38:00:00:02 1 -70 GVH5074_0002 {GOVEE_H5074}", f"add {sid} kitchen -"],
    )
    out = run(cli, tmp_path, ["init", "api 1000"])  # a new process = a new boot: the list is back
    api = json.loads(out[-1][4:])
    assert [c["alias"] for c in api["configured"]] == ["kitchen"] and api["configured"][0]["value"] is None
    assert api["configured"][0]["id"] == sid  # same address -> same id (crc of the address)


def test_add_rename_remove_rules(cli, tmp_path):
    a1 = f"adv A4:C1:38:00:00:01 1 -60 GVH5075_0001 {GOVEE_H5075}"
    a2 = f"adv A4:C1:38:00:00:02 1 -70 GVH5074_0002 {GOVEE_H5074}"
    out = run(cli, tmp_path, ["init", a1, a2, "api 1000"])
    ids = [s["id"] for s in json.loads(out[-1][4:])["seen"]]
    out = run(cli, tmp_path, ["init", a1, a2, f"add {ids[0]} - -", f"add {ids[1]} - -", f"add {ids[1]} Bad! -",
                              f"add {ids[1]} {'x' * 17} -", "add 2 x -", f"add {ids[1]} hall -",
                              f"rename {ids[1]} govee", f"rename {ids[1]} porch", f"remove {ids[0]}", "remove 2",
                              "api 1000"])  # fmt: skip
    rcs = [int(x.split()[1]) for x in out if x.startswith("OK ")][3:]  # after init + 2 adverts
    # add ok | default alias taken | bad alias | too long | unknown id | add ok | rename to a taken alias |
    # rename ok | remove ok | remove unknown
    assert rcs == [0, -1, -1, -1, -4, 0, -1, 0, 0, -4]
    assert [c["alias"] for c in json.loads(out[-1][4:])["configured"]] == ["porch"]


def test_unknown_mfr_id_is_ignored_and_seen_table_caps_at_16(cli, tmp_path):
    advs = [f"adv A4:C1:38:00:{i:02X}:01 1 -60 GVH5075_{i:04X} {GOVEE_H5075}" for i in range(20)]
    out = run(cli, tmp_path, ["init", "adv 11:22:33:44:55:66 1 -50 Other 4c000215aabb", *advs, "api 1000"])
    assert len(json.loads(out[-1][4:])["seen"]) == 16


def test_ext_record_rate_limited_to_one_per_minute(cli, tmp_path):
    out = run(cli, tmp_path, ["init", f"adv A4:C1:38:00:00:02 1 -70 GVH5074_0002 {GOVEE_H5074}", "api 1000"])
    sid = json.loads(out[-1][4:])["seen"][0]["id"]
    a = f"adv A4:C1:38:00:00:02 1 -70 GVH5074_0002 {GOVEE_H5074}"
    out = run(cli, tmp_path, ["init", a, f"add {sid} - -", "now 1000", a, "now 30000", a, "now 61001", a])
    assert sum(line.startswith("REC ") for line in out) == 2


def test_window_cadence_and_pause(cli, tmp_path):
    out = run(cli, tmp_path, ["init", "tick 100 1 0", "tick 30000 1 0", "tick 60100 1 0", "tick 61000 1 1",
                              "tick 120200 1 1", "tick 120300 0 0", "tick 180400 1 0"])  # fmt: skip
    assert [line for line in out if line.startswith("WINDOW")] == ["WINDOW 5000"] * 3  # t=100, 60100, 180400
    # paused while pairing, nothing while disabled


def test_kick_opens_a_window_at_once_unless_one_ran_recently(cli, tmp_path):
    out = run(
        cli,
        tmp_path,
        ["init", "tick 100 1 0", "kick", "tick 5000 1 0", "tick 20000 1 0", "kick", "tick 20100 1 0"],
    )
    assert [line for line in out if line.startswith("WINDOW")] == [
        "WINDOW 5000",
        "WINDOW 5000",
    ]  # t=100 and t=20100


def test_kick_survives_a_busy_transport(cli, tmp_path):
    out = run(
        cli,
        tmp_path,
        ["init", "tick 100 1 0", "kick", "busy 1", "tick 20000 1 0", "busy 0", "tick 20100 1 0"],
    )
    assert [line for line in out if line.startswith("WINDOW")] == [
        "WINDOW 5000",
        "WINDOW 5000",
    ]  # t=100, t=20100


def test_add_refuses_a_seen_row_past_the_offline_age(cli, tmp_path):
    a = f"adv A4:C1:38:00:00:02 1 -70 GVH5074_0002 {GOVEE_H5074}"
    out = run(cli, tmp_path, ["init", a, "api 0"])
    sid = json.loads(out[-1][4:])["seen"][0]["id"]
    out = run(cli, tmp_path, ["init", a, "now 600000", f"add {sid} - -", "now 0", f"add {sid} - -"])
    rcs = [line for line in out if line.startswith("OK ")][-4:]
    assert rcs == ["OK 0", "OK -4", "OK 0", "OK 0"]  # now | hidden (600 s old) -> unknown | now | added


def test_ext_record_fits_body_max(cli, tmp_path):
    # the longest body: a 16-char alias + every field of the widest type; vrec refuses an over-long one
    out = run(cli, tmp_path, ["init", "maxbody"])
    assert int(out[-1].split()[1]) < 288


# Victron Instant Readout (victron_ble 0.9.3 container). The CLI's AES stub returns the key as the
# keystream block, so "ciphertext" = plaintext XOR key; the real AES-CTR path is test_sensors_e2e.py.
KEY = bytes.fromhex("00112233445566778899aabbccddeeff")


def bits(fields):  # [(value, nbits)] LSB-first -> bytes
    v = n = 0
    for val, nb in fields:
        v |= (val & ((1 << nb) - 1)) << n
        n += nb
    return v.to_bytes((n + 7) // 8, "little")


def victron(model, mode, plain, key=KEY, iv=0x1234):
    cipher = bytes(p ^ k for p, k in zip(plain, key))  # the CLI stub's keystream = key
    return ("e102" + "10" + "00" + model.to_bytes(2, "little").hex() + bytes([mode]).hex()
            + iv.to_bytes(2, "little").hex() + bytes([key[0]]).hex() + cipher.hex())  # fmt: skip


# float, no error, 13.52 V, 2.3 A, 420 Wh, 87 W, load n/a
SOLAR = bits([(5, 8), (0, 8), (1352, 16), (23, 16), (42, 16), (87, 16), (0x1FF, 9)])
# 12.98 V, 298.15 K = 25.0 C
BSENSE = bits(
    [(0xFFFF, 16), (1298, 16), (0, 16), (29815, 16), (2, 2), (0x3FFFFF, 22), (0xFFFFF, 20), (0x3FF, 10)]
)


def test_victron_mppt_decodes(cli, tmp_path):
    adv = victron(0xA075, 0x01, SOLAR)
    out = run(cli, tmp_path, ["init", f"adv DA:4B:25:00:00:01 1 -55 SmartSolar_HQ00 {adv}", "api 1000"])
    sid = json.loads(out[-1][4:])["seen"][0]["id"]
    assert json.loads(out[-1][4:])["seen"][0]["type"] == "victron_mppt"
    out = run(cli, tmp_path, ["init", f"adv DA:4B:25:00:00:01 1 -55 SmartSolar_HQ00 {adv}", f"add {sid} - {KEY.hex()}",
                              f"adv DA:4B:25:00:00:01 1 -55 SmartSolar_HQ00 {adv}", "state 2000"])  # fmt: skip
    assert json.loads(out[-1][4:]) == {"mppt-75-15": {"battery_current": 2.3, "battery_voltage": 13.52, "charge_state": 5,
                                       "error_code": 0, "load_current": None, "pv_power": 87, "yield_today_kwh": 0.42,
                                       "age_s": 2}}  # fmt: skip


def test_victron_battery_sense_decodes(cli, tmp_path):
    adv = victron(0xA3A4, 0x02, BSENSE)
    out = run(cli, tmp_path, ["init", f"adv CB:F2:E4:00:00:01 1 -58 BatterySense_00 {adv}", "api 1000"])
    sid = json.loads(out[-1][4:])["seen"][0]["id"]
    out = run(cli, tmp_path, ["init", f"adv CB:F2:E4:00:00:01 1 -58 BatterySense_00 {adv}", f"add {sid} - {KEY.hex()}",
                              f"adv CB:F2:E4:00:00:01 1 -58 BatterySense_00 {adv}", "state 2000"])  # fmt: skip
    assert json.loads(out[-1][4:]) == {
        "battery-sense": {"battery_voltage": 12.98, "temperature_c": 25.0, "age_s": 2}
    }


def test_victron_add_needs_key_and_checks_byte(cli, tmp_path):
    adv = victron(0xA075, 0x01, SOLAR)
    out = run(cli, tmp_path, ["init", f"adv DA:4B:25:00:00:01 1 -55 SmartSolar_HQ00 {adv}", "api 1000"])
    sid = json.loads(out[-1][4:])["seen"][0]["id"]
    pre = ["init", f"adv DA:4B:25:00:00:01 1 -55 SmartSolar_HQ00 {adv}"]
    assert run(cli, tmp_path, pre + [f"add {sid} - -"])[-1] == "OK -2"  # CALI_SENS_E_KEY
    assert (
        run(cli, tmp_path, pre + [f"add {sid} - ff" + KEY.hex()[2:]])[-1] == "OK -3"
    )  # CALI_SENS_E_KEY_MISMATCH
    assert run(cli, tmp_path, pre + [f"add {sid} - {KEY.hex()}"])[-1] == "OK 0"


def test_victron_non_product_record_is_ignored(cli, tmp_path):
    # a Victron also emits a record whose prefix byte is not 0x10 (no Instant Readout): never a seen row, never a
    # decode. (A key with the right first byte but wrong rest decodes to garbage — the key-check byte is the only
    # defence the protocol offers; the owner compares the first values with VictronConnect, see the board steps.)
    adv = "e102" + "20" + "00" + "75a0" + "01" + "3412" + "00" + "aa"
    out = run(cli, tmp_path, ["init", f"adv DA:4B:25:00:00:01 1 -55 SmartSolar_HQ00 {adv}", "api 1000"])
    assert json.loads(out[-1][4:])["seen"] == []


def _seen(cli, tmp_path, adv):
    out = run(cli, tmp_path, ["init", f"adv CB:F2:E4:00:00:01 1 -58 BMV_00 {adv}", "api 1000"])
    return json.loads(out[-1][4:])["seen"]


def test_victron_battery_monitor_by_mode_and_unknown_mode_ignored(cli, tmp_path):
    assert (
        _seen(cli, tmp_path, victron(0xA389, 0x02, BSENSE))[0]["type"] == "victron_battery"
    )  # not A3A4: mode 2
    assert _seen(cli, tmp_path, victron(0xA389, 0x03, BSENSE)) == []  # prefix 0x10, unknown readout type


def test_victron_short_advert_never_decodes(cli, tmp_path):
    adv = victron(0xA075, 0x01, SOLAR[:8])  # 8 of the 12 layout bytes
    sid = _seen(cli, tmp_path, adv)[0]["id"]
    a = f"adv CB:F2:E4:00:00:01 1 -58 BMV_00 {adv}"
    out = run(cli, tmp_path, ["init", a, f"add {sid} - {KEY.hex()}", a, "state 0"])
    assert json.loads(out[-1][4:])["mppt-75-15"]["age_s"] is None and not any(
        x.startswith("REC") for x in out
    )


def test_govee_0001_gv5179_layout_decodes(cli, tmp_path):
    """The owner's GV5179 advertises under the shared company id 0x0001: a 6-byte, plausible payload is a Govee."""
    a = f"adv A4:C1:38:00:00:05 1 -59 GV5179_0005 {GOVEE_GV5179}"
    out = run(cli, tmp_path, ["init", a, "api 1000"])
    api = json.loads(out[-1][4:])
    assert api["seen"][0]["type"] == "govee" and api["seen"][0]["model"] == "GV5179"
    sid = api["seen"][0]["id"]
    out = run(cli, tmp_path, ["init", a, f"add {sid} - -", a, "state 0"])
    assert json.loads(out[-1][4:]) == {"govee": {"temp_c": 20.5, "humidity": 59.1, "battery": 50, "age_s": 0}}


def test_company_id_0001_needs_a_plausible_6_byte_payload(cli, tmp_path):
    # 0x0001 is shared: a 7-byte payload and a 6-byte one with 255 % humidity are not Govees
    out = run(
        cli,
        tmp_path,
        [
            "init",
            "adv 11:22:33:44:55:01 1 -50 Other 0100010103231732aa",
            "adv 11:22:33:44:55:02 1 -50 Other 01000101ffffffff",
            "api 1000",
        ],
    )
    assert json.loads(out[-1][4:])["seen"] == []
