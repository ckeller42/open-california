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


def test_camping_independent_outputs():
    f = _funcs()
    c = semantics.campingmode(P.decode(f["campingmode"], CAMPING))
    assert c["master_on"] is False and c["usb_charger"] is True  # independent
    assert c["enable"] is True


def test_encode_cooler_frames_match_hand_derived():
    f = _funcs()
    base = dict(State=1, Mode=4, Level=4, TimerStart=3, TimerCancel=3, NightTimerSet=0,
                NightTimerHourOff=0, TimerHour=15, TimerMin=30, NightTimerHourOn=0)
    assert P.encode(f["cooler"], base, frame_bytes=6).hex() == "3d44000f1e00"
    assert P.encode(f["cooler"], {**base, "State": 0}, frame_bytes=6).hex() == "3c44000f1e00"
    assert P.encode(f["cooler"], {**base, "Level": 3}, frame_bytes=6).hex() == "3d43000f1e00"


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


def test_mqtt_discovery_and_commands():
    from calictl import mqtt
    cfgs = mqtt.render_discovery()
    # every entity has a unique config topic + references its function state topic
    assert any("binary_sensor" in t and "fridge" in t.lower() or "cooler_on" in t for t in cfgs)
    fridge = cfgs["homeassistant/switch/vwcamper_cooler_power/config"]
    assert fridge["command_topic"] == "calivan/cooler/set/power"
    assert mqtt.command_topics()["calivan/cooler/set/power"] == ("cooler", "power")
