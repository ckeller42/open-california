"""Firmware drift-capture + plausibility anchors (calictl.firmware, calictl.anchors)."""
import json

from calictl import anchors, firmware


def test_firmware_change_detection():
    """`changed` fires only on a KNOWN-to-KNOWN difference — not first read, not a partial read."""
    assert firmware.changed(None, ("0410", 2)) is False           # first read: no prev
    assert firmware.changed(("0410", 2), ("0410", 2)) is False    # unchanged
    assert firmware.changed(("0409", 2), ("0410", 2)) is True     # amb bump
    assert firmware.changed(("0410", 2), ("0410", 3)) is True     # protocol (comm) bump
    assert firmware.changed(("0410", 2), (None, None)) is False   # partial/empty read -> not a change
    assert firmware.changed(("0410", 2), (None, 2)) is False      # amb missing this poll -> no fake change


def test_firmware_snapshot_captures_raw_frames(tmp_path):
    """.. test:: The drift snapshot persists the raw per-char bytes + version
       :id: T_FIRMWARE_DRIFT_CAPTURE
       :links: R_FIRMWARE_DRIFT_CAPTURE

    The snapshot must carry the RAW hex frames (the irreplaceable evidence) keyed by function, plus
    the firmware identity, so a later diff can spot what shifted.
    """
    states = {"general": {"amb_sw_version": "0411", "cm_sw_version": "0208", "comm_version": 3},
              "energy": {"batt2_v": 13.1}}
    raw = {"general": b"\x30\x34\x31\x31", "energy": b"\x01\x02\x03"}
    path = firmware.write_snapshot(states, raw, str(tmp_path), reason="drift", now=1000.0)
    blob = json.load(open(path))
    assert blob["amb_sw_version"] == "0411" and blob["comm_version"] == 3
    assert blob["raw_frames_hex"]["general"] == "30343131"        # the wire bytes, recoverable
    assert blob["raw_frames_hex"]["energy"] == "010203"
    assert "fw-drift-0411-1000.json" in path


def test_anchors_flag_implausible_decode():
    """A shifted offset pushes a value out of physical range -> a violation string; in-range = none.

    .. test:: Plausibility anchors flag an implausible (drifted) decode
       :id: T_PLAUSIBILITY_ANCHORS
       :links: R_PLAUSIBILITY_ANCHORS
    """
    assert anchors.check({"energy": {"batt2_v": 13.2, "soc2_level": 8}}) == []
    bad = anchors.check({"energy": {"batt2_v": 250.0, "soc2_level": 99}})
    assert any("leisure battery" in m for m in bad) and any("SoC" in m for m in bad)
    # subsystem gating: an out-of-range cooler level is ignored when the cooler isn't installed
    assert anchors.check({"cooler": {"installed": False, "level": 42}}) == []
    assert any("cooler level" in m for m in anchors.check({"cooler": {"installed": True, "level": 42}}))
    # a missing value is never a violation
    assert anchors.check({"energy": {"batt2_v": None}}) == []
