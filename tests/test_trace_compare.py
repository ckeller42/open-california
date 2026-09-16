"""tools/trace_compare replays a real-unit trace through the dictionary + mock model.

.. test:: Trace compare reports round-trip, cadence and dynamics from a JSONL trace
   :id: T_TRACE_COMPARE
   :links: R_BLE_TRACE
"""
import json

from tools import trace_compare


def _ev(t, ev, char, fn, hx):
    return {"t": t, "ev": ev, "char": char, "fn": fn, "hex": hx}


def test_report_on_a_synthetic_trace(tmp_path):
    # heater running: RunningTimeinAction 60 -> 59 -> 58 one minute apart; energy pushed 3/s;
    # a vehicle ignition edge followed 2 s later by campingmode Enable=1 / master shed.
    heat = lambda rem: bytes.fromhex("1050003c0c0000")[:6] + bytes([rem])  # noqa: E731  (RunningTimeinAction = byte 6)
    evs = [_ev(1000.0, "connect", None, None, None)]
    evs[0] = {"t": 1000.0, "ev": "connect", "addr": "XX"}
    evs += [_ev(1000.0 + i / 3, "notify", "1602", "energy",
                "00d0800a0025810000fe0e3a3088ffd9fffe000001ff") for i in range(30)]
    evs += [_ev(1000.0 + 60 * k, "notify", "1702", "airheater", (heat(60 - k)).hex()) for k in range(3)]
    evs += [_ev(1010.0, "notify", "1004", "vehicle", "047e071c12070100000000"),       # ignition off
            _ev(1020.0, "notify", "1004", "vehicle", "057e071c12070100000000"),       # bit 7 -> on
            _ev(1022.0, "notify", "1202", "campingmode", "2f"),                       # before shed
            _ev(1022.5, "notify", "1202", "campingmode", "1e")]                       # Enable=1, State=0
    evs += [_ev(1030.0, "write", "1701", "airheater", "3d05003c0c00")]
    p = tmp_path / "ble.jsonl"
    p.write_text("\n".join(json.dumps(e) for e in evs) + "\n")

    r = trace_compare.report(str(p))
    assert r["events"] == len(evs)
    assert r["round_trip"]["energy"]["frames"] == 30 and r["round_trip"]["energy"]["mismatch"] == []
    assert r["cadence"]["1602"]["n"] == 30 and 2.5 < 1 / r["cadence"]["1602"]["median_s"] < 3.5
    assert r["dynamics"]["heater_remaining_per_min"]["observed"] == -1.0
    assert r["dynamics"]["heater_remaining_per_min"]["mock"] == -1.0
    coupling = r["dynamics"]["ignition_to_camping_enable"]
    assert coupling and coupling[0]["ignition"] == 1
    assert r["writes"][0]["decoded"]["NormalOperationRequest"] == 1


def test_cli_prints_a_human_report(tmp_path, capsys):
    p = tmp_path / "ble.jsonl"
    p.write_text(json.dumps(_ev(1.0, "notify", "1302", "water", "030e1d010716")) + "\n")
    assert trace_compare.main([str(p)]) == 0
    out = capsys.readouterr().out
    assert "round-trip" in out and "water" in out and "OK" in out
