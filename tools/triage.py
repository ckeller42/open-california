"""One-shot triage of the auto-seeded signal catalog.

Encodes the surface/omit decision for every field, cross-referenced against the
four sources during the 2026-07-06 audit. SURFACE names are the canonical keys
`semantics.interpret` emits (the coverage guardrail asserts name in emitted-set);
everything not surfaced is omitted with a concrete, category-based reason.

Run: `python3 -m tools.triage`  (rewrites protocol/signals.yaml)
"""
from __future__ import annotations
import os, yaml
from tools import catalog

# field -> (canonical_name, unit, scale, kind).  scale "raw" or a multiplier or UNVERIFIED.
SURFACE = {
    "water": {
        "FreshWaterLevel": ("fresh_percent", "%", "derived", "percent"),
        "FreshWaterVolume": ("fresh_capacity_l", "L", "raw", "level"),
        "WasteWaterLevel": ("waste_percent", "%", "derived", "percent"),
        "WasteWaterVolume": ("waste_capacity_l", "L", "raw", "level"),
    },
    "energy": {
        "UOneBattBemAfs": ("batt1_v", "V", "0.1", "battery"),
        "IOneBattBemAfs": ("batt1_current", "A", "raw", "battery"),
        "SocOneBattAfs": ("soc1_level", None, "raw", "level"),
        "UTwoBattBemAfs": ("batt2_v", "V", "0.1", "leisure_battery"),
        "ITwoBattBemAfs": ("batt2_current", "A", "UNVERIFIED", "leisure_battery"),
        "SocTwoBattAfs": ("soc2_level", None, "raw", "level"),
        "tTwoBattRemainingh": ("batt2_remaining_h", "h", "raw", "level"),
        "tTwoBattRemainingmin": ("batt2_remaining_min", "min", "raw", "level"),
        "StateDcdcAfs": ("dcdc_charging", None, "raw", "state"),
        "PDcdcAfs": ("dcdc_power", "W", "raw", "source"),
        "PLandAfs": ("shore_power", "W", "raw", "source"),
        "PPvAfs": ("solar_power", "W", "raw", "source"),
        "IDcdcAfs": ("dcdc_current", "A", "UNVERIFIED", "source"),
        "ILandAfs": ("shore_current", "A", "UNVERIFIED", "source"),
        "IPvAfs": ("solar_current", "A", "UNVERIFIED", "source"),
        "AgeOneBattValuesMinutes": ("age_min", "min", "raw", "level"),
        "EnergyMode": ("energy_mode", None, "raw", "state"),
        "EnergyModeNotSelectable": ("energy_mode_locked", None, "raw", "state"),
        "CurrentDeratingTemperature": ("derating_temp_active", None, "raw", "state"),
        "SleepWarning": ("sleep_warning", None, "raw", "state"),
        "DcdcInstalled": ("dcdc_installed", None, "raw", "state"),
        "LadInstalled": ("shore_installed", None, "raw", "state"),
        "PvInstalled": ("solar_installed", None, "raw", "state"),
    },
    "cooler": {
        "State": ("on", None, "raw", "state"),
        "Level": ("level", None, "raw", "level"),
        "Mode": ("mode", None, "raw", "state"),
        "TimerState": ("timer_active", None, "raw", "state"),
        "Error": ("error", None, "raw", "state"),
    },
    "campingmode": {
        "State": ("master_on", None, "raw", "state"),
        "UsbCharger": ("usb_charger", None, "raw", "state"),
        "InteriorLight": ("interior_light", None, "raw", "state"),
        "OutsideLight": ("outside_light", None, "raw", "state"),
        "Enable": ("enable", None, "raw", "state"),
    },
    "airheater": {
        "NormalOperation": ("running", None, "raw", "state"),
        "PermanentOperation": ("permanent", None, "raw", "state"),
        "HeatingLevel": ("level", None, "raw", "level"),
        "ErrorCode": ("error_code", None, "raw", "state"),
        "OperationModeAirHeater": ("mode", None, "raw", "state"),
        "AirDistribution": ("air_distribution", None, "raw", "state"),
        "RunningTime": ("running_time", "min", "raw", "level"),
    },
    "livingroomheater": {
        "StateAir": ("air_on", None, "raw", "state"),
        "StateWater": ("water_on", None, "raw", "state"),
        "TemperatureAir": ("air_temp", "°C", "UNVERIFIED", "temperature"),
        "TemperatureWater": ("water_temp", "°C", "UNVERIFIED", "temperature"),
        "Mode": ("mode", None, "raw", "state"),
        "Error": ("error", None, "raw", "state"),
    },
    "roofaircondition": {
        "State": ("on", None, "raw", "state"),
        "Mode": ("mode", None, "raw", "state"),
        "Fanspeed": ("fan_speed", None, "raw", "level"),
        "Temperature": ("target_temp", "°C", "UNVERIFIED", "temperature"),
        "Error": ("error", None, "raw", "state"),
    },
    "roof": {
        "Position": ("position", None, "raw", "level"),
        "SafetyCounterValid": ("safety_valid", None, "raw", "state"),
    },
    "lighting": {
        "LightValue": ("any_on", None, "raw", "state"),
        "ProfileNumber": ("profile", None, "raw", "state"),
        "Mode": ("mode", None, "raw", "state"),
    },
    "satelliteantenna": {
        "Dish": ("dish", None, "raw", "state"),
        "SatelliteSelection": ("satellite", None, "raw", "state"),
        "SignalLevel": ("signal_level", None, "raw", "level"),
        "System": ("system_on", None, "raw", "state"),
        "Error": ("error", None, "raw", "state"),
    },
    "stairs": {
        "State": ("extended", None, "raw", "state"),
        "OperationMode": ("mode", None, "raw", "state"),
        "Sensor": ("obstacle_sensor", None, "raw", "state"),
    },
    "general": {
        "CommunicationVersion": ("comm_version", None, "raw", "state"),
        "CmSwVersion": ("cm_sw_version", None, "raw", "state"),
        "AmbSwVersion": ("amb_sw_version", None, "raw", "state"),
    },
}
# lighting per-zone brightness -> zone_NN (surface all 16)
_ZMAP = {"One": 1, "Two": 2, "Three": 3, "Four": 4, "Five": 5, "Six": 6, "Seven": 7,
         "Eight": 8, "Nine": 9, "OneZero": 10, "OneOne": 11, "OneTwo": 12, "OneThree": 13,
         "OneFour": 14, "OneFive": 15, "OneSix": 16}
for suf, num in _ZMAP.items():
    SURFACE["lighting"]["BrightnessL" + suf] = ("brightness_zone_%d" % num, None, "raw", "level")

# explicit omit reasons by category (checked in order; else a default).
def omit_reason(fn, field, kind="state"):
    f = field
    if kind == "control":
        return ("control command field — definition captured in dictionary/overrides; "
                "command surfacing deferred while control writes are blocked (issue #2)")
    if fn == "generalpurposesignals":
        return "opaque general-purpose signal — no GUI widget or getter; meaning undetermined"
    if f.endswith("InfoPopUp"):
        return "UI transient popup flag, not telemetry"
    if f == "Timestamp":
        return "frame timestamp, not a user-facing signal"
    if f == "Installed":
        return "per-function feature-availability flag — surfaced as the 'installed' key"
    if f in ("EmpInstalled",):
        return "EMP feature-availability flag — not a measurement"
    if f in ("DcdcDefect", "LandDefect", "PvDefect", "SystemError", "FaultTriggerBit",
             "LandNotAvailable", "TwoBattNotCharged", "WarningLevelActive", "WarningLevelTwo",
             "TwoBattSwitchAtCharging", "TwoBattSwitchAtWorkshop", "StatePvAfs", "StateLandAfs"):
        return "fault/warning/source-state bit — aggregated into the faults[] / source status, not standalone"
    if f in ("NightTimerHourOff", "NightTimerHourOn", "NightTimerSet", "TimerCounterHour",
             "TimerCounterMin", "TimerElapsed", "TimerHourSet", "TimerMinSet",
             "RunningTimeinAction", "PermanentOperationRequestConfirmation",
             "OperationModeCombined"):
        return "granular timer/mode detail — surfaced via the aggregate state, not standalone"
    return "control/telemetry field with no GUI or getter evidence; not surfaced (revisit if needed)"

def main():
    root = os.path.join(os.path.dirname(__file__), "..")
    cat = catalog.load_catalog()
    for fn, kinds in cat.items():
        for kind, fields in (kinds or {}).items():
            for field in fields:
                s = SURFACE.get(fn, {}).get(field)
                if s and kind == "state":
                    name, unit, scale, kd = s
                    fields[field] = {"decision": "surface", "name": name, "unit": unit,
                                     "scale": scale, "kind": kd,
                                     "sources": fields[field].get("sources", {}),
                                     "confidence": "medium" if scale == "UNVERIFIED" else "high"}
                else:
                    fields[field] = {"decision": "omit", "reason": omit_reason(fn, field, kind),
                                     "sources": fields[field].get("sources", {})}
    with open(os.path.join(root, "protocol", "signals.yaml"), "w") as fh:
        yaml.safe_dump(cat, fh, sort_keys=True, default_flow_style=False)
    surf = sum(1 for ks in cat.values() for fs in ks.values() for e in fs.values()
               if e["decision"] == "surface")
    print("triaged: %d surface" % surf)

if __name__ == "__main__":
    main()
