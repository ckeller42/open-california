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
        # Volume is always the tank capacity in liters. None-tolerant: a truncated
        # frame must not KeyError and kill the whole poll (all functions).
        if level is None or volume is None:
            return {"liters": None, "capacity_l": volume, "percent": None}
        if unit:
            liters, pct = level, _pct(level, volume)
        else:
            pct, liters = level, round(volume * level / 100)
        return {"liters": liters, "capacity_l": volume, "percent": pct}
    return {
        "installed": bool(d.get("Installed")),
        "fresh": tank(d.get("FreshWaterUnit"), d.get("FreshWaterLevel"), d.get("FreshWaterVolume")),
        "waste": tank(d.get("WasteWaterUnit"), d.get("WasteWaterLevel"), d.get("WasteWaterVolume")),
    }


def energy(d: dict) -> dict:
    stale = d.get("AgeOneBattValuesMinutes", 255) >= 255
    # battery-1 = STARTER ("default car"/vehicle) battery: only measured with
    # terminal-15 (engine on); off => sentinels (I=0x81 / U=48). battery-2 = LEISURE
    # (second) battery: always live. Suppress only the starter's sentinel garbage,
    # never the real leisure/source signals (solar stays even when not fitted).
    _b1 = d.get("IOneBattBemAfs")
    b1_valid = _b1 is not None and _b1 != 0x81   # missing (short frame) -> not valid, not 0.0
    # A source's raw current reads a not-fitted sentinel (observed 511 / 0x1FF over
    # 14 d telemetry: solar_current constant 511 with solar absent) when the source
    # isn't installed. Null the current in that case so the sentinel never surfaces as
    # a reading — the signal stays present (None), like the batt1 0x81 handling. The
    # *_power fields read a clean 0 when absent, so they're left as-is.
    dcdc_i, shore_i, solar_i = (bool(d.get("DcdcInstalled")), bool(d.get("LadInstalled")),
                                bool(d.get("PvInstalled")))
    # Scales/signs/enums verified against the app view-model xf/d.java (2026-07-08):
    #   powers = raw*10 W (PDcdc signed, PLand/PPv unsigned); ITwoBatt signed /10 A;
    #   ILand/IPv UNSIGNED /10 A; IDcdc signed, NO /10 (a +2 correction applies for SW
    #   version 0409/0410 — not wired here, needs the general SW version); SoC 0-10 -> *10 %.
    def soc_pct(level):
        return level * 10 if isinstance(level, int) and 0 <= level <= 10 else None

    def src_state(v):   # bf/a.java: 0=inactive 1=active 2=standby 6=init 7/else=error
        return None if v is None else {0: "inactive", 1: "active", 2: "standby", 6: "init"}.get(v, "error")

    soc1, soc2 = d.get("SocOneBattAfs"), d.get("SocTwoBattAfs")
    return {
        "installed": True,
        "stale": stale,               # True => STARTER values are last-known, not live
        "age_min": d.get("AgeOneBattValuesMinutes"),
        # --- STARTER (default car) battery: engine-on only ---
        "batt1_v": round(d.get("UOneBattBemAfs", 0) * 0.1, 1) if b1_valid else None,
        "batt1_current": _signed(d.get("IOneBattBemAfs", 0), 8) if b1_valid else None,  # signed A, no scale
        "soc1_level": soc1,                        # coarse 0-15 (11-15 invalid)
        "soc1_pct": soc_pct(soc1),                 # 0-10 -> 0/10/../100 %; else None
        # --- LEISURE (second) battery: always live ---
        "batt2_v": round(d.get("UTwoBattBemAfs", 0) * 0.1, 1),
        "batt2_current": round(_signed(d.get("ITwoBattBemAfs", 0), 16) * 0.1, 1),  # signed A (/10)
        "soc2_level": soc2,
        "soc2_pct": soc_pct(soc2),
        "batt2_remaining_h": d.get("tTwoBattRemainingh"),
        "batt2_remaining_min": d.get("tTwoBattRemainingmin"),
        # --- sources feeding the leisure battery (DC-DC=vehicle/alternator, Land=shore, Pv=solar) ---
        "dcdc_charging": src_state(d.get("StateDcdcAfs")) == "active",   # active(1) only, not "nonzero"
        "dcdc_state": src_state(d.get("StateDcdcAfs")),
        "shore_state": src_state(d.get("StateLandAfs")),
        "solar_state": src_state(d.get("StatePvAfs")),
        # powers = raw*10 W, but 0 when the source isn't installed (the raw reads a 254
        # not-fitted sentinel — observed live: PPv=254 with solar absent — which *10 would
        # surface as phantom watts). dcdc signed; land/pv unsigned.
        "dcdc_power": _signed(d.get("PDcdcAfs", 0), 8) * 10 if dcdc_i else 0,
        "shore_power": d.get("PLandAfs", 0) * 10 if shore_i else 0,
        "solar_power": d.get("PPvAfs", 0) * 10 if solar_i else 0,
        "dcdc_current": _signed(d.get("IDcdcAfs", 0), 16) if dcdc_i else None,   # signed A, no /10; +2 on AmbSwVersion 0409/0410 via apply_sw_corrections()
        "shore_current": round(d.get("ILandAfs", 0) * 0.1, 1) if shore_i else None,   # unsigned A (/10)
        "solar_current": round(d.get("IPvAfs", 0) * 0.1, 1) if solar_i else None,     # unsigned A (/10)
        "dcdc_installed": dcdc_i,
        "shore_installed": shore_i,
        "solar_installed": solar_i,
        "energy_mode": d.get("EnergyMode"),                     # 0=normal 1=max_charge 2=eco 3=error (bf/c.java)
        "energy_mode_locked": bool(d.get("EnergyModeNotSelectable")),
        "warning_level": d.get("WarningLevelTwo"),              # 0=none 1=level1 2=level2 (bf/d.java)
        "warning_active": bool(d.get("WarningLevelActive")),
        "derating_temp_active": bool(d.get("CurrentDeratingTemperature")),
        "sleep_warning": bool(d.get("SleepWarning")),
        "faults": [k for k in ("SystemError", "DcdcDefect", "PvDefect", "LandDefect",
                               "LandNotAvailable", "TwoBattNotCharged", "TwoBattSwitchAtCharging",
                               "TwoBattSwitchAtWorkshop", "WarningLevelTwo", "WarningLevelActive",
                               "SleepWarning", "CurrentDeratingTemperature") if d.get(k)],
    }


# Cooler Error is a 2-bit enum (bits 0-1 of char 1102): 0=ok, 1=generic error, 2=emergency
# operation, 3=door open (COOLER_ERROR/EMERGENCY_OPERATION/DOOR_OPEN IDs). The app surfaces it
# whenever the box is INSTALLED — vf/c.java:423 gates the error-notification dispatch on the
# Installed bit (state bit 4), NOT on State (power, bit 7); the UI error flags H()/h0() are
# ungated. So a fault on an installed-but-powered-off fridge (e.g. door open, Error==3) still shows.
_COOLER_FAULT = {1: "error", 2: "emergency", 3: "door_open"}


def cooler(d: dict) -> dict:
    on = d.get("State") == 1
    # Fault is gated on Installed, not power — mirrors the app (vf/c.java:423). Fixed 2026-08-17
    # from the consistency audit: gating on State hid faults on an installed-but-off fridge.
    fault = _COOLER_FAULT.get(d.get("Error")) if d.get("Installed") else None
    return {
        "installed": bool(d.get("Installed")),
        "on": on,
        "level": d.get("Level"),        # 1-5 cooling level
        "mode": d.get("Mode"),
        "timer_active": bool(d.get("TimerState")),
        # Readback of the schedule the unit currently holds (state char, NOT the control echo):
        "quiet_from": d.get("NightTimerHourOn"),    # quiet-mode schedule start hour (0-23)
        "quiet_to": d.get("NightTimerHourOff"),     # quiet-mode schedule end hour (0-23)
        "quiet_scheduled": bool(d.get("NightTimerSet")),
        "timer_hour": d.get("TimerHourSet"),        # configured cooling-timer start time
        "timer_min": d.get("TimerMinSet"),
        "fault": fault,                 # None | "error" | "emergency" | "door_open"
        "door_open": fault == "door_open",
        "error": bool(fault),           # back-compat: any active fault
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
        "running_time": d.get("RunningTime"),           # configured run duration (min)
        # Timer readback the unit reports (state char 1702; cross-checked bit-exact vs the app's
        # rf/b.java:~395 decoder: RunningTime@24, TimerHour@32, TimerMin@40, RunningTimeinAction@48):
        "timer_hour": d.get("TimerHour"),               # configured start-at hour
        "timer_min": d.get("TimerMin"),
        "running_time_remaining": d.get("RunningTimeinAction"),   # counts down while heating
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


# Roof Position -> name (ig/c.java l() + hf/b.java enum): 0/14=closed, 1=open, 2=middle,
# 15=error, everything else (3-13, transitional) = other.
_ROOF_POS = {0: "closed", 1: "open", 2: "middle", 14: "closed", 15: "error"}
# Roof InfoPopUp alert enum (ig/c.java, 4-bit InfoPopUp), only meaningful while installed:
# 0=none 1=child_lock 4=error 6=sensor_error 7=emergency_locked 10=not_possible 11=low_battery.
_ROOF_ALERT = {1: "child_lock", 4: "error", 6: "sensor_error", 7: "emergency_locked",
               10: "not_possible", 11: "low_battery"}


def roof(d: dict) -> dict:
    """Interpret the pop-top roof state (BLE char ``00001402``).

    Adds the app's human-facing readouts on top of the raw ``Position``: a
    ``position_name`` (``closed``/``open``/``middle``/``error``/``other``) and the
    ``InfoPopUp`` alert enum (child-lock, sensor error, low battery, ...), the alert
    surfaced only while the roof is installed. Mapping taken from the decompiled roof
    view-model ``ig/c.java`` (``l()`` + the ``InfoPopUp`` branch) and ``hf/b.java``.

    :param d: decoded field map for the ``roof`` function (``Position``, ``Installed``,
        ``InfoPopUp``, ``SafetyCounterValid``).
    :returns: interpreted dict with ``position``, ``position_name``, ``alert``
        (``None`` when clear/uninstalled) and ``safety_valid``.

    .. req:: Interpret roof position + InfoPopUp alert
       :id: R_ROOF_ALERT
       :status: implemented
       :tags: ble, telemetry, roof

       ``calictl`` shall map the roof ``Position`` to a human name and the ``InfoPopUp``
       field to the app's alert enum (child-lock/error/sensor-error/emergency-locked/
       not-possible/low-battery), surfacing the alert only while the roof is installed.
    """
    installed = bool(d.get("Installed"))
    pos = d.get("Position")
    return {
        "installed": installed,
        "position": pos,                          # raw 0-15 (0 = closed/down)
        "position_name": _ROOF_POS.get(pos, "other") if pos is not None else None,
        # alert is the app's InfoPopUp popup, gated on the roof being fitted (ig/c.java gate)
        "alert": _ROOF_ALERT.get(d.get("InfoPopUp")) if installed else None,
        "safety_valid": bool(d.get("SafetyCounterValid")),
    }


_LZONES = {"One": 1, "Two": 2, "Three": 3, "Four": 4, "Five": 5, "Six": 6, "Seven": 7,
           "Eight": 8, "Nine": 9, "OneZero": 10, "OneOne": 11, "OneTwo": 12, "OneThree": 13,
           "OneFour": 14, "OneFive": 15, "OneSix": 16}


# L1-L8 are the installed lamps on this van (reading/kitchen/roof-ambient/outside — confirmed
# by the HCI capture 2026-07-08). L9-L16 read constant not-installed defaults (0 or 13) that
# ignore SET_BRIGHTNESS, so they must NOT count toward "any light on".
_REAL_LIGHT_ZONES = frozenset(range(1, 9))


def lighting(d: dict) -> dict:
    out = {"installed": True, "profile": d.get("ProfileNumber"), "mode": d.get("Mode")}
    any_on = False
    for suf, num in _LZONES.items():
        v = d.get("BrightnessL" + suf)
        out["brightness_zone_%d" % num] = v
        if v and v not in (13, 14) and num in _REAL_LIGHT_ZONES:   # 13=NOT_EQUIPPED, 14=sentinel
            any_on = True
    out["any_on"] = any_on
    return out


def _sw_ascii(v):
    """AmbSwVersion/CmSwVersion are 4 ASCII bytes packed into a 32-bit field -> a 4-char string
    (e.g. 0x30343130 -> "0410"), NOT a numeric ÷100 value (verified vs the app's zf/d.g()
    2026-08-17). Returns the decoded string, or None if absent/non-printable."""
    if not isinstance(v, int):
        return None
    s = bytes([(v >> 24) & 0xFF, (v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF]).decode("ascii", "replace")
    return s if s.isprintable() else None


def general(d: dict) -> dict:
    return {
        "installed": True,
        "comm_version": d.get("CommunicationVersion"),      # 1-byte int
        "cm_sw_version": _sw_ascii(d.get("CmSwVersion")),    # 4 ASCII bytes -> "0207"
        "amb_sw_version": _sw_ascii(d.get("AmbSwVersion")),  # 4 ASCII bytes -> "0410" (feeds the +2 gate)
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
        # System is a 2-bit ENUM, not a bool — the app's getter is `System == 1` (mg/f.java),
        # so values 2/3 are NOT "on". `bool()` (any-nonzero) was wrong for 2/3. Also surface the raw.
        "system": d.get("System"),
        "system_on": d.get("System") == 1,
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
        ``car_variant``, ``level_roll``/``level_pitch`` (degrees, signed), ``car_clock``
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

    def deg(v):
        # roll/pitch are signed-16 hundredths of a degree (0.01° resolution). Verified against
        # the app's Niveauanzeige (HCI capture 2026-07-08): raw -109 -> -1.09° ~ app roll 1.1°,
        # raw 35 -> 0.35° ~ app pitch 0.3°. The old "-57/-256" were just -0.57°/-2.56° unscaled.
        s = s16(v)
        return None if s is None else round(s / 100.0, 2)
    y, mo, da = d.get("CarTimeYear"), d.get("CarTimeMonth"), d.get("CarTimeDay")
    h, mi, se = d.get("CarTimeHour"), d.get("CarTimeMinute"), d.get("CarTimeSecond")
    clock = None
    # A day field of 0 means the RTC is unset (ignition off / asleep the unit reports all-zero
    # time); don't format that as the nonsensical "1900-01-00 00:00:00" — leave it unavailable.
    if None not in (y, mo, da, h, mi, se) and da:
        # app applies +1900 to the year field and +1 to the month field
        clock = "%04d-%02d-%02d %02d:%02d:%02d" % (y + 1900, mo + 1, da, h, mi, se)
    return {
        "installed": True,
        "ignition_on": bool(d.get("TerminalOneFive")),   # terminal-15 line
        "car_variant": d.get("CarVariant"),
        "level_popup": d.get("CarLevelPopUp"),
        "level_roll": deg(d.get("CarLevelRoll")),         # degrees (signed, 0.01° resolution)
        "level_pitch": deg(d.get("CarLevelPitch")),
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


# Firmware versions (AmbSwVersion) on which the app adds +2 to the DC-DC current. Verified against
# the app 2026-08-17: xf/d.java:171 gates on `["0409","0410"]`, and the field is AmbSwVersion (not
# Cm/Communication) — resolved by SootUp def-use through gj/b -> pf/k -> zf/d.g() -> cVar.f1297a.
_DCDC_PLUS2_SW = frozenset({"0409", "0410"})


def apply_sw_corrections(states: dict) -> dict:
    """Cross-function firmware-version corrections over a FULL interpreted-states dict (mutates &
    returns it). Kept out of the per-function ``energy()`` because the correction depends on the
    ``general`` service's SW version — data ``energy()`` alone doesn't have.

    Currently: DC-DC current += 2 on AmbSwVersion 0409/0410 (this van is 0410, so it applies). A
    single-function caller (``cli get energy``) that lacks ``general`` context simply skips it.
    """
    en, gen = states.get("energy"), states.get("general")
    if (isinstance(en, dict) and isinstance(gen, dict)
            and en.get("dcdc_current") is not None
            and gen.get("amb_sw_version") in _DCDC_PLUS2_SW):
        en["dcdc_current"] = en["dcdc_current"] + 2
    return states


def is_installed(function: str, decoded: dict) -> bool | None:
    """Whether the vehicle actually has this feature (per-function Installed
    bit). None if the function has no Installed field to judge by."""
    if "Installed" in decoded:
        return bool(decoded["Installed"])
    if function == "energy":
        return True
    return None
