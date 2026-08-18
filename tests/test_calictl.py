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


def test_cooler_error_is_installed_gated_2bit_enum():
    """Error is a 2-bit enum (bits 0-1 of char 1102): 0=ok 1=error 2=emergency 3=door_open. The app
    gates the fault on INSTALLED, not power (vf/c.java:423 dispatches on the Installed bit, not
    State), so a fault on an installed-but-powered-off fridge still surfaces. Not installed -> none."""
    f = _funcs()
    d = P.decode(f["cooler"], COOLER)
    d["Installed"] = 1
    for state in (1, 0):                       # fault shows regardless of power, as long as installed
        d["State"] = state
        for raw, tag in [(0, None), (1, "error"), (2, "emergency"), (3, "door_open")]:
            d["Error"] = raw
            c = semantics.cooler(d)
            assert c["fault"] == tag
            assert c["error"] is bool(tag)
            assert c["door_open"] is (raw == 3)
    # not installed: the Error bits are meaningless -> never surface a fault
    d["Installed"] = 0; d["Error"] = 3
    off = semantics.cooler(d)
    assert off["fault"] is None and off["error"] is False and off["door_open"] is False


def test_airheater_level_range_1_to_10():
    """Verified at the van 2026-07-14 (HCI capture): the app exposes heating levels 1-9 + "HI"(=10),
    building `HeatingLevel` raw 1 (lowest) .. 10 (HI). 11 is the post-set leave-unchanged/commit
    value; 0 and 12-15 are never emitted. calictl's frames match the app's byte-for-byte."""
    from calictl import control
    f = _funcs()
    st = {"AirDistribution": 0, "OperationModeAirHeater": 7, "HeatingLevel": 11,
          "RunningTime": 127, "TimerHour": 31, "TimerMin": 63}
    assert control.build(f, "airheater", "level", 1, st).hex() == "3f71007f1f3f"   # == app's lowest
    assert control.build(f, "airheater", "level", 10, st).hex() == "3f7a007f1f3f"  # == app's "HI"
    for bad in (0, 12, 15):
        try:
            control.build(f, "airheater", "level", bad, st)
        except ValueError as e:
            assert "HeatingLevel" in str(e)
        else:
            raise AssertionError("expected ValueError for out-of-range HeatingLevel=%d" % bad)


def test_energy_mode_control_frame():
    """`set energy mode <normal|max_charge|eco>` builds the 1-byte char-1601 frame with
    EnergyModeSet @2/w2 (0/1/2) and DisplayRefresh @7/w1 = 0. Decompile-derived from the app's
    own energy-VM setter (xf/d.java:389 stages EnergyModeSet = the mode ordinal, DisplayRefresh
    stays 0) + the shared EnergyMode enum (bf/c.java: 0=normal 1=max_charge 2=eco); NOT yet
    wire-captured / live-verified."""
    from calictl import control
    f = _funcs()
    assert control.build(f, "energy", "mode", "normal", {}).hex() == "00"      # EnergyModeSet=0
    assert control.build(f, "energy", "mode", "max_charge", {}).hex() == "10"  # EnergyModeSet=1 -> bits2,3=01
    assert control.build(f, "energy", "mode", "eco", {}).hex() == "20"         # EnergyModeSet=2 -> bits2,3=10
    assert control.build(f, "energy", "mode", "Eco ", {}).hex() == "20"        # case/space-insensitive
    assert control.build(f, "energy", "mode", "turbo", {}) is None            # unknown mode
    assert control.build(f, "energy", "off", "eco", {}) is None               # unknown action
    assert f["energy"].control_char.startswith("00001601")                    # actuate target


def test_cooler_quiet_mode_and_schedule_frames():
    """Cooler quiet Mode + night-schedule/timer branches, decompile-verified from vf/c.java
    (Mode 0=normal 2=manual-quiet 4=timer-quiet; NightTimerHourOn/Off = raw hour; TimerStart=1).
    Full-packet, carrying State/Level; NOT yet live-verified."""
    from calictl import control
    f = _funcs()
    last = {"State": 1, "Mode": 0, "Level": 3}
    assert control.build(f, "cooler", "mode", "quiet", last).hex() == "3d2300000000"        # Mode=2
    assert control.build(f, "cooler", "mode", "timer_quiet", last).hex() == "3d4300000000"  # Mode=4
    assert control.build(f, "cooler", "night_on", 22, last).hex() == "3d0300001600"          # byte4=0x16
    assert control.build(f, "cooler", "night_off", 7, last).hex() == "3d0300000007"          # byte5=0x07
    assert control.build(f, "cooler", "timer_set", "06:45", last).hex() == "3d03062d0000"   # TimerHour=6 TimerMin=45
    assert control.build(f, "cooler", "timer_start", None, last).hex()[:2] != "3d"          # TimerStart flips byte0
    assert control.build(f, "cooler", "mode", "loud", last) is None                          # unknown mode
    for bad in (-1, 24):
        try:
            control.build(f, "cooler", "night_on", bad, last)
        except ValueError:
            pass
        else:
            raise AssertionError("expected ValueError for night hour %d" % bad)


def test_airheater_runtime_and_timer_frames():
    """Air-heater run-time + start-timer branches, decompile-verified from rf/b.java
    (RunningTime @24, TimerHour @32 / TimerMin @40). Permanent heating is deliberately NOT wired
    (the app's E3() only writes OFF; the ON value is unknown). NOT live-verified."""
    from calictl import control
    f = _funcs()
    st = {"NormalOperationRequest": 0, "HeatingLevel": 5, "RunningTime": 127,
          "AirDistribution": 0, "OperationModeAirHeater": 7, "TimerHour": 31, "TimerMin": 63}
    assert control.build(f, "airheater", "runtime", 60, st).hex() == "3f75003c1f3f"   # byte3=0x3c=60
    assert control.build(f, "airheater", "timer", "22:30", st).hex() == "3f75007f161e" # byte4=22 byte5=30
    assert control.build(f, "airheater", "permanent", "on", st) is None               # not wired (ON unknown)
    try:
        control.build(f, "airheater", "timer", "24:00", st)
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for out-of-range timer")


def test_lighting_save_favorite_frame():
    """`save_profile` writes the CURRENT lighting into a favorite (dg/h.java:564 l3): SET_BRIGHTNESS
    with ProfileNumber = the favorite N (not the live-view 9), real zones carrying their current
    brightness (from `last`), non-equipped zones = 14. Decompile-derived, NOT live-verified."""
    from calictl import control
    f = _funcs()
    last = {"BrightnessLSeven": 5, "BrightnessLFive": 8, "BrightnessLThree": 2}
    fr = control.build(f, "lighting", "save_profile", 3, last)
    assert fr[0] == 0x03            # ProfileNumber = 3 (the favorite, not 9)
    assert fr[1] == 0x04            # Mode = SET_BRIGHTNESS
    assert fr.hex().endswith("eeeeeeee")   # L9-L16 not-equipped -> unchanged (14)
    assert "e2e8e5" in fr.hex()    # L3=2, L5=8, L7=5 carried (each real zone's current brightness)
    for bad in (0, 8):
        try:
            control.build(f, "lighting", "save_profile", bad, last)
        except ValueError:
            pass
        else:
            raise AssertionError("expected ValueError for save_profile slot %d" % bad)


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


def test_general_decodes_sw_version_strings_and_drives_plus2():
    # general (char 1001) had UNPLACED fields -> decoded to {} -> amb_sw_version None -> the +2
    # correction could never fire live. With offsets + ASCII decode it yields the real strings.
    f = _funcs()
    raw = bytes.fromhex("303431303032303702")            # "0410" | "0207" | 0x02
    g = semantics.general(P.decode(f["general"], raw))
    assert g["amb_sw_version"] == "0410" and g["cm_sw_version"] == "0207" and g["comm_version"] == 2
    # and it now feeds the +2 gate for real (this van is 0410)
    st = {"general": g, "energy": {"dcdc_current": -2}}
    semantics.apply_sw_corrections(st)
    assert st["energy"]["dcdc_current"] == 0


def test_dcdc_sw_correction():
    # app adds +2 to DC-DC current on AmbSwVersion 0409/0410 (this van is 0410); cross-function,
    # applied over a full states dict via apply_sw_corrections (verified vs the app 2026-08-17)
    st = {"general": {"amb_sw_version": "0410"}, "energy": {"dcdc_current": 5}}
    semantics.apply_sw_corrections(st)
    assert st["energy"]["dcdc_current"] == 7                     # +2 applied
    # a non-listed version -> untouched
    assert semantics.apply_sw_corrections(
        {"general": {"amb_sw_version": "0207"}, "energy": {"dcdc_current": 5}})["energy"]["dcdc_current"] == 5
    # dcdc not present (source not installed) -> no-op; missing general context -> no-op
    assert semantics.apply_sw_corrections(
        {"general": {"amb_sw_version": "0410"}, "energy": {"dcdc_current": None}})["energy"]["dcdc_current"] is None
    assert semantics.apply_sw_corrections({"energy": {"dcdc_current": 5}})["energy"]["dcdc_current"] == 5


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


def test_numeric_fields_alert_enums_become_codes():
    # cooler.fault / roof.alert are strings (dropped by Influx) -> emit numeric <key>_code so
    # Grafana can chart them; None/unknown -> 0 (ok), keeping the series continuous.
    from calictl import influx
    assert influx.numeric_fields({"fault": "door_open"})["fault_code"] == 3.0
    assert influx.numeric_fields({"fault": None})["fault_code"] == 0.0
    assert influx.numeric_fields({"alert": "child_lock"})["alert_code"] == 1.0
    assert influx.numeric_fields({"alert": "low_battery"})["alert_code"] == 6.0
    assert influx.numeric_fields({"alert": None})["alert_code"] == 0.0


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
    # ProfileNumber is HARDCODED to 9 like the app (LIGHT_BRIGHTNESS_PROFILE), regardless of the
    # currently-active profile in `last` — so a set applies with the lights off (PN=0) too.
    assert back["ProfileNumber"] == control.LIGHT_BRIGHTNESS_PROFILE
    assert back["BrightnessLSeven"] == 8                       # kitchen (L7) -> 8
    others = [v for k, v in back.items() if k.startswith("Brightness") and k != "BrightnessLSeven"]
    assert set(others) == {control.LIGHT_UNCHANGED}            # every other zone = 14 (unchanged)


def test_lighting_power_is_app_faithful_profile_toggle():
    from calictl import control
    f = _funcs()
    # power == the app's "Alle Lichter" master toggle: SET_PROFILE (Mode 16) selecting
    # LIGHTS_ON (12) / LIGHTS_OFF (0), NOT per-zone brightness (dg/h.java:323 Q()).
    on = control.decode_control(f["lighting"], control.build(f, "lighting", "power", "on", {}))
    off = control.decode_control(f["lighting"], control.build(f, "lighting", "power", "off", {}))
    assert on["Mode"] == control.LIGHT_MODE_SET_PROFILE and on["ProfileNumber"] == 12
    assert off["Mode"] == control.LIGHT_MODE_SET_PROFILE and off["ProfileNumber"] == 0
    # every zone carries the unchanged sentinel (a profile select never sets per-zone brightness)
    assert all(v == control.LIGHT_UNCHANGED for n, v in on.items() if n.startswith("BrightnessL"))
    assert all(v == control.LIGHT_UNCHANGED for n, v in off.items() if n.startswith("BrightnessL"))


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
    # power == SET_PROFILE master toggle -> check ProfileNumber (12=LIGHTS_ON, 0=LIGHTS_OFF),
    # NOT any_on (the profile-select echo carries all zones=14, so any_on is always False)
    assert cli._set_check("lighting", "power", "on", {"profile": 12}, {}) == ("profile", 12, 12)
    assert cli._set_check("lighting", "power", "off", {"profile": 0}, {}) == ("profile", 0, 0)
    assert cli._set_check("airheater", "power", "on", {"running": True}, {}) == ("running", True, True)
    assert cli._set_check("airheater", "level", "9", {"level": 9}, {}) == ("level", 9, 9)
    # unknown target -> passthrough with no expectation
    assert cli._set_check("cooler", "bogus", "x", {}, {}) == ("bogus", None, None)


def test_cli_max_zone():
    from calictl import cli
    # only real zones L1-L8 count; L9-L16 (here 10=13, 16=8) are the unchanged-sentinel range
    interp = {"brightness_zone_1": 0, "brightness_zone_7": 9, "brightness_zone_10": 13, "brightness_zone_16": 8}
    assert cli._max_zone(interp) == 9
    assert cli._max_zone({}) == 0


def test_heartbeat_counter_bytes():
    # 1003 liveness counter: 4-byte big-endian, monotonic +1, wraps at 32 bits.
    from calictl import device
    assert device._beat_bytes(device.HEARTBEAT_START) == bytes.fromhex("00100000")
    assert device._beat_bytes(device.HEARTBEAT_START + 1) == bytes.fromhex("00100001")
    assert device._beat_bytes(0x1_00000000) == bytes.fromhex("00000000")


def test_water_stale_latch_guard():
    """ANY fresh-water drop with no matching grey-water rise is physically impossible (the
    parked/asleep stale latch, confirmed at the van 2026-07-14). Crucially the latch DECAYS
    gradually (~1 L/poll), so even a 1 L unaccounted drop must be flagged — else it ratchets the
    baseline down. Real usage (fresh down, grey up together) and refills (fresh up) stay plausible.

    .. test:: reject physically impossible fresh-water drops
       :id: T_WATER_STALE_GUARD
       :links: R_WATER_STALE_GUARD
       :status: passing
    """
    from calictl import freshness
    def w(fresh, waste):
        return {"fresh": {"liters": fresh}, "waste": {"liters": waste}}
    # sharp latch: 17 -> 1 with grey flat -> impossible
    assert freshness.implausible_water_drop(w(1, 1), w(17, 1)) is True
    # GRADUAL decay: even a 1 L drop with grey flat -> impossible (the ratchet bug this prevents)
    assert freshness.implausible_water_drop(w(16, 1), w(17, 1)) is True
    # real usage: fresh down 2, grey up 2 -> plausible
    assert freshness.implausible_water_drop(w(15, 3), w(17, 1)) is False
    # real usage, exact 1 L: fresh down 1, grey up 1 -> plausible
    assert freshness.implausible_water_drop(w(16, 2), w(17, 1)) is False
    # real usage where fresh leaves faster than grey fills (drink/cook/external drain): grey STILL
    # rose, which proves the unit is live-measuring -> the drop is real, NOT the parked latch.
    # Regression: conservation-of-mass here false-positived and suppressed a true 22 L reading as a
    # stale 29 L on an online, in-use van (2026-07-17, live at the van).
    assert freshness.implausible_water_drop(w(22, 2), w(29, 0)) is False
    # grey FROZEN while fresh drops -> the unpowered-latch signature -> stale, at any drop size
    assert freshness.implausible_water_drop(w(10, 0), w(29, 0)) is True
    # refill / active re-measure: fresh up or equal -> plausible
    assert freshness.implausible_water_drop(w(25, 1), w(17, 1)) is False
    assert freshness.implausible_water_drop(w(17, 1), w(17, 1)) is False
    # missing levels -> can't judge -> not flagged
    assert freshness.implausible_water_drop({"fresh": {"liters": None}}, w(17, 1)) is False
    # grey FELL (tank emptied at a dump station) -> the unit is live-measuring -> the fresh drop is
    # real, NOT the latch. Regression: `ng <= pg` classified any grey fall as "frozen", so after a
    # real grey dump every subsequent genuine reading re-latched and the hold WEDGED for a month
    # (live evidence 2026-08-16: unit fresh 0 L/waste 2 L vs a held July-18 baseline 10 L/9 L).
    assert freshness.implausible_water_drop(w(0, 2), w(10, 9)) is False
    # grey fall alone (fresh flat) was never a latch candidate; still plausible
    assert freshness.implausible_water_drop(w(10, 2), w(10, 9)) is False
