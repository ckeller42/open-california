"""Generate docs/features.md: what calictl does today, per camper-unit function, for a reader.

Read-only projection, never hand-edit the page: regenerate it with
``python3 -m tools.gen_features``. Sources:

- ``protocol/signals.yaml`` — per function, the values calictl shows (name + unit);
- :mod:`calictl.control` — the commands (the table below is checked against ``control.build`` and
  ``tests/vectors/control.json`` by ``tests/test_features_page.py``; ranges come from the
  module's own constants);
- :mod:`calictl.mqtt` ``COMMAND_SPECS`` — the Home Assistant entities;
- ``tools.gen_c_dict.ESP_CONTROL_FUNCTIONS`` — the functions the ESP32 satellite writes (the list
  that becomes ``CALI_CTL_CHARS`` in ``csrc/control_consts.h``, which ``ctl_char()`` in
  ``firmware/components/cali_core/control.c`` reads); the wake-up light there needs the web page's
  clock;
- ``calictl/webui/strings.de.js`` — the German term for each label the web UI has;
- :data:`UI_LABELS` — the web UI's label for each value it shows (checked against
  ``calictl/webui/app.js``);
- :data:`FUNCTIONS` — the one hand-kept part: title, proof tier and a one-sentence note per
  function, each checked against ``docs/business-logic/evidence-ledger.md``.

.. req:: A generated page says what calictl does today, per function
   :id: R_FEATURES_PAGE

   ``docs/features.md`` shall be generated from the catalog, the command builders, the Home
   Assistant entity list and the satellite's write list, so it cannot drift from the code; it
   shall say for each function what is shown, what can be controlled, where, and how it is
   proven, in present tense and without issue numbers, dates, decompile class names or catalog
   vocabulary.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from calictl import control, mqtt
from tools._view_common import load_catalog
from tools.gen_c_dict import ESP_CONTROL_FUNCTIONS, ESP_ELSEWHERE_REASON

ROOT = Path(__file__).resolve().parent.parent
OUT_FILE = ROOT / "docs" / "features.md"
STRINGS_DE = ROOT / "calictl" / "webui" / "strings.de.js"
LEDGER = "https://ckeller42.github.io/open-california/business-logic/evidence-ledger.html"

LIVE = "live-verified on the real unit"
READ_LIVE = "readings live-verified; controls verified against the app only"
APP_ONLY = "verified against the app's code only"
NOT_FITTED = "not fitted on this van"

# function -> (title as the web UI names it, tier, one-sentence note). Order = page order.
# Each tier is checked against docs/business-logic/evidence-ledger.md.
FUNCTIONS = {
    "cooler": (
        "Cooler",
        LIVE,
        "Power, cooling level and the quiet-mode hours are confirmed on the unit; the unit stores the"
        " cooling start time, but switching on at that time is not yet observed.",
    ),
    "campingmode": (
        "Camping mode",
        LIVE,
        "Camping mode, its lights and the rear USB ports are confirmed on the unit.",
    ),
    "lighting": (
        "Lighting",
        LIVE,
        "Lamps, all lights, the sliding-door light and the wake-up light are confirmed on the unit;"
        " favourites match the app's own frames; a favourite's colour is from the app's code only.",
    ),
    "airheater": (
        "Air heater",
        LIVE,
        "Calictl has run the heater on the unit (level, run time, start time, timer); the unit starting"
        " an armed timer at its time is not yet observed.",
    ),
    "water": (
        "Water",
        LIVE,
        "The unit measures only while its water system is on; when parked, calictl keeps the last"
        " plausible reading and marks it stale.",
    ),
    "energy": (
        "Energy",
        READ_LIVE,
        "The readings match the app on the real unit; the energy-mode command matches the app's own"
        " frames, and the app offers ECO only on a van with solar.",
    ),
    "roof": (
        "Roof",
        READ_LIVE,
        "The pop-top roof is fitted and its position is confirmed; calictl has never driven the roof"
        " motor. A move needs the ignition on and is press-and-hold.",
    ),
    "vehicle": (
        "Vehicle",
        LIVE,
        "Roll and pitch show only while the ignition is on and the van is not driving.",
    ),
    "general": ("Unit firmware", LIVE, "The software versions the unit reports."),
    "generalpurposesignals": (
        "General-purpose signals",
        APP_ONLY,
        "Calictl decodes this function but shows none of its values.",
    ),
    "stairs": ("Stairs", NOT_FITTED, "Frames and values are checked against the app's code only."),
    "livingroomheater": (
        "Living-room heater",
        NOT_FITTED,
        "Frames and values are checked against the app's code only.",
    ),
    "roofaircondition": (
        "Roof air conditioning",
        NOT_FITTED,
        "Frames and values are checked against the app's code only.",
    ),
    "satelliteantenna": ("Satellite dish", NOT_FITTED, "Calictl decodes it but sends no commands."),
}
# Text under a function's command table.
FOOTNOTES = {
    "lighting": "Lamp levels: 0 = off, 1–10 = 10–100 %%, %d = the lamp's default." % control.LIGHT_MAX_SET,
}

_ONOFF = "on / off"
_LVL = "0–%d" % control.LIGHT_MAX_SET
_ZONE_LABELS = {  # control.LIGHT_ZONES key -> (group, lamp) as the web UI labels them
    "reading-1": ("Reading lights", "Left"),
    "reading-2": ("Reading lights", "Right"),
    "reading-3": ("Reading lights", "Front passenger"),
    "kitchen-ambient": ("Kitchen", "Background lighting"),
    "kitchen-cabinet": ("Kitchen", "Cabinet"),
    "kitchen": ("Kitchen", "Cooking"),
    "roof-ambient": ("Pop-up roof", "Background lighting"),
    "roof-reading": ("Pop-up roof", "Reading light"),
    "outside-rear": ("Exterior light", "Rear surroundings"),
    "entrance": ("Exterior light", "Entrance"),
}


# (function, catalog name) -> the web UI's label for the row or control that shows that value.
# Several values share one row (a battery's volts and amps). A value missing here is not shown by
# the web UI under a label of its own (a banner, a gate or nothing); the page then gives only its
# name. tests/test_features_page.py checks every label against calictl/webui/app.js.
UI_LABELS = {
    "cooler": {
        "level": "Cooling level",
        "mode": "Quiet mode",
        "on": "Refrigerator box",
        "timer_active": "Timer",
    },
    "campingmode": {
        "lights_on": "Exterior and interior lighting",
        "master_on": "Camping mode",
        "usb_charger": "Rear USB ports",
    },
    "lighting": {"any_on": "All lights", "wakeup": "Wake-up light"},
    "airheater": {
        "error_code": "Error",
        "level": "Heating temperature",
        "mode": "Timer",
        "permanent": "Continuous heating",
        "running_time": "Run time",
    },
    "water": {
        "fresh_percent": "Fresh water",
        "fresh_capacity_l": "Fresh water",
        "waste_percent": "Waste water",
        "waste_capacity_l": "Waste water",
    },
    "energy": {
        "age_min": "Starter data age",
        "energy_mode": "Energy mode",
        "sleep_warning": "Issues",
        "soc1_level": "Starter battery",
        "soc2_level": "Second battery",
        "batt1_v": "Starter power",
        "batt1_current": "Starter power",
        "batt2_v": "Second battery power",
        "batt2_current": "Second battery power",
        "batt2_remaining_h": "Time remaining",
        "dcdc_charging": "Vehicle power",
        "dcdc_current": "Vehicle power",
        "dcdc_power": "Vehicle power",
        "shore_current": "Shore power",
        "shore_power": "Shore power",
        "solar_current": "Solar power",
        "solar_power": "Solar power",
    },
    "general": {
        "amb_sw_version": "Firmware (amb · cm · comm)",
        "cm_sw_version": "Firmware (amb · cm · comm)",
        "comm_version": "Firmware (amb · cm · comm)",
    },
    "roof": {"position": "Position", "safety_valid": "Safety valid"},
    "vehicle": {
        "ignition_on": "Ignition",
        "car_variant": "Model",
        "level_roll": "Level indicator (roll / pitch)",
        "level_pitch": "Level indicator (roll / pitch)",
    },
}


def _r(lo_hi, unit=""):
    return "%d–%d%s" % (lo_hi[0], lo_hi[1], unit)


def _keys(d):
    return " / ".join(d)


# function -> {what: (label, values, note, sample value)}. Labels are the web UI's (German looked up
# in strings.de.js); ranges and enums come from calictl.control. tests/test_features_page.py checks
# every row builds a frame and every command in the control vectors has a row.
COMMANDS = {
    "cooler": {
        "power": ("Refrigerator box", _ONOFF, "", "on"),
        "level": ("Cooling level", _r(control.COOLER_LEVEL_RANGE), "", 3),
        "mode": (
            "Quiet mode",
            _keys(control.COOLER_MODES),
            "off / manual / automatic (by the hours below); only while the box is on",
            "quiet",
        ),
        "night_on": (
            "Quiet mode starts at",
            "hour 0–23",
            "only while the box is on and its state is known",
            22,
        ),
        "night_off": (
            "Quiet mode ends at",
            "hour 0–23",
            "only while the box is on and its state is known",
            6,
        ),
        "timer_set": ("Cooling starts at", "HH:MM", "only while the box is off", "07:30"),
        "timer_start": ("Start timer", "—", "only while the box is off", None),
        "timer_cancel": ("Cancel timer", "—", "", None),
    },
    "campingmode": {
        "master": ("Camping mode", _ONOFF, "the web UI greys it while the ignition is on", "on"),
        "lights": ("Exterior and interior lighting", _ONOFF, "only while camping mode is on", "on"),
        "usb": ("Rear USB ports", _ONOFF, "only while camping mode is on", "on"),
    },
    "lighting": {
        "power": ("All lights", _ONOFF, "restores the lamps' saved levels", "on"),
        **{
            k: (
                "%s · %s" % _ZONE_LABELS[k],
                _LVL,
                "only while the pop-up roof is open" if k == "roof-reading" else "",
                5,
            )
            for k in control.LIGHT_ZONES
        },
        "all": ("Every lamp", _LVL, "sets every fitted lamp to one level", 5),
        "brightness": ("Every lamp", _LVL, "the same as `all`; the Home Assistant slider uses it", 5),
        "profile": (
            "Favourite",
            "0–13 except 8",
            "1–7 = a stored favourite, %d = all off, %d = all on; a favourite the unit reports empty is refused"
            % (control.LIGHT_PROFILE_ALL_OFF, control.LIGHT_PROFILE_ALL_ON),
            1,
        ),
        "save_profile": (
            "Save favourite",
            "N [colour]",
            "N = 1–7; colour = one of %s" % ", ".join(control.LIGHT_COLORS),
            "1",
        ),
        "door_contact": (
            "Sliding door light",
            _ONOFF,
            "the rear interior lights come on when the sliding door opens",
            "on",
        ),
        "wakeup": (
            "Wake-up light",
            "HH:MM [areas] [brightness] [ramp] [on/off]",
            "areas 1–4 (comma list), brightness 0–10, ramp %s min"
            % "/".join(map(str, control.WAKEUP_RAMPS_MIN)),
            "07:00 on",
        ),
    },
    "airheater": {
        "power": (
            "Immediate heating",
            _ONOFF,
            "on is refused while the unit reports the heater not possible (engine running or water heater on)",
            "on",
        ),
        "permanent": ("Continuous heating", "off", "can only be started from inside the vehicle", "off"),
        "level": ("Heating temperature", _r(control.AIRHEATER_LEVEL_RANGE), "10 = HI", 5),
        "runtime": (
            "Run time",
            "0–%d min" % control.AIRHEATER_MAX_RUNTIME_MIN,
            "not during continuous heating",
            60,
        ),
        "timer": ("Start heating at", "HH:MM", "not while the timer is on", "06:45"),
        "timer_start": ("Start timer", "—", "the heater then starts at the set time for the run time", None),
        "timer_cancel": ("Stop timer", "—", "", None),
    },
    "energy": {
        "mode": (
            "Energy mode",
            _keys(control.ENERGY_MODES),
            "refused while the unit locks the energy mode",
            "normal",
        ),
    },
    "roof": {
        "open": ("open", "—", "press and hold; release stops", None),
        "close": ("close", "—", "press and hold; release stops", None),
        "stop": ("stop", "—", "calictl never refuses it", None),
    },
    "stairs": {
        "move": ("Move", _keys(control.STAIRS_MOVEMENT), "", "extend"),
        "mode": ("Mode", "0–3", "", 1),
    },
    "roofaircondition": {
        "power": ("Power", _ONOFF, "", "on"),
        "mode": ("Mode", "0–3", "automatic / cooling / heating / venting", 1),
        "fanspeed": ("Fan speed", "0–4", "4 = automatic", 2),
        "temperature": ("Temperature", "0–255", "the unit's raw value; its scale is unknown", 20),
    },
    "livingroomheater": {
        "air": ("Air heating", _ONOFF, "", "on"),
        "water": ("Water heating", _ONOFF, "", "on"),
        "temperature": ("Temperature", "0–255", "the unit's raw value; its scale is unknown", 20),
        "temperatureair": ("Temperature", "0–255", "the same as `temperature`", 20),
    },
}

INTRO = """\
# What calictl does today

This page lists, for each function of the VW California camper unit, what calictl shows, what it
can control, where, and how well that is proven. It is generated from the code (the signal catalog,
the command builders, the Home Assistant entities and the satellite's write list), so it changes
when the code changes.

**Where** has three columns:

- **buspi** — the calictl daemon on the Raspberry Pi: its web UI, the `calictl set` command line
  and the HTTP API. The web UI shows only the functions fitted to the van.
- **Home Assistant** — the entity calictl publishes over MQTT, with its name.
- **Satellite** — the ESP32 satellite's web UI (the same web UI, served by the satellite). It writes
  to five functions only; for anything else it answers "{elsewhere}".

Both web UIs show the same values. A value without a unit is a level, a mode or an on/off flag by
nature, not a measurement. Labels are the web UI's, with the German term in parentheses. The
**Value** column gives the value's name in the daemon's state (`/api/state`); a value without a web
UI label is in that state, but the web UI does not show it in a row of its own.

**Proof** says how far a function is checked, in one of four steps:

- *{live}*
- *{read_live}*
- *{app_only}*
- *{not_fitted}*

The evidence for each claim is in the [evidence ledger]({ledger}).
"""


def _german() -> dict:
    """``{English: German}`` from the web UI's ``strings.de.js``."""
    return dict(
        re.findall(r'^\s*"((?:[^"\\]|\\.)*)":\s*"((?:[^"\\]|\\.)*)",?\s*$', STRINGS_DE.read_text(), re.M)
    )


def _label(text, de) -> str:
    def cap(t):
        return t[:1].upper() + t[1:]

    parts = [text] if text in de else text.split(" · ")
    return " · ".join("%s (%s)" % (cap(p), cap(de[p])) if de.get(p, p) != p else cap(p) for p in parts)


def _shown(fn, state, de) -> list:
    """``[(web UI label, value, unit)]`` calictl shows, in catalog order; ``x_1`` … ``x_16`` collapse
    into ``x_N``. The label is ``—`` for a value the web UI does not show under a label."""
    labels = UI_LABELS.get(fn, {})
    rows = {}
    for e in state.values():
        if e.get("decision") != "surface":
            continue
        m = re.fullmatch(r"(.*_)(\d+)", e["name"])
        key = m.group(1) + "N" if m else e["name"]
        rows.setdefault(key, [e.get("unit") or "—", []])[1].extend([int(m.group(2))] if m else [])
    return [
        (
            _label(labels[k], de) if k in labels else "—",
            "`%s`" % k + (" (N = %d–%d)" % (min(n), max(n)) if n else ""),
            unit,
        )
        for k, (unit, n) in rows.items()
    ]


def _ha() -> dict:
    return {(fn, s.what): s.name for fn, specs in mqtt.COMMAND_SPECS.items() for s in specs}


def _satellite(fn, what) -> str:
    if fn not in ESP_CONTROL_FUNCTIONS or (fn, what) in SATELLITE_LACKS:
        return "—"
    return "yes, with the web page's clock" if (fn, what) == ("lighting", "wakeup") else "yes"


# Commands of a satellite function that the satellite's C twin does not carry (an alias only Home
# Assistant on buspi uses). tests/test_features_page.py checks this against control.c.
SATELLITE_LACKS = {("lighting", "brightness")}


def build(signals_path=None) -> str:
    """Render docs/features.md. Returns the file text.

    :param signals_path: ``protocol/signals.yaml`` path override (tests).
    :returns: the Markdown page.
    :raises ValueError: a catalog function without an entry in :data:`FUNCTIONS`, or the reverse.
    """
    catalog = load_catalog(signals_path)
    if set(catalog) != set(FUNCTIONS):
        raise ValueError("FUNCTIONS and signals.yaml disagree: %s" % sorted(set(catalog) ^ set(FUNCTIONS)))
    de, ha = _german(), _ha()
    lines = [
        INTRO.format(
            elsewhere=ESP_ELSEWHERE_REASON,
            live=LIVE,
            read_live=READ_LIVE,
            app_only=APP_ONLY,
            not_fitted=NOT_FITTED,
            ledger=LEDGER,
        )
    ]
    for fn, (title, tier, note) in FUNCTIONS.items():
        lines += [
            "## %s" % _label(title, de),
            "",
            "**Proof:** %s ([evidence](%s)). %s" % (tier, LEDGER, note),
            "",
        ]
        shown = _shown(fn, (catalog[fn] or {}).get("state") or {}, de)
        if shown:
            lines += ["**You can see:**", "", "| Web UI label | Value | Unit |", "|---|---|---|"]
            lines += ["| %s | %s | %s |" % row for row in shown]
            lines.append("")
        cmds = COMMANDS.get(fn) or {}
        if cmds:
            lines += [
                "**You can control** (`calictl set %s <command>%s`):"
                % (fn, "" if all(c[3] is None for c in cmds.values()) else " <value>"),
                "",
                "| Command | Label | Values | buspi | Home Assistant | Satellite | Note |",
                "|---|---|---|---|---|---|---|",
            ]
            for what, (label, values, cnote, _) in cmds.items():
                lines.append(
                    "| `%s` | %s | %s | yes | %s | %s | %s |"
                    % (
                        what,
                        _label(label, de),
                        values,
                        ha.get((fn, what), "—"),
                        _satellite(fn, what),
                        cnote or "—",
                    )
                )
            lines.append("")
            if fn in FOOTNOTES:
                lines += [FOOTNOTES[fn], ""]
        else:
            lines += ["Nothing to control.", ""]
    return "\n".join(lines).rstrip("\n") + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description="generate docs/features.md (what calictl does today)")
    ap.add_argument("--out", default=str(OUT_FILE))
    args = ap.parse_args(argv)
    out = Path(args.out)
    out.write_text(build())
    print("wrote %s" % out)


if __name__ == "__main__":
    main()
