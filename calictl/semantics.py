"""Human-meaningful interpretation of decoded Camper Unit fields.

This is the layer that encodes what we learned by cross-referencing the app
source with live reads — the transforms a raw field dump can't tell you:

- Water: `Level` is the CURRENT reading, `Volume` is tank CAPACITY (names are
  reversed); percent = Level*100/Volume. Verified live.
- Energy: battery voltages are ×0.1 V; DC-DC/shore/solar currents & powers are
  signed; `Age…=255` means the data is STALE (subsystem asleep). Verified when
  ignition-on flipped DC-DC charging + voltage to ~13.6 V.
- Feature availability: each function's `Installed` state bit gates whether the
  vehicle actually has it (this van has no stairs/AC/sat/LR-heater/solar).
- Camping: `State`(master), `UsbCharger`, lights are INDEPENDENT outputs sharing
  one frame; `Enable` is a read-only vehicle signal (tracks terminal-15).
"""
from __future__ import annotations


def _signed(v: int, width: int) -> int:
    return v - (1 << width) if v >= (1 << (width - 1)) else v


def _pct(cur: int, cap: int) -> int | None:
    return round(cur * 100 / cap) if cap else None


def water(d: dict) -> dict:
    def tank(unit, level, volume):
        # unit 1 = ABSOLUTE (Level=liters), 0 = PERCENT (Level=percent).
        # Volume is always the tank capacity in liters.
        if unit:
            liters, pct = level, _pct(level, volume)
        else:
            pct, liters = level, round(volume * level / 100)
        return {"liters": liters, "capacity_l": volume, "percent": pct}
    return {
        "installed": bool(d.get("Installed")),
        "fresh": tank(d["FreshWaterUnit"], d["FreshWaterLevel"], d["FreshWaterVolume"]),
        "waste": tank(d["WasteWaterUnit"], d["WasteWaterLevel"], d["WasteWaterVolume"]),
    }


def energy(d: dict) -> dict:
    stale = d.get("AgeOneBattValuesMinutes", 255) >= 255
    # battery-1 = STARTER ("default car"/vehicle) battery: only measured with
    # terminal-15 (engine on); off => sentinels (I=0x81 / U=48). battery-2 = LEISURE
    # (second) battery: always live. Suppress only the starter's sentinel garbage,
    # never the real leisure/source signals (solar stays even when not fitted).
    b1_valid = d.get("IOneBattBemAfs") != 0x81
    # A source's raw current reads a not-fitted sentinel (observed 511 / 0x1FF over
    # 14 d telemetry: solar_current constant 511 with solar absent) when the source
    # isn't installed. Null the current in that case so the sentinel never surfaces as
    # a reading — the signal stays present (None), like the batt1 0x81 handling. The
    # *_power fields read a clean 0 when absent, so they're left as-is.
    dcdc_i, shore_i, solar_i = (bool(d.get("DcdcInstalled")), bool(d.get("LadInstalled")),
                                bool(d.get("PvInstalled")))
    return {
        "installed": True,
        "stale": stale,               # True => STARTER values are last-known, not live
        "age_min": d.get("AgeOneBattValuesMinutes"),
        # --- STARTER (default car) battery: engine-on only ---
        "batt1_v": round(d.get("UOneBattBemAfs", 0) * 0.1, 1) if b1_valid else None,
        "batt1_current": _signed(d.get("IOneBattBemAfs", 0), 8) if b1_valid else None,
        "soc1_level": d.get("SocOneBattAfs"),      # coarse 0-15
        # --- LEISURE (second) battery: always live ---
        "batt2_v": round(d.get("UTwoBattBemAfs", 0) * 0.1, 1),
        "batt2_current": _signed(d.get("ITwoBattBemAfs", 0), 16),  # draw from leisure batt (raw signed; scale UNVERIFIED)
        "soc2_level": d.get("SocTwoBattAfs"),
        "batt2_remaining_h": d.get("tTwoBattRemainingh"),
        "batt2_remaining_min": d.get("tTwoBattRemainingmin"),
        # --- sources feeding the leisure battery (app: vehiclePower / externalPowerSource / solarPower) ---
        "dcdc_charging": bool(d.get("StateDcdcAfs")),
        "dcdc_power": _signed(d.get("PDcdcAfs", 0), 8),    # app "vehiclePower" (DC-DC from starter/alternator)
        "shore_power": _signed(d.get("PLandAfs", 0), 8),   # app "externalPowerSource" / campsite
        "solar_power": _signed(d.get("PPvAfs", 0), 8),     # app "solarPower" (kept even if not fitted)
        "dcdc_current": _signed(d.get("IDcdcAfs", 0), 16) if dcdc_i else None,
        "shore_current": _signed(d.get("ILandAfs", 0), 16) if shore_i else None,
        "solar_current": _signed(d.get("IPvAfs", 0), 16) if solar_i else None,
        "dcdc_installed": dcdc_i,
        "shore_installed": shore_i,
        "solar_installed": solar_i,
        "energy_mode": d.get("EnergyMode"),                     # 0=eco 1=normal 2=max (per GUI)
        "energy_mode_locked": bool(d.get("EnergyModeNotSelectable")),
        "derating_temp_active": bool(d.get("CurrentDeratingTemperature")),
        "sleep_warning": bool(d.get("SleepWarning")),
        "faults": [k for k in ("SystemError", "DcdcDefect", "PvDefect", "LandDefect")
                   if d.get(k)],
    }


def cooler(d: dict) -> dict:
    return {
        "installed": bool(d.get("Installed")),
        "on": d.get("State") == 1,
        "level": d.get("Level"),        # 1-5 cooling level
        "mode": d.get("Mode"),
        "timer_active": bool(d.get("TimerState")),
        "error": bool(d.get("Error")),
    }


def airheater(d: dict) -> dict:
    return {
        "installed": bool(d.get("Installed")),
        # two independent op bits -> a single "running" state (see AirHeater notes)
        "running": bool(d.get("NormalOperation") or d.get("PermanentOperation")),
        "permanent": bool(d.get("PermanentOperation")),
        "level": d.get("HeatingLevel"),
        "error_code": d.get("ErrorCode"),
        "mode": d.get("OperationModeAirHeater"),
        "air_distribution": d.get("AirDistribution"),
        "running_time": d.get("RunningTime"),
    }


def campingmode(d: dict) -> dict:
    # The app has ONE combined "Lights" toggle, INVERTED: tf/a.java K0(on) writes 0
    # to BOTH InteriorLight+OutsideLight; readback m2() = lit iff both read 0. USB
    # (B2) and master (z2) are normal (on=1). Lights+USB are only actionable in the
    # app while camping mode (master) is on — a UI gate, not a per-field guard.
    master = bool(d.get("State"))
    return {
        "installed": bool(d.get("Installed")),
        "master_on": master,
        "usb_charger": bool(d.get("UsbCharger")),                 # normal: on=1
        # inverted+combined AND only meaningful while master on (fields default to 0
        # when camping is off, which would otherwise read as a false "lit").
        "lights_on": master and d.get("InteriorLight") == 0 and d.get("OutsideLight") == 0,
        "outputs_controllable": master,                            # lights/USB toggle only when master on
        "enable": bool(d.get("Enable")),          # read-only vehicle signal (terminal-15)
    }


def roof(d: dict) -> dict:
    return {
        "installed": bool(d.get("Installed")),
        "position": d.get("Position"),            # 0 = closed/down
        "safety_valid": bool(d.get("SafetyCounterValid")),
    }


_LZONES = {"One": 1, "Two": 2, "Three": 3, "Four": 4, "Five": 5, "Six": 6, "Seven": 7,
           "Eight": 8, "Nine": 9, "OneZero": 10, "OneOne": 11, "OneTwo": 12, "OneThree": 13,
           "OneFour": 14, "OneFive": 15, "OneSix": 16}


def lighting(d: dict) -> dict:
    out = {"installed": True, "profile": d.get("ProfileNumber"), "mode": d.get("Mode")}
    any_on = bool(d.get("LightValue"))
    for suf, num in _LZONES.items():
        v = d.get("BrightnessL" + suf)
        out["brightness_zone_%d" % num] = v
        if v:
            any_on = True
    out["any_on"] = any_on
    return out


def general(d: dict) -> dict:
    # SwVersion-style fields are raw (÷100 for display); left raw here.
    return {
        "installed": True,
        "comm_version": d.get("CommunicationVersion"),
        "cm_sw_version": d.get("CmSwVersion"),
        "amb_sw_version": d.get("AmbSwVersion"),
    }


def livingroomheater(d: dict) -> dict:
    return {
        "installed": bool(d.get("Installed")),
        "air_on": bool(d.get("StateAir")),
        "water_on": bool(d.get("StateWater")),
        "air_temp": d.get("TemperatureAir"),        # scale UNVERIFIED
        "water_temp": d.get("TemperatureWater"),    # scale UNVERIFIED
        "mode": d.get("Mode"),
        "error": bool(d.get("Error")),
    }


def roofaircondition(d: dict) -> dict:
    return {
        "installed": bool(d.get("Installed")),
        "on": bool(d.get("State")),
        "mode": d.get("Mode"),
        "fan_speed": d.get("Fanspeed"),
        "target_temp": d.get("Temperature"),        # scale UNVERIFIED
        "error": bool(d.get("Error")),
    }


def satelliteantenna(d: dict) -> dict:
    # UNVERIFIED polarity: mg/f.java has an inverted readback getter (!((Boolean)…)), so
    # one of these booleans is likely inverted in the app. Sat is NOT installed on this
    # van -> can't confirm which. See docs/business-logic/signal-scales.md.
    return {
        "installed": bool(d.get("Installed")),
        "dish": d.get("Dish"),
        "satellite": d.get("SatelliteSelection"),
        "signal_level": d.get("SignalLevel"),
        "system_on": bool(d.get("System")),
        "error": bool(d.get("Error")),
    }


def stairs(d: dict) -> dict:
    # UNVERIFIED polarity: og/b.java has an inverted readback getter (!((Boolean)…)) plus
    # inverted setters — one of these booleans is likely inverted. Stairs is NOT installed
    # on this van -> can't confirm which. See docs/business-logic/signal-scales.md.
    return {
        "installed": bool(d.get("Installed")),
        "extended": bool(d.get("State")),
        "mode": d.get("OperationMode"),
        "obstacle_sensor": bool(d.get("Sensor")),
    }


def vehicle(d: dict) -> dict:
    """Interpret the vehicle-state characteristic (BLE char ``00001004``).

    Decodes the ignition line (terminal-15), the car variant, the unit's
    real-time clock and the two-axis leveling readout. Bit layout is taken from
    the app's ``ag/a.java`` cell model + ``zf/d.java`` parser and was validated
    on-device 2026-07-07 (the RTC decoded to the correct wall-clock time). Roll
    and pitch are signed 16-bit.

    :param d: decoded field map from :func:`calictl.protocol.decode` for the
        ``vehicle`` function (keys ``TerminalOneFive``, ``CarVariant``,
        ``CarTime*``, ``CarLevelRoll``, ``CarLevelPitch``).
    :returns: interpreted dict with ``ignition_on`` (terminal-15),
        ``car_variant``, ``level_roll``/``level_pitch`` (signed), ``car_clock``
        (ISO-ish ``YYYY-MM-DD HH:MM:SS``) and ``level_popup``.

    .. req:: Decode vehicle state (char 1004)
       :id: R_VEHICLE_1004
       :status: implemented
       :tags: ble, telemetry, vehicle

       ``calictl`` shall decode BLE characteristic ``00001004`` into the ignition
       state (terminal-15), car variant, unit real-time clock and the two-axis
       (roll/pitch) leveling readout, exposing them as interpreted signals.
    """
    def s16(v):
        return None if v is None else (v - 65536 if v >= 32768 else v)
    y, mo, da = d.get("CarTimeYear"), d.get("CarTimeMonth"), d.get("CarTimeDay")
    h, mi, se = d.get("CarTimeHour"), d.get("CarTimeMinute"), d.get("CarTimeSecond")
    clock = None
    if None not in (y, mo, da, h, mi, se):
        # app applies +1900 to the year field and +1 to the month field
        clock = "%04d-%02d-%02d %02d:%02d:%02d" % (y + 1900, mo + 1, da, h, mi, se)
    return {
        "installed": True,
        "ignition_on": bool(d.get("TerminalOneFive")),   # terminal-15 line
        "car_variant": d.get("CarVariant"),
        "level_popup": d.get("CarLevelPopUp"),
        "level_roll": s16(d.get("CarLevelRoll")),         # signed; 0 = level/unknown
        "level_pitch": s16(d.get("CarLevelPitch")),
        "car_clock": clock,
    }


def _generic(d: dict) -> dict:
    # surface fields inline (installed flag first) so status isn't blank
    out = {}
    if "Installed" in d:
        out["installed"] = bool(d["Installed"])
    out.update({k: v for k, v in d.items() if k != "Installed"})
    return out


INTERPRETERS = {
    "water": water, "energy": energy, "cooler": cooler, "airheater": airheater,
    "campingmode": campingmode, "roof": roof, "lighting": lighting, "general": general,
    "livingroomheater": livingroomheater, "roofaircondition": roofaircondition,
    "satelliteantenna": satelliteantenna, "stairs": stairs, "vehicle": vehicle,
}


def interpret(function: str, decoded: dict) -> dict:
    """Raw decoded fields -> human-meaningful dict. Falls back to a generic
    Installed+raw view for functions without a dedicated interpreter."""
    return INTERPRETERS.get(function, _generic)(decoded)


def is_installed(function: str, decoded: dict) -> bool | None:
    """Whether the vehicle actually has this feature (per-function Installed
    bit). None if the function has no Installed field to judge by."""
    if "Installed" in decoded:
        return bool(decoded["Installed"])
    if function == "energy":
        return True
    return None
