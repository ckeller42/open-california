"""calictl protocol/semantics/encode tests — driven by real live captures."""
from calictl import protocol as P, semantics, overrides

# Frames captured live from the owner's vehicle (see docs/business-logic).
WATER = bytes.fromhex("030b1d010016")
COOLER = bytes.fromhex("09440f1e00000000")
ENERGY_IGN_ON = bytes.fromhex("00d040140000452b00fe18318884013f001c000001ff")
CAMPING = bytes.fromhex("32")  # ignition-on: Enable=1, UsbCharger=1, State=0


def _funcs():
    f = P.load(); overrides.apply(f); return f


def test_loads_all_functions():
    f = _funcs()
    assert len(f) == 14                                      # +vehicle (char 1004)
    assert f["water"].state_char.startswith("00001302")
    assert f["cooler"].control_char.startswith("00001101")
    assert f["vehicle"].state_char.startswith("00001004")   # picker selects the non-1000 short
    assert f["general"].state_char.startswith("00001001")   # fixed: was 1002 (VIN)


def test_decode_water_roles_and_percent():
    f = _funcs()
    w = semantics.water(P.decode(f["water"], WATER))
    # Level=current liters, Volume=capacity (names are reversed on the wire)
    assert w["fresh"] == {"liters": 11, "capacity_l": 29, "percent": 38}
    assert w["waste"]["liters"] == 0 and w["waste"]["percent"] == 0
    assert w["installed"] is True


def test_decode_cooler():
    f = _funcs()
    c = semantics.cooler(P.decode(f["cooler"], COOLER))
    assert c["on"] is True and c["level"] == 4 and c["timer_active"] is False


def test_cooler_error_is_power_gated_2bit_enum():
    """Error is a 2-bit enum (bits 0-1 of char 1102), interpreted only while powered on
    (vf/c.java): 0=ok 1=error 2=emergency 3=door_open. Off -> no fault regardless of bits."""
    f = _funcs()
    d = P.decode(f["cooler"], COOLER)
    d["State"] = 1
    for raw, tag in [(0, None), (1, "error"), (2, "emergency"), (3, "door_open")]:
        d["Error"] = raw
        c = semantics.cooler(d)
        assert c["fault"] == tag
        assert c["error"] is bool(tag)
        assert c["door_open"] is (raw == 3)
    # powered off: bits are stale/undefined -> never surface a fault
    d["State"] = 0; d["Error"] = 3
    off = semantics.cooler(d)
    assert off["fault"] is None and off["error"] is False and off["door_open"] is False


def test_roof_position_name_and_infopopup_alert():
    """Position -> name (ig/c.java l() + hf/b.java: 0/14=closed 1=open 2=middle 15=error else=other)
    and InfoPopUp -> alert (0=none 1=child_lock 4=error 6=sensor_error 7=emergency_locked
    10=not_possible 11=low_battery), the alert only surfaced while the roof is installed.

    .. test:: roof position name + InfoPopUp alert mapping
       :id: T_ROOF_ALERT
       :links: R_ROOF_ALERT
       :status: passing
    """
    base = {"Installed": 1, "SafetyCounterValid": 1, "InfoPopUp": 0}
    for pos, name in [(0, "closed"), (1, "open"), (2, "middle"), (14, "closed"),
                      (15, "error"), (7, "other")]:
        assert semantics.roof({**base, "Position": pos})["position_name"] == name
    for raw, alert in [(0, None), (1, "child_lock"), (4, "error"), (6, "sensor_error"),
                       (7, "emergency_locked"), (10, "not_possible"), (11, "low_battery")]:
        assert semantics.roof({**base, "Position": 0, "InfoPopUp": raw})["alert"] == alert
    # not installed -> no alert even if the field is non-zero (matches the app's install gate)
    assert semantics.roof({"Installed": 0, "Position": 0, "InfoPopUp": 4})["alert"] is None


def test_energy_charging_and_scale():
    f = _funcs()
    e = semantics.energy(P.decode(f["energy"], ENERGY_IGN_ON))
    assert e["stale"] is False              # Age=0 (ignition on)
    assert e["dcdc_charging"] is True       # StateDcdc=1
    assert e["batt1_v"] == 13.6             # UOneBattBemAfs=136 * 0.1
    assert e["solar_installed"] is False
    assert isinstance(e["solar_power"], int)   # solar signal kept even when not fitted
    assert e["batt2_current"] is not None      # leisure-battery draw (ITwoBattBemAfs) now decoded
    assert "batt2_remaining_h" in e


def test_camping_lights_combined_inverted_and_gated():
    f = _funcs()
    c = semantics.campingmode(P.decode(f["campingmode"], CAMPING))  # 0x32: State=0,USB=1,Int=Out=0,Enable=1
    assert c["master_on"] is False and c["usb_charger"] is True
    assert c["outputs_controllable"] is False          # lights/USB only toggle when master on
    assert c["lights_on"] is False                     # gated off when camping mode off
    assert c["enable"] is True
    # app: Lights ON = master on AND both light fields 0 (K0 writes 0/0, inverted+combined)
    d = P.decode(f["campingmode"], CAMPING); d["State"] = 1
    d["InteriorLight"] = 0; d["OutsideLight"] = 0
    assert semantics.campingmode(d)["lights_on"] is True
    d["InteriorLight"] = 1; d["OutsideLight"] = 1
    assert semantics.campingmode(d)["lights_on"] is False   # fields=1 -> not lit


def test_encode_cooler_frames_match_hand_derived():
    f = _funcs()
    base = dict(State=1, Mode=4, Level=4, TimerStart=3, TimerCancel=3, NightTimerSet=0,
                NightTimerHourOff=0, TimerHour=15, TimerMin=30, NightTimerHourOn=0)
    # Corrected cooler timer offsets (sf/a.java f() default branch): TimerHour@16, TimerMin@24,
    # contiguous (no 4-bit hole — that was the airheater layout). So 15/30 land in bytes 2/3.
    assert P.encode(f["cooler"], base, frame_bytes=6).hex() == "3d440f1e0000"
    assert P.encode(f["cooler"], {**base, "State": 0}, frame_bytes=6).hex() == "3c440f1e0000"
    assert P.encode(f["cooler"], {**base, "Level": 3}, frame_bytes=6).hex() == "3d430f1e0000"


def test_encode_rejects_out_of_width_value():
    f = _funcs()
    base = dict(State=1, Mode=4, Level=16, TimerStart=3, TimerCancel=3, NightTimerSet=0,
                NightTimerHourOff=0, TimerHour=0, TimerMin=0, NightTimerHourOn=0)
    # Level is a 4-bit field (0..15); 16 would silently wrap to 0 without the guard
    try:
        P.encode(f["cooler"], base, frame_bytes=6)
    except ValueError as e:
        assert "out of range" in str(e) and "Level" in str(e)
    else:
        raise AssertionError("expected ValueError for out-of-width Level=16")


def test_encode_rejects_curated_invalid_value():
    f = _funcs()
    # cooler State=3 fits the 2-bit field but is rejected 0x0E on-device; curated {0,1}
    base = dict(State=3, Mode=4, Level=4, TimerStart=3, TimerCancel=3, NightTimerSet=0,
                NightTimerHourOff=0, TimerHour=0, TimerMin=0, NightTimerHourOn=0)
    try:
        P.encode(f["cooler"], base, frame_bytes=6)
    except ValueError as e:
        assert "not an allowed value" in str(e) and "State" in str(e)
    else:
        raise AssertionError("expected ValueError for curated-invalid cooler State=3")
    # the camping sentinel 3 is NOT restricted (leave-unchanged), still encodes
    from calictl import control
    assert len(control.build(f, "campingmode", "usb", "on", {})) == 1


def test_control_ranges_consistent_with_width():
    from tools import app_ranges
    f = _funcs()
    assert app_ranges.inconsistencies(f) == []   # no curated value exceeds its field width


def test_energy_source_current_nulled_when_not_installed():
    f = _funcs()
    e = semantics.energy(P.decode(f["energy"], ENERGY_IGN_ON))
    assert e["solar_installed"] is False and e["solar_current"] is None   # 511 sentinel not surfaced
    # when installed, the current is a real value (unsigned /10 A, per xf/d.java)
    d = P.decode(f["energy"], ENERGY_IGN_ON); d["PvInstalled"] = 1; d["IPvAfs"] = 50
    assert semantics.energy(d)["solar_current"] == 5.0   # 50 raw /10 = 5.0 A


def test_encode_refuses_frame_too_small():
    # a too-small frame_bytes must raise, not silently grow/corrupt the frame
    f = _funcs()
    base = dict(State=1, Mode=4, Level=4, TimerStart=3, TimerCancel=3, NightTimerSet=0,
                NightTimerHourOff=0, TimerHour=0, TimerMin=0, NightTimerHourOn=0)
    try:
        P.encode(f["cooler"], base, frame_bytes=2)   # cooler needs 6
    except ValueError as e:
        assert "exceeds" in str(e)
    else:
        raise AssertionError("expected ValueError for undersized frame_bytes")


def test_encode_refuses_unresolved_without_override():
    f = P.load()  # NO overrides applied -> cooler timer fields unplaced
    try:
        P.encode(f["cooler"], dict(State=1, Mode=4, Level=4, TimerStart=3,
                                   TimerCancel=3, NightTimerSet=0), frame_bytes=6)
    except ValueError as e:
        assert "unresolved" in str(e)
    else:
        raise AssertionError("expected ValueError for unresolved offsets")


def test_decode_roundtrip_via_bits():
    # a byte-aligned 8-bit field equals its raw byte
    bits = P.to_bits(bytes([0x1d]))
    assert P.get_field(bits, 0, 8) == 0x1d


def test_energy_batt1_sentinel_nulled():
    f = _funcs()
    # engine-off frame: IOneBattBemAfs=0x81 sentinel -> batt1_v suppressed
    off = bytes.fromhex("00d0500400ff810000fe18033083fffdfffe000001ff")
    e = semantics.energy(P.decode(f["energy"], off))
    assert e["batt1_v"] is None and e["batt2_v"] == 13.1


def test_mqtt_flatten_and_state():
    from calictl import mqtt
    interp = {"installed": True, "fresh": {"percent": 38, "liters": 11}, "waste": {"percent": 0}}
    flat = mqtt.flatten(interp)
    assert flat["fresh_percent"] == 38 and flat["waste_percent"] == 0
    topic, payload = mqtt.render_state("water", interp)
    assert topic == "calivan/water" and '"fresh_percent": 38' in payload


def test_installed_from_states():
    from calictl import serve
    states = {"water": {"installed": True}, "stairs": {"installed": False},
              "energy": {"installed": True}, "roof": {"installed": True}}
    assert serve.installed_from(states) == {"water", "energy", "roof"}


def test_numeric_fields_flattens_and_filters():
    # pure/stdlib: nested numerics flatten; bool->1.0/0.0; list-> <key>_count; str/None dropped
    from calictl import influx
    nf = influx.numeric_fields({"installed": True, "on": False, "level": 5,
                                "fresh": {"percent": 38.0}, "mode": "eco", "x": None,
                                "faults": ["a", "b"]})
    assert nf == {"installed": 1.0, "on": 0.0, "level": 5.0,
                  "fresh_percent": 38.0, "faults_count": 2.0}


def test_points_for_reuses_numeric_fields():
    import pytest
    pytest.importorskip("influxdb_client")   # tests must run without MQTT/Influx installed
    from calictl import influx
    pts = influx.points_for({"water": {"installed": True, "fresh": {"percent": 38}}})
    # one "camper" Point tagged function=water carrying the flattened numeric field
    assert len(pts) == 1
    p = pts[0]
    assert p._name == "camper" and p._tags.get("function") == "water"
    assert p._fields.get("fresh_percent") == 38.0   # field actually landed, not just the point


def test_full_parity_devices_and_installed_gating():
    from calictl import mqtt
    # every installed function gets its own HA device + >=1 entity
    installed = {"water", "energy", "cooler", "campingmode", "airheater", "roof", "lighting"}
    cfgs = mqtt.render_discovery(installed=installed)
    # one device per function, keyed in each entity's config
    devices = {tuple(sorted(c["device"]["identifiers"])) for c in cfgs.values()}
    assert ("vwcamper_water",) in devices and ("vwcamper_energy",) in devices
    # a not-installed function (stairs) publishes nothing
    assert not any("stairs" in t for t in cfgs)
    # roof is present but ONLY as a sensor (no switch/light/command_topic)
    roof = [c for t, c in cfgs.items() if "roof_" in t and "roofair" not in t]
    assert roof and all("command_topic" not in c for c in roof)


def test_camping_lights_control_frame():
    from calictl import protocol as P, overrides, control
    funcs = P.load(); overrides.apply(funcs)
    # app's single "Lights" toggle writes BOTH light fields together, inverted (on -> 0)
    frame = control.build(funcs, "campingmode", "lights", "on", {})
    assert len(frame) == 1
    back = control.decode_control(funcs["campingmode"], frame)
    assert back["InteriorLight"] == control.LIGHT_ON and back["OutsideLight"] == control.LIGHT_ON  # both
    assert back["State"] == 3 and back["UsbCharger"] == 3   # untouched = sentinel
    # USB is not inverted, and is separate from the lights
    usb = control.decode_control(funcs["campingmode"],
                                 control.build(funcs, "campingmode", "usb", "on", {}))
    assert usb["UsbCharger"] == 1 and usb["InteriorLight"] == 3


def test_cooler_power_control_frame():
    from calictl import control
    f = _funcs()
    cur = {"State": 0, "Mode": 4, "Level": 3}
    on = control.decode_control(f["cooler"], control.build(f, "cooler", "power", "on", cur))
    off = control.decode_control(f["cooler"], control.build(f, "cooler", "power", "off", cur))
    assert on["State"] == 1 and off["State"] == 0
    assert on["Level"] == 3 and on["Mode"] == 4          # untargeted fields carry current


def test_cooler_level_control_frame_and_range():
    from calictl import control
    f = _funcs()
    cur = {"State": 1, "Mode": 4, "Level": 3}
    fr = control.build(f, "cooler", "level", "5", cur)
    assert control.decode_control(f["cooler"], fr)["Level"] == 5
    for bad in ("0", "6"):
        try:
            control.build(f, "cooler", "level", bad, cur)
        except ValueError:
            pass
        else:
            raise AssertionError("expected ValueError for cooler level %s" % bad)


def test_lighting_zone_set_leaves_others_unchanged():
    from calictl import control
    f = _funcs()
    # setting one zone: that zone gets the value, every OTHER zone the leave-unchanged
    # sentinel 14 (0xe) — the HCI-verified behaviour, NOT 0.
    last = {"ProfileNumber": 12}
    back = control.decode_control(f["lighting"], control.build(f, "lighting", "kitchen", "8", last))
    assert back["Mode"] == control.LIGHT_MODE_SET_BRIGHTNESS   # SET_BRIGHTNESS
    assert back["ProfileNumber"] == 12                         # echoes the ACTIVE profile
    assert back["BrightnessLSeven"] == 8                       # kitchen (L7) -> 8
    others = [v for k, v in back.items() if k.startswith("Brightness") and k != "BrightnessLSeven"]
    assert set(others) == {control.LIGHT_UNCHANGED}            # every other zone = 14 (unchanged)


def test_lighting_power_all_zones():
    from calictl import control
    f = _funcs()
    on = control.decode_control(f["lighting"], control.build(f, "lighting", "power", "on", {}))
    off = control.decode_control(f["lighting"], control.build(f, "lighting", "power", "off", {}))
    on_zones = [on[n] for n in on if n.startswith("BrightnessL")]
    off_zones = [off[n] for n in off if n.startswith("BrightnessL")]
    assert len(on_zones) == 16 and all(z == control.LIGHT_ON_BRIGHTNESS for z in on_zones)
    assert all(z == 0 for z in off_zones)


def test_lighting_brightness_range_guard():
    from calictl import control
    f = _funcs()
    for bad in ("-1", "16"):
        try:
            control.build(f, "lighting", "kitchen", bad, {})
        except ValueError:
            pass
        else:
            raise AssertionError("expected ValueError for lighting brightness %s" % bad)


def test_airheater_control_frame():
    """Airheater control frame: power (NormalOperationRequest) + level (HeatingLevel).

    .. test:: Airheater control frame
       :id: T_AIRHEATER_SET
       :links: R_AIRHEATER_SET
       :status: passing

       Verifies :need:`R_AIRHEATER_SET`: all four formerly-MERGED_AMBIGUOUS fields
       are placed (the frame encodes), power toggles NormalOperationRequest 1/0, and
       level sets HeatingLevel; untargeted request fields stay at the sentinel 3.
    """
    from calictl import control
    f = _funcs()
    cur = {"OperationModeAirHeater": 7, "HeatingLevel": 11, "AirDistribution": 0, "RunningTime": 127}
    on = control.decode_control(f["airheater"], control.build(f, "airheater", "power", "on", cur))
    off = control.decode_control(f["airheater"], control.build(f, "airheater", "power", "off", cur))
    assert on["NormalOperationRequest"] == 1 and off["NormalOperationRequest"] == 0
    assert on["OperationModeCombined"] == 0 and on["RunningTime"] == 127   # formerly MERGED, now placed
    assert on["PermanentOperationRequest"] == 3                            # untouched = sentinel
    lvl = control.decode_control(f["airheater"], control.build(f, "airheater", "level", "9", cur))
    assert lvl["HeatingLevel"] == 9
    for bad in ("-1", "16"):
        try:
            control.build(f, "airheater", "level", bad, cur)
        except ValueError:
            pass
        else:
            raise AssertionError("expected ValueError for airheater level %s" % bad)


def test_vehicle_decode_char_1004():
    """Decode the vehicle characteristic (BLE char ``00001004``) end-to-end.

    .. test:: Vehicle 1004 decode
       :id: T_VEHICLE_DECODE
       :links: R_VEHICLE_1004
       :status: passing

       Verifies :need:`R_VEHICLE_1004`: a captured ``1004`` frame decodes to the
       correct ignition (terminal-15 off), car variant, real-time clock and
       roll/pitch leveling, and that the level axes are signed.
    """
    f = _funcs()
    raw = bytes.fromhex("047e060717142a00000000")   # live capture 2026-07-07
    v = semantics.vehicle(P.decode(f["vehicle"], raw))
    assert v["ignition_on"] is False                 # terminal-15 off (parked)
    # byte-0 layout corrected to the app's subList slices (CarVariant@0/4): this yields a
    # CONSISTENT CarVariant across frames (0 here and in the ignition-on capture), whereas the
    # old CarVariant@3/4 spuriously read a CarLevelPopUp bit as "2". See test_capture_verified.
    assert v["car_variant"] == 0
    assert v["car_clock"] == "2026-07-07 23:20:42"   # year+1900, month+1
    assert v["level_roll"] == 0 and v["level_pitch"] == 0
    # signed axes scaled to degrees (0.01°): a 0xFFFF roll = -1 raw = -0.01°, not 655.35
    raw_neg = bytes.fromhex("047e060717142affff0000")
    assert semantics.vehicle(P.decode(f["vehicle"], raw_neg))["level_roll"] == -0.01


def test_cli_set_check_all_rows():
    from calictl import cli
    # (function, what, value, interp, decoded) -> (label, got, want)
    assert cli._set_check("cooler", "power", "on", {}, {"State": 1}) == ("State", 1, 1)
    assert cli._set_check("cooler", "power", "off", {}, {"State": 0}) == ("State", 0, 0)
    assert cli._set_check("cooler", "level", "5", {}, {"Level": 5}) == ("Level", 5, 5)
    assert cli._set_check("campingmode", "master", "on", {"master_on": True}, {}) == ("master_on", True, True)
    assert cli._set_check("campingmode", "usb", "off", {"usb_charger": False}, {}) == ("usb_charger", False, False)
    assert cli._set_check("campingmode", "lights", "on", {"lights_on": True}, {}) == ("lights_on", True, True)
    assert cli._set_check("lighting", "power", "on", {"any_on": True}, {}) == ("any_on", True, True)
    assert cli._set_check("airheater", "power", "on", {"running": True}, {}) == ("running", True, True)
    assert cli._set_check("airheater", "level", "9", {"level": 9}, {}) == ("level", 9, 9)
    # unknown target -> passthrough with no expectation
    assert cli._set_check("cooler", "bogus", "x", {}, {}) == ("bogus", None, None)


def test_cli_max_zone():
    from calictl import cli
    interp = {"brightness_zone_1": 0, "brightness_zone_10": 13, "brightness_zone_16": 8, "other": 99}
    assert cli._max_zone(interp) == 13
    assert cli._max_zone({}) == 0


def test_heartbeat_counter_bytes():
    # 1003 liveness counter: 4-byte big-endian, monotonic +1, wraps at 32 bits.
    from calictl import device
    assert device._beat_bytes(device.HEARTBEAT_START) == bytes.fromhex("00100000")
    assert device._beat_bytes(device.HEARTBEAT_START + 1) == bytes.fromhex("00100001")
    assert device._beat_bytes(0x1_00000000) == bytes.fromhex("00000000")
