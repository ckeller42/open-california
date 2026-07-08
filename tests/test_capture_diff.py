"""Tests for the Phase-2 differential oracle (tools.capture_diff).

Deterministic — no real capture, no tshark, no BLE. They validate the tested core:
  * a known-good app frame diffs to ZERO against calictl (the cooler pipeline check);
  * a frame carrying a field calictl sends as 0 is flagged as a LEAD (the lighting crack);
  * the frames-file and tshark-field parsers;
  * end-to-end run() over a --frames list.
"""
import pytest

from calictl import protocol, control, overrides
from tools import capture_diff
from tools.capture_diff import Scenario


def _funcs():
    f = protocol.load(); overrides.apply(f); return f


def _cooler_scenario():
    return Scenario.from_dict("cooler/power-on", {
        "function": "cooler", "what": "power", "value": "on", "control_char": "1101",
        "handle": 0x0022, "state": {"State": 0, "Mode": 4, "Level": 3}})


def test_cooler_scenario_zero_diff():
    funcs = _funcs()
    app_frame = bytes.fromhex("3d4300000000")          # the app's real cooler-ON frame
    rows, leads, ours = capture_diff.diff(funcs, _cooler_scenario(), app_frame)
    assert ours == app_frame                            # calictl reproduces it byte-for-byte
    assert all(r.match for r in rows) and leads == []


def test_lighting_lead_detected():
    """Simulate the app carrying LightValue + a lit zone that calictl leaves at 0 —
    exactly the signal that would crack lighting SET."""
    funcs = _funcs()
    scen = Scenario.from_dict("lighting/kitchen-50", {
        "function": "lighting", "what": "brightness", "value": 8, "control_char": "1501"})
    ours_frame = control.build(funcs, "lighting", "brightness", 8, {})
    app_vals = dict(control.decode_control(funcs["lighting"], ours_frame))
    zone = next(cf.name for cf in funcs["lighting"].control_fields
                if cf.name.startswith("BrightnessL") and cf.placed)
    app_vals["LightValue"] = 1                          # app enables the zone mask
    app_vals[zone] = 8                                  # ...and lights the zone
    app_frame = protocol.encode(funcs["lighting"], app_vals, frame_bytes=16)

    rows, leads, _ = capture_diff.diff(funcs, scen, app_frame)
    assert "LightValue" in leads and zone in leads
    assert any(not r.match for r in rows)


def test_parse_frames_file(tmp_path):
    p = tmp_path / "frames.txt"
    p.write_text("# a capture excerpt\n0x0022: 3d 43 00 00 00 00\n1501: 00040000\n")
    got = capture_diff.parse_frames_file(p)
    assert got[0] == ("0x0022", bytes.fromhex("3d4300000000"))
    assert got[1] == ("1501", bytes.fromhex("00040000"))


def test_select_app_frame_from_frames():
    scen = _cooler_scenario()
    writes = [("1501", b"\x00"), ("0x0022", bytes.fromhex("3d4300000000"))]
    assert capture_diff.select_app_frame(scen, writes, from_frames=True) == bytes.fromhex("3d4300000000")


def test_parse_tshark_fields():
    out = "0x0022\t3d:43:00:00:00:00\n\t\n0x0025\t00040000\n"
    got = capture_diff._parse_tshark_fields(out)
    assert got == [(0x22, bytes.fromhex("3d4300000000")), (0x25, bytes.fromhex("00040000"))]


def test_run_frames_cooler_zero_diff(tmp_path):
    pytest.importorskip("yaml")                         # run() loads the scenario yaml
    p = tmp_path / "frames.txt"
    p.write_text("0x0022: 3d4300000000\n")
    assert capture_diff.run(str(p), "cooler/power-on", frames=True) == 0


def test_load_scenario_reads_yaml():
    pytest.importorskip("yaml")
    scen = capture_diff.load_scenario("cooler/power-on")
    assert scen.function == "cooler" and scen.handle == 0x0022
