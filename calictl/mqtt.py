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
from collections import namedtuple

DISCOVERY_PREFIX = "homeassistant"
BASE = "calivan"

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
    ],
    "cooler": [
        EntitySpec("binary_sensor", "on", "Fridge On", {**_BIN, "icon": "mdi:fridge"}),
        EntitySpec("sensor", "level", "Fridge Level", {"icon": "mdi:snowflake"}),
    ],
    "campingmode": [
        EntitySpec("binary_sensor", "usb_charger", "USB Charger", {**_BIN, "icon": "mdi:usb"}),
        EntitySpec("binary_sensor", "interior_light", "Interior Light (state)", {**_BIN, "icon": "mdi:lightbulb"}),
        EntitySpec("binary_sensor", "outside_light", "Outside Light (state)", {**_BIN, "icon": "mdi:outdoor-lamp"}),
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
        EntitySpec("sensor", "air_temp", "LR Heater Air Temp", {"unit_of_measurement": "°C", "device_class": "temperature"}),
        EntitySpec("sensor", "water_temp", "LR Heater Water Temp", {"unit_of_measurement": "°C", "device_class": "temperature"}),
    ],
    "roofaircondition": [
        EntitySpec("binary_sensor", "on", "Roof A/C On", {**_BIN, "icon": "mdi:air-conditioner"}),
        EntitySpec("sensor", "target_temp", "Roof A/C Target Temp", {"unit_of_measurement": "°C", "device_class": "temperature"}),
        EntitySpec("sensor", "fan_speed", "Roof A/C Fan", {"icon": "mdi:fan"}),
    ],
    "satelliteantenna": [
        EntitySpec("sensor", "signal_level", "Satellite Signal", {"icon": "mdi:satellite-uplink"}),
        EntitySpec("binary_sensor", "system_on", "Satellite On", {**_BIN, "icon": "mdi:satellite-variant"}),
    ],
}


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
           "device": device_block(function)}
    cfg.update(spec.config)
    return "%s/%s/%s/config" % (DISCOVERY_PREFIX, spec.component, uid), cfg


def render_discovery(installed=None) -> dict:
    out = {}
    for function, specs in ENTITY_SPECS.items():
        if installed is not None and function not in installed:
            continue
        for spec in specs:
            topic, cfg = _config(function, spec)
            out[topic] = cfg
    return out


def render_state(function: str, interp: dict) -> tuple[str, str]:
    """(state_topic, json_payload) for one function's interpreted status."""
    return "%s/%s" % (BASE, function), json.dumps(flatten(interp), default=str)


def availability_topic() -> str:
    return "%s/status" % BASE
