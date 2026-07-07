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
    assert len(f) == 13
    assert f["water"].state_char.startswith("00001302")
    assert f["cooler"].control_char.startswith("00001101")


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
    assert P.encode(f["cooler"], base, frame_bytes=6).hex() == "3d44000f1e00"
    assert P.encode(f["cooler"], {**base, "State": 0}, frame_bytes=6).hex() == "3c44000f1e00"
    assert P.encode(f["cooler"], {**base, "Level": 3}, frame_bytes=6).hex() == "3d43000f1e00"


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
    # when installed, the current is a real signed int (not None)
    d = P.decode(f["energy"], ENERGY_IGN_ON); d["PvInstalled"] = 1; d["IPvAfs"] = 5
    assert semantics.energy(d)["solar_current"] == 5


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


def test_points_for_reuses_numeric_fields():
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


def test_heartbeat_counter_bytes():
    # 1003 liveness counter: 4-byte big-endian, monotonic +1, wraps at 32 bits.
    from calictl import device
    assert device._beat_bytes(device.HEARTBEAT_START) == bytes.fromhex("00100000")
    assert device._beat_bytes(device.HEARTBEAT_START + 1) == bytes.fromhex("00100001")
    assert device._beat_bytes(0x1_00000000) == bytes.fromhex("00000000")
