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
