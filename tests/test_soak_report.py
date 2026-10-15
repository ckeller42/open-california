"""tools/esplab/soak_report.py: the 10 criteria against fixture rows (no Influx, no ESP).

.. test:: Soak criteria and verdict
   :id: T_SOAK_REPORT
   :links: R_SOAK_REPORT
"""

import importlib.util
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("soak_report", ROOT / "tools" / "esplab" / "soak_report.py")
soak = importlib.util.module_from_spec(spec)
spec.loader.exec_module(soak)

H = 3_600_000
T0 = 1_789_639_000_000


def data(**kw):
    base = dict(
        start_ms=T0,
        end_ms=T0 + 72 * H,
        boots=[{"t": T0 + 1000, "reset_reason": "sw", "fw": "8f9d591"}],
        link_down=[],
        diag=[{"t": T0 + i * 60_000, "heap_min": 40_000, "link_up": 1} for i in range(10)],
        touch=[],
        ingest=[{"lost": 0, "held": 0}],
        i2c_lines=[],
        fw="8f9d591",
        boot=46,
    )
    base.update(kw)
    return soak.SoakData(**base)


def by_name(lines):
    return {n: (v, d) for n, v, d in lines}


def test_all_green():
    lines = soak.evaluate(data())
    assert [v for _, v, _ in lines] == [
        "PASS",
        "PASS",
        "PASS",
        "PASS",
        "PASS",
        "PASS",
        "PASS",
        "SKIP",
        "PASS",
        "SKIP",  # sensors: nothing comparable in the default fixture
    ]
    assert soak.verdict(lines) == "PASS"


@pytest.mark.parametrize("reason", ["panic", "int_wdt", "task_wdt", "wdt", "brownout", "cpu_lockup"])
def test_bad_reboot_fails(reason):
    d = data(
        boots=[
            {"t": T0 + 1000, "reset_reason": "sw", "fw": "x"},
            {"t": T0 + 5 * H, "reset_reason": reason, "fw": "x"},
        ]
    )
    v, detail = by_name(soak.evaluate(d))["reboots"]
    assert v == "FAIL" and reason in detail


def test_sw_reboot_is_listed_not_failed():
    v, detail = by_name(soak.evaluate(data()))["reboots"]
    assert v == "PASS" and "sw" in detail


def test_link_down_per_boot():
    downs = [{"t": T0 + 2 * H + i * 1000} for i in range(11)]
    assert by_name(soak.evaluate(data(link_down=downs)))["link"][0] == "FAIL"
    # 11 drops split over two boots: 6 + 5 -> PASS
    boots = [
        {"t": T0 + 1000, "reset_reason": "sw", "fw": "x"},
        {"t": T0 + 2 * H + 6500, "reset_reason": "sw", "fw": "x"},
    ]
    assert by_name(soak.evaluate(data(link_down=downs, boots=boots)))["link"][0] == "PASS"


def test_touch_nacks_per_hour_per_boot():
    # boot 1 lasts 2 h and reaches errors=12 (6/h) -> FAIL; boot 2 lasts 70 h with 20 errors -> fine
    boots = [{"t": T0, "reset_reason": "sw", "fw": "x"}, {"t": T0 + 2 * H, "reset_reason": "sw", "fw": "x"}]
    touch = [{"t": T0 + H, "errors": 12}, {"t": T0 + 50 * H, "errors": 20}]
    v, detail = by_name(soak.evaluate(data(boots=boots, touch=touch)))["touch_nacks"]
    assert v == "FAIL" and "6.0/h" in detail
    touch = [{"t": T0 + H, "errors": 9}, {"t": T0 + 50 * H, "errors": 20}]
    assert by_name(soak.evaluate(data(boots=boots, touch=touch)))["touch_nacks"][0] == "PASS"


def test_ingest_lost_or_held_fails_and_missing_skips():
    assert by_name(soak.evaluate(data(ingest=[{"lost": 3, "held": 0}])))["ingest"][0] == "FAIL"
    assert by_name(soak.evaluate(data(ingest=[{"lost": 0, "held": 2}])))["ingest"][0] == "FAIL"
    assert (
        by_name(soak.evaluate(data(ingest=[{"lost": 0, "held": 2}, {"lost": 0, "held": 0}])))["ingest"][0]
        == "PASS"
    )
    assert by_name(soak.evaluate(data(ingest=None)))["ingest"][0] == "SKIP"


def test_heap_min_threshold():
    diag = [{"t": T0, "heap_min": 32768, "link_up": 1}, {"t": T0 + 60_000, "heap_min": 32767, "link_up": 1}]
    v, detail = by_name(soak.evaluate(data(diag=diag)))["heap_min"]
    assert v == "FAIL" and "32767" in detail


def _i2c(*hours):
    return [{"t": T0 + int(h * H), "msg": "i2c.master: I2C transaction timeout"} for h in hours]


def test_i2c_timeouts_per_day():
    assert (
        by_name(soak.evaluate(data(i2c_lines=_i2c(1, 2, 3, 4, 5, 6))))["i2c_timeouts"][0] == "FAIL"
    )  # burst
    assert by_name(soak.evaluate(data(i2c_lines=_i2c(1, 2, 25, 26, 49, 50))))["i2c_timeouts"][0] == "PASS"
    v, detail = by_name(soak.evaluate(data(i2c_lines=_i2c(1, 2, 3))))["i2c_timeouts"]
    assert v == "FAIL" and "worst day 3" in detail


def test_empty_window_fails():
    lines = soak.evaluate(data(diag=[]))
    assert by_name(lines)["telemetry"][0] == "FAIL" and soak.verdict(lines) == "FAIL"


def test_since_without_offset_is_utc():
    assert soak._since_ms("2026-10-14T00:00") == soak._since_ms("2026-10-14T00:00:00+00:00")
    assert soak._since_ms("2026-10-14T02:00+02:00") == soak._since_ms("2026-10-14T00:00")


def test_report_text_and_point(capsys):
    lines = soak.evaluate(data())
    text = soak.render(data(), lines)
    assert text.splitlines()[0].startswith("soak fw=8f9d591 boot=46 window=72.0h")
    assert "PASS reboots" in text and text.rstrip().endswith("VERDICT PASS")
    p = soak.point(data(), lines)
    assert p["measurement"] == "esp_soak" and p["tags"] == {"fw": "8f9d591"}
    assert p["fields"] == {"verdict": "PASS", "fails": 0.0, "boot": 46.0}


def test_journal_summary_parser():
    out = 'Oct 14 08:00:01 buspi python[1]: INFO esp-ingest: {"pulled": 3, "held": 1, "lost": 0}\nnoise\n'
    assert soak.parse_journal(out) == [{"pulled": 3, "held": 1, "lost": 0}]


def test_journal_parser_skips_malformed_line():
    out = 'esp-ingest: {"held": 1, "lo\nesp-ingest: {"held": 0, "lost": 0}\n'
    assert soak.parse_journal(out) == [{"held": 0, "lost": 0}]


def test_fw_mid_window_install_fails():
    boots = [
        {"t": T0 + 1000, "reset_reason": "sw", "fw": "1111111"},
        {"t": T0 + 5 * H, "reset_reason": "sw", "fw": "8f9d591"},
    ]
    v, detail = by_name(soak.evaluate(data(boots=boots)))["fw"]
    assert v == "FAIL" and "1111111" in detail
    long_fw = [{"t": T0 + 1000, "reset_reason": "sw", "fw": "8f9d591aaaa"}]
    assert by_name(soak.evaluate(data(boots=long_fw)))["fw"][0] == "PASS"  # prefix compare


def test_fw_unreachable_or_no_boot_skips():
    assert by_name(soak.evaluate(data(fw="?")))["fw"][0] == "SKIP"
    assert by_name(soak.evaluate(data(boots=[])))["fw"][0] == "SKIP"


ESP_EXT = {
    "govee": {"temp_c": 20.5, "humidity": 59.1, "battery": 50, "age_s": 12},
    "mppt-75-15": {"battery_voltage": 13.52, "battery_current": 2.3, "pv_power": 87, "age_s": 30},
    "battery-sense": {"battery_voltage": 12.98, "temperature_c": 25.0, "age_s": 40},
}
BUSPI = {
    "govee": {"temp_c": 20.7, "humidity": 58.0, "battery": 50.0, "age_s": 20},
    "victron_mppt": {"battery_voltage": 13.5, "battery_current": 2.4, "age_s": 25},
    "victron_battery": {"battery_voltage": 12.99, "temperature_c": 24.8, "age_s": 50},
}


def test_sensors_match_passes():
    v, detail = by_name(soak.evaluate(data(ext=ESP_EXT, buspi_sensors=BUSPI)))["sensors"]
    assert v == "PASS" and detail.startswith("6 fields")  # 3 govee + 1 mppt voltage + 2 battery-sense


def test_sensors_pair_by_type_not_alias():
    """A renamed sensor still meets buspi's measurement: the entry's ``type`` names it."""
    ext = {
        "kitchen": {**ESP_EXT["govee"], "type": "govee"},
        "roof-mppt": {**ESP_EXT["mppt-75-15"], "type": "victron_mppt"},
    }
    v, detail = by_name(soak.evaluate(data(ext=ext, buspi_sensors=BUSPI)))["sensors"]
    # 3 govee + 1 mppt voltage; type itself is never compared
    assert v == "PASS" and detail.startswith("4 fields")


def test_sensors_voltage_delta_fails_naming_the_field():
    bad = {**BUSPI, "victron_mppt": {"battery_voltage": 13.22, "age_s": 25}}
    v, detail = by_name(soak.evaluate(data(ext=ESP_EXT, buspi_sensors=bad)))["sensors"]
    assert v == "FAIL" and "mppt-75-15.battery_voltage esp=13.52 buspi=13.22" in detail


def test_sensors_stale_or_absent_skips():
    stale_esp = {a: {**r, "age_s": 301} for a, r in ESP_EXT.items()}
    stale_buspi = {m: {**r, "age_s": 301} for m, r in BUSPI.items()}
    never = {"govee": {"temp_c": None, "humidity": None, "battery": None, "age_s": None}}
    for kw in (
        dict(ext={}, buspi_sensors=BUSPI),
        dict(ext=ESP_EXT, buspi_sensors={}),
        dict(ext=stale_esp, buspi_sensors=BUSPI),
        dict(ext=ESP_EXT, buspi_sensors=stale_buspi),
        dict(ext=never, buspi_sensors=BUSPI),
    ):
        assert by_name(soak.evaluate(data(**kw)))["sensors"][0] == "SKIP"
