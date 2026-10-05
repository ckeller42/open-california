"""tools/app_parity.py on a fixture recording: the lifecycle table (one section per connection)
and the --live screen comparison."""

import json
from pathlib import Path

from calictl import overrides, protocol
from calictl.trace import char_short
from tools import app_parity

HERE = Path(__file__).resolve().parent
BASE = json.loads((HERE / "scenarios" / "firmware" / "baseline-0410.json").read_text())["raw_frames_hex"]
HEADER = {"recorded_by": "app 5.0.8.3028", "date": "2026-10-02", "avd": "lab34", "scenario": "session"}


def _e(conn, t0, t_ms, ev, **kw):
    return {"t": t0 + t_ms / 1000, "ev": ev, "conn": conn, "t_ms": t_ms, **kw}


def _session(tmp_path):
    c1 = [
        _e(1, 100.0, 0, "connect"),
        _e(1, 100.0, 50, "mtu", mtu=247),
        _e(1, 100.0, 100, "read", char="1002", fn=None, hex="<vin-hash>"),
        _e(1, 100.0, 150, "read", char="1001", fn="general", hex=BASE["general"]),
        _e(1, 100.0, 200, "pair", state="passkey_shown"),
        _e(1, 100.0, 1500, "pair", state="bonded"),
        _e(1, 100.0, 1600, "read", char="1004", fn="vehicle", hex=BASE["vehicle"]),
        _e(1, 100.0, 2000, "subscribe", char="1102", fn="cooler", hex="0100"),
        _e(1, 100.0, 2100, "subscribe", char="1202", fn="campingmode", hex="0100"),
        _e(1, 100.0, 2200, "write", char="1003", fn="heartbeat", hex="00000000"),
        _e(1, 100.0, 2950, "write", char="1003", fn="heartbeat", hex="00000001"),
        _e(1, 100.0, 3700, "write", char="1003", fn="heartbeat", hex="00000002"),
        _e(1, 100.0, 4600, "read", char="1602", fn="energy", hex=BASE["energy"]),
        _e(1, 100.0, 30000, "disconnect", reason=19),
    ]
    c2 = [_e(2, 134.0, 0, "connect"), _e(2, 134.0, 900, "disconnect", reason=19)]
    p = tmp_path / "session.jsonl"
    p.write_text("".join(json.dumps(r) + "\n" for r in [HEADER, *c1, *c2]))
    return p


def test_lifecycle_has_a_section_per_connection_and_the_reconnect_gap(tmp_path):
    out = app_parity.render(_session(tmp_path))
    assert "## connection 1" in out and "## connection 2" in out
    assert (
        "| reconnect gap s | 4 | 5 | 1 | differs |" in out
    )  # calictl 5 s is within 20 %, the ESP's 1 s is not


def test_lifecycle_rows_compare_the_app_with_both_runtimes(tmp_path):
    from tools.capture_diff import load_recording

    _, events = load_recording(_session(tmp_path))
    (c1, _c2) = app_parity.connections(events)
    rows = {
        r[0]: r[1:] for r in app_parity.lifecycle_rows(c1, app_parity.calictl_side(), app_parity.esp_side())
    }
    assert rows["MTU"] == ("247", "-", "-", "n/a")
    assert rows["reads before subscribe"] == ("1002,1001,1004", "1001,1004", "(none)", "differs")
    assert rows["reads 1002"] == ("yes", "no", "no", "differs")
    assert rows["pairing"] == ("passkey_shown,bonded", "-", "-", "n/a")
    assert rows["heartbeat starts"] == (
        "after subscribes",
        "after subscribes",
        "before subscribes",
        "differs",
    )
    assert rows["heartbeat period ms"] == ("750", "600", "600", "differs")
    assert rows["warm-up before reads ms"] == ("2500", "2000", "2000", "differs")
    assert rows["disconnect reason"] == ("19", "-", "-", "n/a")


def test_esp_side_reads_the_compiled_headers():
    funcs = protocol.load()
    overrides.apply(funcs)
    esp, cal = app_parity.esp_side(), app_parity.calictl_side()
    # CODEC_CHARS = every function with a state char, sorted by name (tools.gen_c_dict.generate_chars)
    assert esp["reads"] == [char_short(funcs[n].state_char) for n in sorted(funcs) if funcs[n].state_char]
    assert esp["subscribes"] == esp["reads"]
    assert esp["backoff_s"][0] < esp["backoff_s"][-1]
    assert (
        esp["beat_ms"] == cal["beat_ms"] and esp["warmup_ms"] == cal["warmup_ms"]
    )  # one source (codec_chars.h)
    assert cal["backoff_s"] == [5, 10, 30, 60]


def test_live_flags_units_seen_on_one_side_only(tmp_path):
    funcs = protocol.load()
    overrides.apply(funcs)
    heater = protocol.decode(funcs["airheater"], bytes.fromhex(BASE["airheater"]))
    rows = [
        HEADER | {"scenario": "airheater"},
        {"t": 1.0, "ev": "step", "n": 1, "verb": "xy", "arg": ["heater_runtime_60"]},
        {
            "t": 2.0,
            "ev": "app_screen",
            "step": 1,
            "texts": ["Active • 60 min remaining", "Heating Temperature"],
        },
        {
            "t": 2.1,
            "ev": "esp_state",
            "step": 1,
            "screen": "airheater",
            "fn": {"airheater": heater},
            "link": {"up": True},
            "pairing": "bonded",
            "error": None,
            "texts": ["Run time", "45 min"],
        },
    ]
    p = tmp_path / "airheater.jsonl"
    p.write_text("".join(json.dumps(r) + "\n" for r in rows))
    from tools.capture_diff import load_recording

    (row,) = app_parity.live_rows(load_recording(p)[1])
    assert row["only_app"] == ["60min"] and row["only_satellite"] == ["45min"]
    assert row["interpreted"]  # semantics.interpret of the ESP's raw airheater fields
    assert "| 1 | xy heater_runtime_60 |" in app_parity.render_live(p)


def test_cli_prints_the_table(tmp_path, capsys):
    assert app_parity.main([str(_session(tmp_path))]) == 0
    assert "| step | app | calictl | ESP | verdict |" in capsys.readouterr().out
