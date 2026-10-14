"""tools/esplab/soak_report.py: the 7 criteria against fixture rows (no Influx, no ESP).

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
    assert [v for _, v, _ in lines] == ["PASS", "PASS", "PASS", "PASS", "PASS", "SKIP", "PASS"]
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


def test_i2c_timeouts_per_day():
    lines = [{"t": T0 + i * H, "msg": "i2c.master: I2C transaction timeout"} for i in range(7)]
    assert by_name(soak.evaluate(data(i2c_lines=lines)))["i2c_timeouts"][0] == "FAIL"  # 7 in 3 days > 2/day
    assert by_name(soak.evaluate(data(i2c_lines=lines[:6])))["i2c_timeouts"][0] == "PASS"


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
