"""sensors.c: type table, Govee decoder, seen/configured tables, window cadence, ext records.

.. test:: Sensor core (Govee)
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
        l.startswith(
            'REC "ev":"ext","dev":"govee","type":"govee","temp_c":23.96,"humidity":58.48,"battery":90'
        )
        for l in out
    )  # noqa: E741
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
