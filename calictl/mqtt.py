"""Home Assistant MQTT-discovery bridge.

Publishes the Camper Unit as native HA entities. For each poll we publish one
JSON state message per function to `calivan/<fn>`; discovery configs point each
entity at a field of that JSON via a value_template. Control entities (e.g. the
fridge switch) subscribe to `calivan/<fn>/set/<what>`.

`paho.mqtt` is imported lazily; `render_discovery`/`flatten`/`render_state` are
pure and unit-tested without a broker.
"""
from __future__ import annotations

import json

DISCOVERY_PREFIX = "homeassistant"
BASE = "calivan"
DEVICE = {
    "identifiers": ["vwcamper"],
    "name": "VW California Camper Unit",
    "manufacturer": "Volkswagen",
    "model": "California T7",
}

# Curated HA entities. Each references a flattened key of its function's state.
# component, function, key, name, [unit], [device_class], [extra config]
ENTITIES = [
    ("sensor", "water", "fresh_percent", "Fresh Water", {"unit_of_measurement": "%", "icon": "mdi:water"}),
    ("sensor", "water", "waste_percent", "Waste Water", {"unit_of_measurement": "%", "icon": "mdi:water-off"}),
    ("binary_sensor", "cooler", "on", "Fridge", {"payload_on": "True", "payload_off": "False", "icon": "mdi:fridge"}),
    ("sensor", "cooler", "level", "Fridge Level", {"icon": "mdi:snowflake"}),
    ("sensor", "energy", "batt2_v", "Leisure Battery", {"unit_of_measurement": "V", "device_class": "voltage"}),
    ("sensor", "energy", "soc2_level", "Leisure Battery Level", {"icon": "mdi:battery"}),
    ("binary_sensor", "energy", "dcdc_charging", "DC-DC Charging", {"payload_on": "True", "payload_off": "False", "device_class": "battery_charging"}),
    ("binary_sensor", "campingmode", "usb_charger", "USB Charger", {"payload_on": "True", "payload_off": "False", "icon": "mdi:usb"}),
    ("binary_sensor", "airheater", "running", "Air Heater", {"payload_on": "True", "payload_off": "False", "device_class": "running"}),
    ("sensor", "roof", "position", "Roof Position", {"icon": "mdi:caravan"}),
]

# Control entities: HA switch -> command topic.  (component, function, what, name, config)
SWITCHES = [
    ("cooler", "power", "Fridge Power", {"icon": "mdi:fridge", "payload_on": "on", "payload_off": "off"}),
]


def flatten(interp: dict, prefix: str = "") -> dict:
    """Flatten nested interpretation (e.g. water fresh.percent) to fresh_percent."""
    out = {}
    for k, v in interp.items():
        if isinstance(v, dict):
            out.update(flatten(v, "%s%s_" % (prefix, k)))
        else:
            out["%s%s" % (prefix, k)] = v
    return out


def _uid(fn, key):
    return "vwcamper_%s_%s" % (fn, key)


def render_discovery() -> dict[str, dict]:
    """Return {config_topic: payload_dict} for all entities + switches."""
    cfgs = {}
    for component, fn, key, name, extra in ENTITIES:
        uid = _uid(fn, key)
        payload = {
            "name": name,
            "unique_id": uid,
            "state_topic": "%s/%s" % (BASE, fn),
            "value_template": "{{ value_json.%s }}" % key,
            "availability_topic": "%s/status" % BASE,
            "device": DEVICE,
        }
        payload.update(extra)
        cfgs["%s/%s/%s/config" % (DISCOVERY_PREFIX, component, uid)] = payload
    for fn, what, name, extra in SWITCHES:
        uid = _uid(fn, what)
        payload = {
            "name": name,
            "unique_id": uid,
            "state_topic": "%s/%s" % (BASE, fn),
            "value_template": "{{ 'on' if value_json.on else 'off' }}",
            "command_topic": "%s/%s/set/%s" % (BASE, fn, what),
            "availability_topic": "%s/status" % BASE,
            "device": DEVICE,
        }
        payload.update(extra)
        cfgs["%s/switch/%s/config" % (DISCOVERY_PREFIX, uid)] = payload
    return cfgs


def render_state(function: str, interp: dict) -> tuple[str, str]:
    """(state_topic, json_payload) for one function's interpreted status."""
    return "%s/%s" % (BASE, function), json.dumps(flatten(interp), default=str)


def command_topics() -> dict[str, tuple[str, str]]:
    """{command_topic: (function, what)} for subscribed control switches."""
    return {"%s/%s/set/%s" % (BASE, fn, what): (fn, what) for fn, what, _n, _e in SWITCHES}
