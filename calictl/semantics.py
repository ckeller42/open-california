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
    return {
        "installed": True,
        "stale": stale,               # True => SoC/V/A below are last-known, not live
        "age_min": d.get("AgeOneBattValuesMinutes"),
        "batt1_v": round(d.get("UOneBattBemAfs", 0) * 0.1, 1),
        "batt2_v": round(d.get("UTwoBattBemAfs", 0) * 0.1, 1),
        "soc1_level": d.get("SocOneBattAfs"),      # coarse 0-15
        "soc2_level": d.get("SocTwoBattAfs"),
        "dcdc_charging": bool(d.get("StateDcdcAfs")),
        "dcdc_power": _signed(d.get("PDcdcAfs", 0), 8),
        "shore_power": _signed(d.get("PLandAfs", 0), 8),
        "solar_power": _signed(d.get("PPvAfs", 0), 8),
        "batt1_current": _signed(d.get("IOneBattBemAfs", 0), 8),
        "dcdc_installed": bool(d.get("DcdcInstalled")),
        "shore_installed": bool(d.get("LadInstalled")),
        "solar_installed": bool(d.get("PvInstalled")),
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
    }


def campingmode(d: dict) -> dict:
    return {
        "installed": bool(d.get("Installed")),
        "master_on": bool(d.get("State")),        # independent of the outputs below
        "usb_charger": bool(d.get("UsbCharger")),
        "interior_light": bool(d.get("InteriorLight")),
        "outside_light": bool(d.get("OutsideLight")),
        "enable": bool(d.get("Enable")),          # read-only vehicle signal (terminal-15)
    }


def roof(d: dict) -> dict:
    return {
        "installed": bool(d.get("Installed")),
        "position": d.get("Position"),            # 0 = closed/down
        "safety_valid": bool(d.get("SafetyCounterValid")),
    }


def _generic(d: dict) -> dict:
    out = {"installed": bool(d.get("Installed"))} if "Installed" in d else {}
    out["raw"] = d
    return out


INTERPRETERS = {
    "water": water, "energy": energy, "cooler": cooler, "airheater": airheater,
    "campingmode": campingmode, "roof": roof,
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
