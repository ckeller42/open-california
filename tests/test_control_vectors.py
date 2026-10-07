"""The ESP control vectors (tests/vectors/control.json) are calictl.control's own answers.

``tools/gen_control_vectors.py`` asks ``control.command_precondition`` / ``build`` / ``preface_for``
/ ``commit_for`` over a value grid x state variants and over every app-recorded action, in
``device.actuate``'s write order. The C twin (``firmware/components/cali_core/control.c``) must
reproduce every vector (``tests/firmware/test_control_parity.py``).

.. test:: The control vectors are fresh, cover every recorded app action and match the app
   :id: T_CONTROL_VECTORS
   :links: R_FW_CONTROL_TWIN, R_APP_FIDELITY
"""

import json

import pytest

from calictl import control
from tools import capture_diff, gen_c_dict, gen_control_vectors

V = json.loads(gen_control_vectors.OUT.read_text(encoding="utf-8"))


def test_checked_in_vectors_are_fresh():
    assert gen_control_vectors.OUT.read_text(encoding="utf-8") == gen_control_vectors.render()


def test_control_header_is_fresh_and_never_lists_the_roof():
    assert gen_c_dict.CONTROL_OUT.read_text(encoding="utf-8") == gen_c_dict.generate_control()
    text = gen_c_dict.CONTROL_OUT.read_text(encoding="utf-8")
    assert "0x1401" not in text and "roof" not in gen_c_dict.ESP_CONTROL_FUNCTIONS
    for fn in gen_c_dict.ESP_CONTROL_FUNCTIONS:
        assert '{"%s", 0x' % fn in text


def test_every_recorded_esp_action_is_a_vector():
    want = []
    for path in sorted(gen_control_vectors.APP_DIR.glob("*.jsonl")):
        for w in capture_diff.check_recording(path):
            if w.kind == "action" and w.fn in gen_c_dict.ESP_CONTROL_FUNCTIONS:
                want.append("%s:%d" % (path.name, w.line))
    assert [a["id"] for a in V["app"]] == want and want


def test_recorded_actions_are_never_refused_or_bad():
    bad = [(a["id"], a["expect"]) for a in V["app"] if a["expect"]["kind"] not in ("frames", "elsewhere")]
    assert not bad


def test_recorded_frames_are_the_apps_byte_for_byte():
    """Ruling R1: the action's own frame (the last undelayed write) IS the app's recorded frame —
    the whole frame, not only its targeted fields (the ESP carries no roof, whose counter differs)."""
    for a in V["app"]:
        if a["expect"]["kind"] != "frames":
            continue
        main = [f for f in a["expect"]["frames"] if f["delay_ms"] == 0][-1]
        assert main["hex"] == a["app_hex"], a["id"]


@pytest.mark.parametrize("what", ["open", "close", "stop"])
def test_roof_is_elsewhere(what):
    c = next(c for c in V["cases"] if c["function"] == "roof" and c["what"] == what)
    assert c["expect"] == {"kind": "elsewhere", "reason": gen_c_dict.ESP_ELSEWHERE_REASON}


def test_wakeup_is_elsewhere():
    assert {c["expect"]["kind"] for c in V["cases"] if c["what"] == "wakeup"} == {"elsewhere"}


def test_reason_constants_are_the_gate_texts():
    assert control.command_precondition("cooler", "timer_set", "07:00", {"cooler": {"State": 1}}) == (
        control.REASON_COOLER_TIMER_NEEDS_FRIDGE_OFF
    )
    assert control.command_precondition("cooler", "mode", "quiet", {"cooler": {"State": 0}}) == (
        control.REASON_QUIET_NEEDS_FRIDGE_ON
    )
    assert control.command_precondition("campingmode", "usb", "on", {"campingmode": {"State": 0}}) == (
        control.REASON_CAMPING_NEEDS_MASTER
    )
    assert control.command_precondition(
        "energy", "mode", "eco", {"energy": {"EnergyModeNotSelectable": 1}}
    ) == (control.REASON_ENERGY_LOCKED)
    assert control.command_precondition("lighting", "roof-reading", 5, {"roof": {"Position": 0}}) == (
        control.REASON_ROOF_READING
    )
    fav = {"lighting": {"Mode": 12, "ProfileNumber": 0, "LightValue": 0}}
    assert control.command_precondition("lighting", "profile", 3, fav) == control.REASON_FAVOURITE_EMPTY


def test_every_gate_is_exercised_both_ways():
    """Each REASON_* the ESP can hit appears as a refusal AND its command also appears allowed."""
    refused = {}
    allowed = set()
    for c in V["cases"]:
        if c["expect"]["kind"] == "refused":
            refused.setdefault(c["expect"]["reason"], set()).add((c["function"], c["what"]))
        elif c["expect"]["kind"] == "frames":
            allowed.add((c["function"], c["what"]))
    for name in (
        "REASON_ROOF_READING",
        "REASON_COOLER_TIMER_NEEDS_FRIDGE_OFF",
        "REASON_QUIET_NEEDS_FRIDGE_ON",
        "REASON_CAMPING_NEEDS_MASTER",
        "REASON_ENERGY_LOCKED",
        "REASON_FAVOURITE_EMPTY",
        "REASON_NOT_ONOFF",
        "REASON_COOLER_STATE_UNKNOWN",
    ):
        text = getattr(control, name)
        assert text in refused, name
        assert refused[text] & allowed, name  # the same command also passes the gate somewhere


def test_generator_shape_is_device_actuates_write_order():
    """A mutation in ``expect()`` (dropped commit, swapped preface) would regenerate self-consistent
    vectors; pin the shape: lighting = [frame@0, commit@FOLLOW], a colour save = preface first."""
    assert V["follow_delay_ms"] == 300
    zone = next(c for c in V["cases"] if c["id"] == "lighting/kitchen/5@seed")["expect"]
    assert [(f["delay_ms"], f["hex"][:4]) for f in zone["frames"]] == [(0, "0904"), (300, "0e00")]
    assert zone["frames"][1]["hex"] == control.LIGHT_COMMIT.hex()
    save = next(c for c in V["cases"] if c["id"] == 'lighting/save_profile/"3 amber"@seed')["expect"]
    assert [f["delay_ms"] for f in save["frames"]] == [0, 300, 0, 300]
    assert save["frames"][0]["hex"][2:4] == "06"  # Mode 6 = SET_COLOR preface first
    assert save["frames"][2]["hex"][2:4] == "04"  # then the SET_BRIGHTNESS save
    cooler = next(c for c in V["cases"] if c["id"] == 'cooler/power/"on"@seed')["expect"]
    assert cooler == {"kind": "frames", "frames": [{"char": "1101", "delay_ms": 0, "hex": "fd771e3e1f1f"}]}


def test_garbage_and_unknown_state_are_refused_in_the_vectors():
    """R3/R4: the C twin's spec never says 'build the OFF frame' for a garbage value, nor 'send the
    default-filled night frame' with no cooler state."""
    by_id = {c["id"]: c["expect"] for c in V["cases"]}
    for cid in ("cooler/power/null@seed", 'cooler/power/"x"@seed', 'campingmode/master/"maybe"@camping_on'):
        assert by_id[cid] == {"kind": "refused", "reason": control.REASON_NOT_ONOFF}, cid
    for cid in ("cooler/night_on/0@empty", "cooler/night_off/7@empty"):
        assert by_id[cid] == {"kind": "refused", "reason": control.REASON_COOLER_STATE_UNKNOWN}, cid
    assert by_id["cooler/night_on/0@cooler_schedule"]["kind"] == "frames"


def test_c_string_literals_escape_quotes_backslashes_and_trigraphs():
    assert gen_c_dict._c_str('a"b\\c??d — e') == '"a\\"b\\\\c\\?\\?d \\342\\200\\224 e"'
