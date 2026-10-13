"""Status display model: rows, stale rule, footer, brightness (#154).

.. test:: Display model maps every status to the right row colour, text, footer and brightness
   :id: T_FW_DISPLAY_MODEL
   :links: R_FW_STATUS_DISPLAY
"""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CORE = ROOT / "firmware/components/cali_core"


@pytest.fixture(scope="module")
def cli(tmp_path_factory):
    cc = shutil.which("cc") or pytest.skip("no C compiler")
    out = tmp_path_factory.mktemp("dm") / "display_cli"
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
            str(ROOT / "firmware/web"),
            str(CORE / "display_model.c"),
            str(CORE / "test/display_cli.c"),
            "-o",
            str(out),
            "-lm",
        ],
        check=True,
    )
    return out


def run(cli, *lines):
    out = (
        subprocess.run([str(cli)], input="\n".join(lines) + "\n", capture_output=True, text=True, check=True)
        .stdout.strip()
        .splitlines()
    )
    return [dict(f.split("=", 1) for f in line.split(" ;; ")) for line in out]


def run_raw(cli, *lines):
    return (
        subprocess.run([str(cli)], input="\n".join(lines) + "\n", capture_output=True, text=True, check=True)
        .stdout.strip()
        .splitlines()
    )


BASE = (
    "pair=6 addr=AA:BB:CC:DD:EE:FF link=1 age=800 wmode=2 wstate=3 ssid=Insel "
    "ip=3232281174 rssi=-58 fail= up=7980000"
)  # 192.168.178.86, 2 h 13 min


def test_all_green_online(cli):
    (v,) = run(cli, BASE + " now=7980000 lang=0")
    assert v["device"] == "green|läuft · seit 2 h 13 min"
    assert v["wifi"] == "green|Insel · 192.168.178.86 · -58 dBm"
    assert v["camper"] == "green|verbunden · Daten vor 1 s"
    assert v["setup"] == "0" and v["footer"] == "http://calictl-esp.local"


def test_wifi_unknown_rssi_is_left_out(cli):  # rssi 0 = unknown (null in /api/state), never "0 dBm"
    (v,) = run(cli, BASE.replace("rssi=-58", "rssi=0") + " now=1 lang=0")
    assert v["wifi"] == "green|Insel · 192.168.178.86"


def test_camper_link_up_no_data_yet(cli):  # Review Focus 2
    (v,) = run(cli, BASE.replace("age=800", "age=-1") + " now=1000 lang=0")
    assert v["camper"].startswith("amber|")


def test_camper_stale_after_90s(cli):
    (v,) = run(cli, BASE.replace("age=800", "age=90001") + " now=100000 lang=0")
    assert v["camper"] == "red|keine Daten seit 90 s"


def test_camper_link_lost(cli):
    (v,) = run(cli, BASE.replace("link=1", "link=0") + " now=20000 lang=0")
    assert v["camper"] == "red|Verbindung verloren, verbinde neu"


def test_camper_not_paired(cli):
    (v,) = run(
        cli, BASE.replace("pair=6 addr=AA:BB:CC:DD:EE:FF link=1", "pair=0 addr= link=0") + " now=20000 lang=0"
    )
    assert v["camper"] == "grey|nicht gekoppelt"


@pytest.mark.parametrize(
    "pair,word",
    [(1, "verbinde"), (2, "verbinde"), (4, "Kopplung"), (3, "Code"), (5, "Kopplung"), (8, "verbinde")],
)
def test_camper_pairing_in_progress_is_amber(cli, pair, word):
    (v,) = run(
        cli, BASE.replace("pair=6", "pair=%d" % pair).replace("link=1", "link=0") + " now=20000 lang=0"
    )
    assert v["camper"].startswith("amber|") and word in v["camper"]


def test_camper_pairing_error(cli):
    (v,) = run(cli, BASE.replace("pair=6", "pair=7").replace("link=1", "link=0") + " now=1 lang=0")
    assert v["camper"] == "red|Kopplung fehlgeschlagen"


def test_wifi_setup_mode_and_footer(cli):
    (v,) = run(
        cli,
        BASE.replace("wmode=2 wstate=3", "wmode=1 wstate=1")
        .replace("ssid=Insel", "ssid=")
        .replace("ip=3232281174", "ip=0")
        + " now=1 lang=0",
    )
    assert v["wifi"] == "amber|Hotspot calictl-esp-setup · 192.168.4.1"
    assert v["setup"] == "1" and v["footer"] == "WLAN calictl-esp-setup · Passwort calictl-setup"


def test_footer_english(cli):
    s, o = run(
        cli,
        BASE.replace("wmode=2 wstate=3", "wmode=1 wstate=1").replace("ip=3232281174", "ip=0")
        + " now=1 lang=1",
        BASE + " now=1 lang=1",
    )
    assert s["footer"] == "WiFi calictl-esp-setup · password calictl-setup"
    assert o["footer"] == "http://calictl-esp.local"


def test_wifi_off_with_reason(cli):
    (v,) = run(
        cli,
        BASE.replace("wmode=2 wstate=3", "wmode=0 wstate=0")
        .replace("fail=", "fail=auth")
        .replace("ip=3232281174", "ip=0")
        + " now=1 lang=0",
    )
    assert v["wifi"] == "red|nicht verbunden · falsches Passwort"


def test_wifi_joining_and_retrying(cli):
    j, r = run(
        cli,
        BASE.replace("wstate=3", "wstate=2").replace("ip=3232281174", "ip=0") + " now=1 lang=0",
        BASE.replace("wstate=3", "wstate=4").replace("ip=3232281174", "ip=0") + " now=1 lang=0",
    )
    assert j["wifi"] == "amber|verbinde mit Insel"
    assert r["wifi"] == "amber|Insel nicht erreichbar, versuche neu"


def test_wifi_off_reasons_are_translated(cli):
    base = BASE.replace("wmode=2 wstate=3", "wmode=0 wstate=0").replace("ip=3232281174", "ip=0")
    got = [
        run(cli, base.replace("fail=", "fail=" + f) + " now=1 lang=%d" % lang)[0]["wifi"]
        for f, lang in [("not_found", 0), ("other", 0), ("auth", 1)]
    ]
    assert got == [
        "red|nicht verbunden · Netz nicht gefunden",
        "red|nicht verbunden · Verbindungsfehler",
        "red|not connected · wrong password",
    ]


def test_wifi_longest_ssid_fits_the_row_buffer(cli):  # Review Focus 3 (the screen ellipsizes, not the model)
    long = "Ä" * 16  # 32 bytes UTF-8 = NET_SSID_MAX; the retrying row is then exactly 63 bytes
    (v,) = run(
        cli,
        BASE.replace("ssid=Insel", "ssid=" + long)
        .replace("wstate=3", "wstate=4")
        .replace("ip=3232281174", "ip=0")
        + " now=1 lang=0",
    )
    text = v["wifi"].split("|", 1)[1]
    assert text == long + " nicht erreichbar, versuche neu"
    assert len(text.encode()) == 63


def test_utf8_cut_backs_up_to_a_lead_byte(cli):
    # no real row puts a multi-byte char at the cut, so drive the cut itself: 62 ASCII + "Ä" (bytes 62-63)
    (out,) = run_raw(cli, "fit=" + "a" * 62 + "Ä")
    assert out == "fit=" + "a" * 62
    (out,) = run_raw(cli, "fit=" + "a" * 61 + "Ä")  # fits exactly (63 bytes): kept
    assert out == "fit=" + "a" * 61 + "Ä"


@pytest.mark.parametrize(
    "link,want", [(1, "green|verbunden · Daten vor 1 s"), (0, "red|Verbindung verloren, verbinde neu")]
)
def test_camper_bonded_after_reboot(cli, link, want):  # PAIR_IDLE with a stored address
    (v,) = run(
        cli, BASE.replace("pair=6", "pair=0").replace("link=1", "link=%d" % link) + " now=20000 lang=0"
    )
    assert v["camper"] == want


def test_stale_boundary(cli):
    a, b = run(
        cli,
        BASE.replace("age=800", "age=90000") + " now=1 lang=0",
        BASE.replace("age=800", "age=90001") + " now=1 lang=0",
    )
    assert a["camper"] == "green|verbunden · Daten vor 90 s" and b["camper"] == "red|keine Daten seit 90 s"


@pytest.mark.parametrize("ms,txt", [(61_000, "1 min"), (7_980_000, "2 h 13 min"), (273_600_000, "3 d 4 h")])
def test_device_uptime_formats(cli, ms, txt):  # Review Focus 4
    (v,) = run(cli, BASE.replace("up=7980000", "up=%d" % ms) + " now=%d lang=0" % ms)
    assert v["device"] == "green|läuft · seit " + txt


def test_english(cli):
    (v,) = run(cli, BASE + " now=7980000 lang=1")
    assert v["camper"] == "green|connected · data 1 s ago"


def test_brightness_numbers_do_not_rebrighten(cli):
    moved = (
        BASE.replace("age=800", "age=3100")
        .replace("rssi=-58", "rssi=-71")
        .replace("up=7980000", "up=8100000")
    )
    v = run(cli, "reset", BASE + " now=0 lang=0", BASE + " now=60000 lang=0", moved + " now=65000 lang=0")
    assert [x["bright"] for x in v] == ["100", "10", "10"]
    assert v[2]["camper"] == "green|verbunden · Daten vor 4 s"  # the text did change, the screen stays dim


@pytest.mark.parametrize(
    "changed",
    [
        BASE.replace("link=1", "link=0"),  # camper colour
        BASE.replace("wstate=3", "wstate=4").replace("ip=3232281174", "ip=0"),  # WiFi online -> retrying
        BASE.replace("wmode=2 wstate=3", "wmode=1 wstate=1").replace(
            "ip=3232281174", "ip=0"
        ),  # setup + footer
    ],
)
def test_brightness_bright_then_dim_then_rebright(cli, changed):
    v = run(
        cli,
        "reset",
        BASE + " now=0 lang=0",
        BASE + " now=59999 lang=0",
        BASE + " now=60000 lang=0",
        changed + " now=61000 lang=0",
    )
    assert [x["bright"] for x in v] == ["100", "100", "10", "100"]


# ---- wake + brightness (R_FW_DISPLAY_WAKE) -------------------------------------------------------


def ticks(start, end, step=200):
    return range(start, end, step)


def test_wake_timings_on_dim_off(cli):
    """on for 60 s after the last wake event, dim until 5 min, then off (backlight 0); a touch wakes.

    .. test:: Display wake model: timings, each wake source, alert-only waking, the swallowed wake tap,
       the lux curve, EMA + deadband, the sensor fallback
       :id: T_FW_DISPLAY_WAKE_MODEL
       :links: R_FW_DISPLAY_WAKE
    """
    v = run(
        cli,
        "reset",
        BASE + " now=0",
        BASE + " now=59999",
        BASE + " now=60000",
        BASE + " now=299999",
        BASE + " now=300000",
        BASE + " now=400000 touch=1",
        BASE + " now=459999",
    )
    assert [(x["state"], x["bright"]) for x in v] == [
        ("on", "100"),
        ("on", "100"),
        ("dim", "10"),
        ("dim", "10"),
        ("off", "0"),
        ("on", "100"),
        ("on", "100"),
    ]
    assert v[0]["sensor"] == "absent"


def test_alert_wakes_ordinary_change_does_not(cli):
    lost = BASE.replace("link=1", "link=0")  # camper red
    v = run(
        cli,
        "reset",
        lost + " now=0",
        BASE + " now=400000",  # red -> green: no wake
        lost + " now=400200",  # green -> red: wake
        lost + " now=800000",
        BASE.replace("wstate=3", "wstate=4").replace("ip=3232281174", "ip=0").replace("link=1", "link=0")
        + " now=800200",  # WiFi green -> amber (retrying): wake
    )
    assert [x["state"] for x in v] == ["on", "off", "on", "off", "on"]


def test_parked_refresh_sawtooth_never_wakes(cli):
    """A parked unit refreshes the snapshot only by the 30 s water re-read (CALI_SESSION_WATER_REREAD_MS),
    so the data age saws 2 s -> ~32 s -> 2 s. That normal gap is not "stale": with no other input
    the screen dims after 60 s and is off after 5 min (live 2026-10-13: it stayed on for 15 min).

    .. test:: The parked snapshot-age sawtooth neither turns the camper row red nor wakes the screen
       :id: T_FW_DISPLAY_PARKED_SAWTOOTH
       :links: R_FW_DISPLAY_WAKE
    """
    lines = ["reset"] + [
        BASE.replace("age=800", "age=%d" % (2000 + t % 30000)) + " now=%d" % t for t in range(0, 360_000, 500)
    ]
    v = run(cli, *lines)
    assert {x["camper"].split("|")[0] for x in v} == {"green"}
    assert v[59_000 // 500]["state"] == "on"
    assert v[61_000 // 500]["state"] == "dim"
    assert v[301_000 // 500]["state"] == v[-1]["state"] == "off"


def test_hand_near_wakes_on_two_reads_with_hysteresis(cli):
    off = BASE + " now=%d"
    v = run(
        cli,
        "reset",
        off % 0,
        (off % 400000) + " prox=900",  # one read above NEAR: not yet
        (off % 400200) + " prox=40",
        (off % 400400) + " prox=900",
        (off % 400600) + " prox=900",  # the second read in a row: wake
        (off % 800000) + " prox=900",  # still near (a cup in front): no new wake, so off again
        (off % 800200) + " prox=180",  # between FAR and NEAR: stays near
        (off % 800400) + " prox=900",
        (off % 800600) + " prox=900",
        (off % 800800) + " prox=100",  # far
        (off % 801000) + " prox=900",
        (off % 801200) + " prox=900",  # near again: wake
    )
    assert [x["state"] for x in v] == [
        "on",
        "off",
        "off",
        "off",
        "on",
        "off",
        "off",
        "off",
        "off",
        "off",
        "off",
        "on",
    ]


def test_hand_near_ignored_when_prox_wake_off(cli):
    v = run(
        cli, "reset", BASE + " now=0", *(BASE + " now=%d prox=900 pw=0" % t for t in (400000, 400200, 400400))
    )
    assert v[-1]["state"] == "off"


def motion_states(cli, accs, start=400000, extra=""):
    """reset, wake at 0, then one tick per 200 ms from ``start`` with each acc vector (None = no reading)."""
    lines = ["reset", BASE + " now=0"]
    for i, a in enumerate(accs):
        acc = "" if a is None else " acc=%d,%d,%d" % a
        lines.append(BASE + " now=%d%s%s" % (start + 200 * i, acc, extra))
    return run(cli, *lines)[1:]


STILL = (12, -8, 1000)  # flat on a table, 1 g on z
TILT = (450, -8, 890)  # picked up: ~27 deg, the vector moves ~450 mg
LIFT1, LIFT2 = (300, -8, 955), (600, -8, 800)  # a pick-up across reads: ~290 / ~340 mg steps


def test_motion_wakes_on_two_reads_in_a_row(cli):
    """A pick-up (the accel vector changing by more than DISPLAY_MOTION_WAKE_MG between two reads, two
    reads in a row) wakes a dark screen; one step alone (moved, then held) does not.

    .. test:: Motion on the BMI270 wakes the screen on two reads in a row over the threshold
       :id: T_FW_DISPLAY_MOTION_WAKE
       :links: R_FW_DISPLAY_WAKE
    """
    v = motion_states(cli, [STILL, STILL, LIFT1, LIFT1, LIFT1, LIFT2, STILL])
    assert [x["state"] for x in v] == ["off", "off", "off", "off", "off", "off", "on"]
    assert v[0]["motion"] == "-1" and int(v[1]["motion"]) == 0 and 280 < int(v[2]["motion"]) < 300


def test_still_sensor_and_van_rocking_never_wake(cli):
    noise = [(12 + i % 3, -8 - i % 2, 1000 + (i % 5) * 10) for i in range(50)]  # sensor noise
    rock = [(12, -8 + (60 if i % 2 else -60), 1000) for i in range(50)]  # ~120 mg sway each read
    assert {x["state"] for x in motion_states(cli, noise)} == {"off"}
    assert {x["state"] for x in motion_states(cli, rock)} == {"off"}


def test_continuous_shaking_does_not_keep_the_screen_on(cli):
    """Driving / a running engine: every read over the threshold for 10 min — one wake (the rising
    edge), then dim and off like any other wake; re-armed only once it was still for
    DISPLAY_MOTION_REARM_MS."""
    shake = [STILL if i % 2 else TILT for i in range(3000)]  # 10 min of 200 ms reads
    st = [x["state"] for x in motion_states(cli, shake + [STILL] * 60 + [TILT, STILL])]
    assert st[1:3] == ["off", "on"]  # the second shaking read in a row woke it
    assert st[2 + 305] == "dim" and st[2 + 1505] == st[2999] == st[-2] == "off"
    assert st[-1] == "on"  # still for 12 s, then a pick-up wakes it again


def test_motion_wake_off_and_read_failures_never_wake(cli):
    assert motion_states(cli, [STILL, TILT, STILL])[-1]["state"] == "on"
    assert motion_states(cli, [STILL, TILT, STILL], extra=" mw=0")[-1]["state"] == "off"
    # a failed read in between: no comparison across it, no wake
    v = motion_states(cli, [STILL, TILT, None, STILL, None, TILT])
    assert {x["state"] for x in v} == {"off"} and v[2]["motion"] == "-1"


@pytest.mark.parametrize(
    "pressed_awake,want", [(["10", "10", "11", "00", "11", "01"], ["0", "0", "0", "1", "1", "1"])]
)
def test_wake_tap_is_swallowed_until_release(cli, pressed_awake, want):
    # a press on a dark screen is swallowed for its whole length, even once the screen is on; the next
    # press on a lit screen reaches the widgets
    assert [ln.split("=")[1] for ln in run_raw(cli, *("gate=" + g for g in pressed_awake))] == want


def bright_at(cli, *lux, **cfg):
    extra = "".join(" %s=%s" % kv for kv in cfg.items())
    return [
        x["bright"] for x in run(cli, *("reset\n" + BASE + " now=0 als=1 lux=%d" % lx + extra for lx in lux))
    ]


def test_lux_curve_endpoints_and_monotonic(cli):
    got = [int(b) for b in bright_at(cli, 0, 1, 3, 10, 31, 100, 316, 999, 1000, 64000)]
    assert got[:2] == [8, 8] and got[-2:] == [100, 100]
    assert got == sorted(got) and len(set(got)) == 7
    assert got[5] == 69  # log curve: 100 lux = 2/3 of the way from 8 to 100
    assert [int(b) for b in bright_at(cli, 1, 1000, min=20, max=60)] == [20, 60]


def test_dim_level_is_30_pct_of_on_with_a_floor(cli):
    v = run(cli, "reset", BASE + " now=0 als=1 lux=1000", BASE + " now=60000 als=1 lux=1000")
    assert [x["bright"] for x in v] == ["100", "30"]
    v = run(cli, "reset", BASE + " now=0 als=1 lux=0", BASE + " now=60000 als=1 lux=0")
    assert [x["bright"] for x in v] == ["8", "3"]


def test_auto_off_uses_max_even_without_a_sensor(cli):
    assert bright_at(cli, 1, auto=0, max=70) == ["70"]
    (v,) = run(cli, "reset", BASE + " now=0 auto=0 max=70")  # no sensor at all
    assert (v["bright"], v["sensor"]) == ("70", "absent")


def test_headlight_burst_does_not_change_the_level_sustained_light_does(cli):
    lines = ["reset"] + [BASE + " now=%d touch=1 als=1 lux=50" % t for t in ticks(0, 10000)]
    lines += [BASE + " now=%d touch=1 als=1 lux=200" % t for t in ticks(10000, 11000)]  # 1 s, 4x brighter
    lines += [BASE + " now=%d touch=1 als=1 lux=50" % t for t in ticks(11000, 20000)]
    v = run(cli, *lines)
    assert {x["bright"] for x in v} == {v[0]["bright"]}  # the applied level never moved
    lines = ["reset"] + [BASE + " now=%d touch=1 als=1 lux=50" % t for t in ticks(0, 10000)]
    lines += [BASE + " now=%d touch=1 als=1 lux=1000" % t for t in ticks(10000, 20000)]
    v = run(cli, *lines)
    assert int(v[0]["bright"]) < 85 <= int(v[-1]["bright"])  # within the 15 % deadband of the 100 % target
    changes = [i for i in range(1, len(v)) if v[i]["bright"] != v[i - 1]["bright"]]
    assert changes[0] > 50  # not on the first bright read: smoothed
    assert all(
        abs(int(v[i]["bright"]) - int(v[i - 1]["bright"])) * 100 > 15 * int(v[i - 1]["bright"])
        for i in changes
    )


def test_sensor_failure_falls_back_after_5_s(cli):
    v = run(
        cli,
        "reset",
        BASE + " now=0 touch=1 als=1 lux=1",
        BASE + " now=5000 touch=1 als=1 lux=-1",
        BASE + " now=5001 touch=1 als=1 lux=-1",
        BASE + " now=5200 touch=1 als=1 lux=1",  # readings back: the curve again
    )
    assert [(x["bright"], x["sensor"]) for x in v] == [
        ("8", "ok"),
        ("8", "ok"),
        ("100", "error"),
        ("8", "ok"),
    ]
    (v,) = run(
        cli, "reset", BASE + " now=6000 als=1"
    )  # present but never a valid reading: boot grace, then error
    assert v["sensor"] == "ok" and v["bright"] == "100"


def test_offset_shifts_the_level_clamps_and_reaches_dim(cli):
    """The owner's brightness offset (disp_off): added to the curve's on level, clamped to min..max,
    dim = 30 % of the shifted level; on the fixed max with auto off too."""
    assert bright_at(cli, 100, off=10) == ["79"]  # 69 + 10
    assert bright_at(cli, 100, off=-50) == ["19"]  # 69 - 50
    assert bright_at(cli, 1000, off=20) == ["100"]  # clamped to max
    assert bright_at(cli, 1, off=-50) == ["8"]  # clamped to min
    assert bright_at(cli, 1, auto=0, off=-30) == ["70"]  # manual: max 100 - 30
    v = run(cli, "reset", BASE + " now=0 als=1 lux=1000 off=-40", BASE + " now=60000 als=1 lux=1000 off=-40")
    assert [x["bright"] for x in v] == ["60", "18"]
    # a changed offset applies on the next tick, past the deadband
    v = run(cli, "reset", BASE + " now=0 als=1 lux=100", BASE + " now=200 als=1 lux=100 off=5")
    assert [x["bright"] for x in v] == ["69", "74"]


URL = "http://calictl-esp.local"


@pytest.mark.parametrize(
    "batt,de,en",
    [
        ("", URL, URL),  # no power chip (host, QEMU): the URL alone
        ("bpct=87 usb=1 chg=1", URL + " · USB 87 % lädt", URL + " · USB 87 % charging"),
        ("bpct=100 usb=1 chg=0", URL + " · USB 100 %", URL + " · USB 100 %"),
        ("bpct=64 usb=0 chg=0", URL + " · Akku 64 %", URL + " · Batt 64 %"),  # no estimate yet
        ("bpct=64 usb=0 chg=0 brt=130", URL + " · Akku 64 % ~2 h 10", URL + " · Batt 64 % ~2 h 10"),
        ("bpct=64 usb=0 chg=0 brt=65", URL + " · Akku 64 % ~1 h 05", URL + " · Batt 64 % ~1 h 05"),
        ("bpct=9 usb=0 chg=0 brt=45", URL + " · Akku 9 % ~45 min", URL + " · Batt 9 % ~45 min"),
        ("usb=1 chg=1", URL, URL),  # percent unknown: no battery text
    ],
)
def test_station_footer_shows_the_battery(cli, batt, de, en):
    """The battery sits in the station footer only (R_FW_BATTERY), EN + DE.

    .. test:: The station footer shows the battery: USB / charging / percent / runtime, EN + DE
       :id: T_FW_BATTERY_FOOTER
       :links: R_FW_BATTERY
    """
    v_de, v_en = run(cli, BASE + " now=1 lang=0 " + batt, BASE + " now=1 lang=1 " + batt)
    assert v_de["footer"] == de and v_en["footer"] == en


def test_setup_footer_ignores_the_battery(cli):
    (v,) = run(cli, "wmode=1 now=1 lang=1 bpct=50 usb=0 chg=0 brt=90")
    assert v["setup"] == "1" and "Batt" not in v["footer"] and "%" not in v["footer"]


def test_battery_changes_never_wake_the_screen(cli):
    # on, then idle into "off"; a battery change (USB pulled, percent and estimate moving) keeps it off
    out = run(
        cli,
        "reset",
        BASE + " now=0 bpct=90 usb=1 chg=1",
        BASE + " now=400000 bpct=90 usb=1 chg=1",
        BASE + " now=401000 bpct=80 usb=0 chg=0 brt=95",
        BASE + " now=402000 bpct=5 usb=0 chg=0 brt=9",
    )
    assert out[0]["state"] == "on" and [v["state"] for v in out[1:]] == ["off"] * 3
    assert out[-1]["footer"].endswith("Akku 5 % ~9 min")
