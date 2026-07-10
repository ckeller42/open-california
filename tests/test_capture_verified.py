"""calictl control frames checked against the real app's HCI capture (2026-07-08).

The owner captured the CaliforniaOnTour app driving the van (idevicebtlogger). These pin the
frames that capture verified, so a refactor can't silently drift from the app.
"""
from calictl import protocol, overrides, control


def _f():
    f = protocol.load(); overrides.apply(f); return f


def test_airheater_on_off_match_app_bytes():
    # captured on handle 0x0047: on 3d7b007f1f3f, off 3c7b007f1f3f (NormalOperationRequest 1/0)
    f = _f()
    assert control.build(f, "airheater", "power", "on", {}).hex() == "3d7b007f1f3f"
    assert control.build(f, "airheater", "power", "off", {}).hex() == "3c7b007f1f3f"


def test_roof_direction_bytes_match_app():
    # captured on handle 0x0037: open byte0=0x01, close=0x04, stop=0x00 (bytes 1-4 = SafetyCounter)
    f = _f()
    assert control.roof_frame(f, "open").hex()[:2] == "01"
    assert control.roof_frame(f, "close").hex()[:2] == "04"
    assert control.roof_frame(f, "stop").hex()[:2] == "00"


def test_vehicle_leveling_is_degrees():
    # captured raw 1004 (017e0608123b00ff930023): roll ff93 = -1.09°, pitch 0023 = 0.35°
    from calictl import semantics
    f = _f()
    d = protocol.decode(f["vehicle"], bytes.fromhex("017e0608123b00ff930023"))
    interp = semantics.interpret("vehicle", d)
    assert interp["level_roll"] == -1.09
    assert interp["level_pitch"] == 0.35
    # This frame was captured with ignition ON (leveling only reads non-zero when awake), which
    # pins the byte-0 layout: TerminalOneFive is bit 7 (app subList), not bit 0 (our old guess) —
    # so ignition must decode True here. Guards the vehicle byte-0 alignment fix.
    assert interp["ignition_on"] is True


def test_vehicle_clock_unavailable_when_rtc_unset():
    # ignition off / asleep -> all-zero time fields; clock must be None, not "1900-01-00..."
    from calictl import semantics
    f = _f()
    d = protocol.decode(f["vehicle"], bytes(11))
    assert semantics.interpret("vehicle", d)["car_clock"] is None
