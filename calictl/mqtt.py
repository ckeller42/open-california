"""Home Assistant MQTT-discovery bridge.

Publishes the Camper Unit as native HA entities, one HA device per vehicle
function. For each poll we publish one JSON state message per function to
`calivan/<fn>`; discovery configs point each entity at a field of that JSON
via a value_template.

`paho.mqtt` is imported lazily; `render_discovery`/`flatten`/`render_state` are
pure and unit-tested without a broker.
"""
from __future__ import annotations

import json
import os
from collections import namedtuple

DISCOVERY_PREFIX = "homeassistant"
BASE = "calivan"

# Freshness: HA marks a sensor `unavailable` if no state message arrives within `expire_after`
# seconds. The daemon publishes each function every successful poll (~30 s) and publishes NOTHING
# when the unit is unreachable (parked deep-sleep), so this makes the battery/telemetry sensors go
# unavailable when the data is stale — instead of showing a days-old value as live. That's the gate
# an automation needs ("act on the leisure battery only while its sensor is available"). Generous
# vs the poll cadence; tune with CALICTL_MQTT_EXPIRE_AFTER_S. The daemon's own up/down is a separate
# signal (the `calivan/status` LWT availability_topic).
EXPIRE_AFTER_S = int(os.environ.get("CALICTL_MQTT_EXPIRE_AFTER_S", "300"))

EntitySpec = namedtuple("EntitySpec", "component key name config")

DEVICE_BASE = {"manufacturer": "Volkswagen", "model": "California T7"}
FUNCTION_NAMES = {
    "water": "Water", "energy": "Energy", "cooler": "Fridge", "campingmode": "Camping Mode",
    "airheater": "Air Heater", "roof": "Roof", "lighting": "Lighting",
    "livingroomheater": "Living-Room Heater", "roofaircondition": "Roof A/C",
    "stairs": "Stairs", "satelliteantenna": "Satellite",
}


def device_block(function: str) -> dict:
    return {"identifiers": ["vwcamper_%s" % function],
            "name": "VW California — %s" % FUNCTION_NAMES.get(function, function),
            "via_device": "vwcamper", **DEVICE_BASE}


_BIN = {"payload_on": "True", "payload_off": "False"}

# Read-only entities per function (control entities added in Tasks 6-8).
ENTITY_SPECS: dict[str, list] = {
    "water": [
        EntitySpec("sensor", "fresh_percent", "Fresh Water", {"unit_of_measurement": "%", "icon": "mdi:water"}),
        EntitySpec("sensor", "waste_percent", "Waste Water", {"unit_of_measurement": "%", "icon": "mdi:water-off"}),
    ],
    "energy": [
        EntitySpec("sensor", "batt2_v", "Leisure Battery", {"unit_of_measurement": "V", "device_class": "voltage"}),
        EntitySpec("sensor", "soc2_level", "Leisure Battery Level", {"icon": "mdi:battery"}),
        EntitySpec("sensor", "batt2_current", "Leisure Battery Current", {"icon": "mdi:current-dc"}),
        EntitySpec("sensor", "batt1_v", "Starter Battery", {"unit_of_measurement": "V", "device_class": "voltage"}),
        EntitySpec("binary_sensor", "dcdc_charging", "DC-DC Charging", {**_BIN, "device_class": "battery_charging"}),
        EntitySpec("sensor", "energy_mode", "Energy Mode", {"icon": "mdi:lightning-bolt"}),
        # Freshness: the unit's OWN battery-values age (minutes; 255 => stale/subsystem asleep) +
        # a derived stale flag, so a frozen starter reading is visible rather than trusted blindly.
        EntitySpec("sensor", "age_min", "Battery Values Age",
                   {"unit_of_measurement": "min", "icon": "mdi:clock-alert-outline",
                    "entity_category": "diagnostic"}),
        EntitySpec("binary_sensor", "stale", "Starter Battery Data Stale",
                   {**_BIN, "device_class": "problem", "entity_category": "diagnostic"}),
    ],
    "cooler": [
        EntitySpec("binary_sensor", "on", "Fridge On", {**_BIN, "icon": "mdi:fridge"}),
        EntitySpec("sensor", "level", "Fridge Level", {"icon": "mdi:snowflake"}),
    ],
    "campingmode": [
        # Read-only power indicator: the DERIVED usb_powered (master AND UsbCharger), not the raw
        # UsbCharger field, which reads "on" even while master gates the port physically off. The
        # controllable "Rear USB Ports Switch" below still reflects the raw toggle setting you set.
        EntitySpec("binary_sensor", "usb_powered", "Rear USB Ports", {**_BIN, "icon": "mdi:usb"}),
        EntitySpec("binary_sensor", "lights_on", "Camping Lights", {**_BIN, "icon": "mdi:lightbulb"}),
    ],
    "airheater": [
        EntitySpec("binary_sensor", "running", "Air Heater Running", {**_BIN, "device_class": "running"}),
        EntitySpec("sensor", "level", "Air Heater Level", {"icon": "mdi:radiator"}),
        EntitySpec("sensor", "running_time", "Air Heater Runtime", {"unit_of_measurement": "min", "icon": "mdi:timer"}),
    ],
    "lighting": [
        EntitySpec("binary_sensor", "any_on", "Interior Lights On", {**_BIN, "icon": "mdi:led-strip-variant"}),
    ],
    "roof": [
        EntitySpec("sensor", "position", "Roof Position", {"icon": "mdi:caravan"}),
    ],
    # Installed-gated: only appear on vans that have these features.
    "livingroomheater": [
        EntitySpec("binary_sensor", "air_on", "LR Heater Air", {**_BIN, "icon": "mdi:heat-wave"}),
        EntitySpec("binary_sensor", "water_on", "LR Heater Water", {**_BIN, "icon": "mdi:water-boiler"}),
        # air_temp scale is UNVERIFIED (8-bit raw) -> no unit/device_class (CLAUDE.md rule);
        # water_temp is a 1-bit flag, not a temperature.
        EntitySpec("sensor", "air_temp", "LR Heater Air Temp (raw)", {"icon": "mdi:thermometer"}),
        EntitySpec("binary_sensor", "water_temp_flag", "LR Heater Water Temp Flag", {**_BIN}),
    ],
    "roofaircondition": [
        EntitySpec("binary_sensor", "on", "Roof A/C On", {**_BIN, "icon": "mdi:air-conditioner"}),
        EntitySpec("sensor", "target_temp", "Roof A/C Target Temp (raw)", {"icon": "mdi:thermometer"}),
        EntitySpec("sensor", "fan_speed", "Roof A/C Fan", {"icon": "mdi:fan"}),
    ],
    "satelliteantenna": [
        EntitySpec("sensor", "signal_level", "Satellite Signal", {"icon": "mdi:satellite-uplink"}),
        EntitySpec("binary_sensor", "system_on", "Satellite On", {**_BIN, "icon": "mdi:satellite-variant"}),
    ],
}


# --- Controllable entities (HA -> BLE command path) ------------------------
# One CommandSpec per (function, what) that ``control.BUILDERS`` can actuate.
# ``component`` maps on/off -> HA ``switch`` and level/brightness -> HA ``number``.
# ``what`` is the control target passed to ``serve.on_command`` / ``control.build``.
# ``config`` carries component extras (a number's min/max). ``state_key`` names the
# flattened state field (from ``semantics``) the entity reflects, or ``None`` if the
# control target has no direct read-back field.
CommandSpec = namedtuple("CommandSpec", "component what name config state_key")

COMMAND_SPECS: dict[str, list] = {
    "cooler": [
        CommandSpec("switch", "power", "Fridge Power", {}, "on"),
        CommandSpec("number", "level", "Fridge Set Level", {"min": 1, "max": 5}, "level"),
    ],
    "campingmode": [
        CommandSpec("switch", "master", "Camping Mode", {}, "master_on"),
        CommandSpec("switch", "lights", "Camping Lights Switch", {}, "lights_on"),
        CommandSpec("switch", "usb", "Rear USB Ports Switch", {}, "usb_charger"),
    ],
    "lighting": [
        CommandSpec("switch", "power", "Interior Lights", {}, "any_on"),
        CommandSpec("number", "brightness", "Interior Brightness", {"min": 0, "max": 15}, None),
    ],
    "airheater": [
        CommandSpec("switch", "power", "Air Heater Power", {}, "running"),
        CommandSpec("number", "level", "Air Heater Set Level", {"min": 0, "max": 15}, "level"),
    ],
}


def command_topic(function: str, what: str) -> str:
    """The MQTT set-topic for one actuatable target: ``calivan/<fn>/<what>/set``."""
    return "%s/%s/%s/set" % (BASE, function, what)


def command_topics() -> dict:
    """Map every actuatable command topic to its ``(function, what)`` pair.

    :returns: ``{topic: (function, what)}`` covering every ``COMMAND_SPECS`` entry.
        ``serve.run`` subscribes to these keys on connect and routes an inbound
        message through the value into ``serve.on_command`` / ``control.build``.

    .. req:: Expose HA command topics for every actuatable target
       :id: R_MQTT_COMMAND_TOPICS
       :status: implemented
       :tags: mqtt, control, ha

       ``mqtt.command_topics`` shall return a ``{topic: (function, what)}`` map
       covering every ``control.BUILDERS`` target (cooler power/level, campingmode
       master/lights/usb, lighting power/brightness, airheater power/level) using a
       stable ``calivan/<function>/<what>/set`` topic scheme.
    """
    out = {}
    for function, specs in COMMAND_SPECS.items():
        for spec in specs:
            out[command_topic(function, spec.what)] = (function, spec.what)
    return out


def flatten(interp: dict, prefix: str = "") -> dict:
    """Flatten nested interpretation (e.g. water fresh.percent) to fresh_percent."""
    out = {}
    for k, v in interp.items():
        if isinstance(v, dict):
            out.update(flatten(v, "%s%s_" % (prefix, k)))
        else:
            out["%s%s" % (prefix, k)] = v
    return out


def _config(function, spec):
    uid = "vwcamper_%s_%s" % (function, spec.key)
    cfg = {"name": spec.name, "unique_id": uid,
           "state_topic": "%s/%s" % (BASE, function),
           "value_template": "{{ value_json.%s }}" % spec.key,
           "availability_topic": "%s/status" % BASE,
           "expire_after": EXPIRE_AFTER_S,   # go `unavailable` when the reading stops refreshing
           "device": device_block(function)}
    cfg.update(spec.config)   # a spec may override (e.g. its own expire_after)
    return "%s/%s/%s/config" % (DISCOVERY_PREFIX, spec.component, uid), cfg


def _command_config(function, spec):
    """Discovery config for one controllable entity (a switch or number with a
    ``command_topic``). Switches carry on/off command payloads (``control._truthy``
    accepts ``"on"``); numbers carry min/max from ``spec.config``. When the target
    has a read-back field (``spec.state_key``) the entity also reflects live state."""
    uid = "vwcamper_%s_%s_set" % (function, spec.what)
    cfg = {"name": spec.name, "unique_id": uid,
           "command_topic": command_topic(function, spec.what),
           "availability_topic": "%s/status" % BASE,
           "device": device_block(function)}
    if spec.state_key is not None:
        cfg["state_topic"] = "%s/%s" % (BASE, function)
        cfg["value_template"] = "{{ value_json.%s }}" % spec.state_key
    if spec.component == "switch":
        cfg["payload_on"] = "on"
        cfg["payload_off"] = "off"
        if spec.state_key is not None:      # semantics emits bools -> "True"/"False"
            cfg["state_on"] = "True"
            cfg["state_off"] = "False"
    cfg.update(spec.config)                 # number min/max
    return "%s/%s/%s/config" % (DISCOVERY_PREFIX, spec.component, uid), cfg


def render_discovery(installed=None) -> dict:
    out = {}
    for function, specs in ENTITY_SPECS.items():
        if installed is not None and function not in installed:
            continue
        for spec in specs:
            topic, cfg = _config(function, spec)
            out[topic] = cfg
    # controllable entities (only for installed functions, same gating)
    for function, specs in COMMAND_SPECS.items():
        if installed is not None and function not in installed:
            continue
        for spec in specs:
            topic, cfg = _command_config(function, spec)
            out[topic] = cfg
    return out


def render_state(function: str, interp: dict) -> tuple[str, str]:
    """(state_topic, json_payload) for one function's interpreted status."""
    return "%s/%s" % (BASE, function), json.dumps(flatten(interp), default=str)


def availability_topic() -> str:
    return "%s/status" % BASE
