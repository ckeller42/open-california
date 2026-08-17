"""Home Assistant command-path tests (pure; no MQTT/BLE broker needed).

Covers the HA -> BLE control wiring: the command-topic map, the controllable
discovery entities (switch/number with a ``command_topic``), and the round-trip
from an inbound topic back to a ``(function, what)`` that ``control.build`` accepts.

.. test:: HA command topics + controllable discovery
   :id: T_MQTT_COMMAND_TOPICS
   :links: R_MQTT_COMMAND_TOPICS

   Verifies ``mqtt.command_topics`` covers every actuatable ``(function, what)``
   with a valid ``calivan/<function>/<what>/set`` topic, that discovery emits a
   command entity carrying that ``command_topic`` for controllable functions, and
   that each topic routes back to the expected pair.
"""
from calictl import mqtt


# Every actuatable (function, what) that control.BUILDERS supports.
EXPECTED = {
    ("cooler", "power"), ("cooler", "level"),
    ("campingmode", "master"), ("campingmode", "lights"), ("campingmode", "usb"),
    ("lighting", "power"), ("lighting", "brightness"),
    ("airheater", "power"), ("airheater", "level"),
}


def test_command_topics_cover_expected_set():
    ct = mqtt.command_topics()
    # exactly the actuatable pairs, no more/less
    assert set(ct.values()) == EXPECTED
    # every topic is the stable calivan/<fn>/<what>/set scheme and unique
    assert len(ct) == len(EXPECTED)
    for topic, (fn, what) in ct.items():
        assert topic == "calivan/%s/%s/set" % (fn, what)
        assert topic.startswith(mqtt.BASE + "/") and topic.endswith("/set")


def test_command_topic_round_trips_to_function_and_what():
    ct = mqtt.command_topics()
    # an inbound message on this topic must resolve to (cooler, level), like serve's
    # on_message does via cmd_map.get(msg.topic)
    assert ct[mqtt.command_topic("cooler", "level")] == ("cooler", "level")
    assert ct[mqtt.command_topic("campingmode", "lights")] == ("campingmode", "lights")
    # and the derived topic is exactly what command_topics() keyed on
    for fn, what in EXPECTED:
        assert ct.get(mqtt.command_topic(fn, what)) == (fn, what)


def test_discovery_emits_command_entity_with_command_topic():
    installed = {"cooler", "campingmode", "lighting", "airheater"}
    cfgs = mqtt.render_discovery(installed=installed)
    # the cooler power switch: a switch component carrying the matching command_topic
    cmd_entities = {t: c for t, c in cfgs.items() if "command_topic" in c}
    assert cmd_entities, "no controllable entity emitted"
    cooler_power = [c for c in cmd_entities.values()
                    if c["command_topic"] == mqtt.command_topic("cooler", "power")]
    assert len(cooler_power) == 1
    c = cooler_power[0]
    assert "/switch/" in next(t for t, v in cfgs.items() if v is c)
    assert c["payload_on"] == "on" and c["payload_off"] == "off"
    # every actuatable (function, what) has exactly one discovery command entity
    topics = {c["command_topic"] for c in cmd_entities.values()}
    assert topics == {mqtt.command_topic(fn, w) for fn, w in EXPECTED}


def test_number_entity_carries_min_max():
    cfgs = mqtt.render_discovery(installed={"cooler"})
    level = [c for t, c in cfgs.items()
             if c.get("command_topic") == mqtt.command_topic("cooler", "level")]
    assert len(level) == 1
    c = level[0]
    # a number component with the cooler level range 1..5
    number_topic = next(t for t, v in cfgs.items() if v is c)
    assert "/number/" in number_topic
    assert c["min"] == 1 and c["max"] == 5


def test_read_only_sensors_have_no_command_topic():
    # existing read-only entities are unchanged: no command_topic leaks onto them
    cfgs = mqtt.render_discovery(installed={"water", "energy", "roof"})
    for t, c in cfgs.items():
        if "/sensor/" in t or "/binary_sensor/" in t:
            assert "command_topic" not in c


def test_read_only_sensors_carry_expire_after_for_freshness():
    # freshness: read-only sensors expire (-> HA `unavailable`) when the reading stops refreshing,
    # so an automation can gate on availability instead of triggering on a stale (parked) value.
    cfgs = mqtt.render_discovery(installed={"energy"})
    battery = [c for t, c in cfgs.items() if "/sensor/" in t and c.get("unique_id", "").endswith("soc2_level")]
    assert battery and battery[0]["expire_after"] == mqtt.EXPIRE_AFTER_S
    # command entities must NOT carry expire_after (they're command-driven, not polled)
    for t, c in mqtt.render_discovery().items():
        if "command_topic" in c:
            assert "expire_after" not in c


def test_command_entities_gated_by_installed():
    # a not-installed controllable function publishes no command entity
    cfgs = mqtt.render_discovery(installed={"cooler"})
    assert not any(c.get("command_topic") == mqtt.command_topic("airheater", "power")
                   for c in cfgs.values())
    # unfiltered discovery still exposes all of them
    all_cfgs = mqtt.render_discovery()
    topics = {c["command_topic"] for c in all_cfgs.values() if "command_topic" in c}
    assert topics == {mqtt.command_topic(fn, w) for fn, w in EXPECTED}
