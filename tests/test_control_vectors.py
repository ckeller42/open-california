"""The ESP control vectors (tests/vectors/control.json) are calictl.control's own answers.

``tools/gen_control_vectors.py`` asks ``control.command_precondition`` / ``build`` / ``preface_for``
/ ``commit_for`` over a value grid x state variants and over every app-recorded action, in
``device.actuate``'s write order. The C twin (``firmware/components/cali_core/control.c``) must
reproduce every vector (``tests/firmware/test_control_parity.py``).

.. test:: The control vectors are fresh, cover every recorded app action and match the app
   :id: T_CONTROL_VECTORS
   :links: R_FW_CONTROL_TWIN, R_APP_FIDELITY
"""

import calendar
import datetime
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
    assert [a["id"] for a in V["app"] if "+" not in a["id"]] == want and want


def test_recorded_actions_are_never_refused_or_bad():
    """Every recorded action builds the app's frame — except a time-only wake-up edit made while no
    wake-up config was known: the app sent its defaults, calictl refuses it (ruling R5). Its explicit
    form (the app's own switch state appended) is the next vector and must build the app's frame."""
    for i, a in enumerate(V["app"]):
        if a["expect"]["kind"] == "refused":
            assert a["expect"]["reason"] == control.WAKEUP_UNKNOWN, a["id"]
            sib = V["app"][i + 1]
            assert sib["id"] in (a["id"] + "+on", a["id"] + "+off"), sib["id"]
            assert sib["expect"]["kind"] == "frames" and sib["app_hex"] == a["app_hex"]
        else:
            assert a["expect"]["kind"] == "frames", (a["id"], a["expect"])
    assert {a["what"] for a in V["app"] if a["function"] == "lighting"} >= {"wakeup"}


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


def test_wakeup_needs_the_pages_clock_on_the_esp():
    wk = [c for c in V["cases"] if c["what"] == "wakeup"]
    none = [c["expect"] for c in wk if c.get("local_now") is None]
    assert none and all(
        e == {"kind": "elsewhere", "reason": gen_c_dict.ESP_WAKEUP_CLOCK_REASON} for e in none
    )
    assert {c["expect"]["kind"] for c in wk if c.get("local_now") is not None} == {"frames", "refused", "bad"}


def _ts(case_id):
    e = next(c for c in V["cases"] if c["id"] == case_id)["expect"]
    return int(e["frames"][0]["hex"][4:12], 16)  # bytes 2-5 = Timestamp (dg/h.m0 layout)


def test_wakeup_clock_edges_are_pinned():
    """The generator pins calictl's clock to local_now for every wake-up vector (else the vectors
    would follow the wall clock and never be fresh): exact minute -> tomorrow, year end -> next year."""
    day = lambda *d: calendar.timegm(datetime.datetime(*d).timetuple())  # noqa: E731
    assert _ts('lighting/wakeup/"07:00 on"@empty@exact') == day(2026, 10, 8, 7, 0)
    assert _ts('lighting/wakeup/"07:00 on"@empty@t0') == day(2026, 10, 7, 7, 0)
    assert _ts('lighting/wakeup/"00:00 off"@empty@yearend') == day(2027, 1, 1, 0, 0)
    assert _ts('lighting/wakeup/"23:59 on"@empty@yearend') == day(2027, 1, 1, 23, 59)
    by_id = {c["id"]: c["expect"] for c in V["cases"]}
    assert _ts('lighting/wakeup/"06:20 on"@empty@y2106') == day(2106, 2, 7, 6, 20)
    assert by_id['lighting/wakeup/"07:00 on"@empty@y2106'] == {"kind": "bad"}  # past the 32-bit Timestamp


def test_a_clock_past_the_32_bit_timestamp_never_builds():
    """A page clock past 2106-02-07 (UINT32_MAX + 1, INT64_MAX) can only give a Timestamp the frame
    cannot carry: bad (after the gates, as calictl orders them), never a wrapped made-up time."""
    huge = [c for c in V["cases"] if c["id"].endswith(("@u32", "@i64max"))]
    assert {c["local_now"] for c in huge} == {2**32, 2**63 - 1}
    assert {c["expect"]["kind"] for c in huge} == {"bad", "refused"}
    by_id = {c["id"]: c["expect"] for c in huge}
    assert (
        by_id['lighting/wakeup/"07:00 on"@empty@u32']
        == by_id['lighting/wakeup/"07:00 on"@empty@i64max']
        == {"kind": "bad"}
    )


def test_the_app_wakeup_actions_are_all_proven():
    """R2: the four recorded wake-up actions — action 1 (a time edit with no config known) through
    its explicit "07:00 off" form, the other three as recorded."""
    wk = [a for a in V["app"] if a["what"] == "wakeup"]
    built = [a["value"] for a in wk if a["expect"]["kind"] == "frames"]
    assert built == ["07:00 off", "07:00 on", "08:00", "08:00 off"], built
    assert all(a["local_now"] > 1767225600 for a in wk)


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
        "REASON_WAKEUP_NO_AREA",
    ):
        text = getattr(control, name)
        assert text in refused, name
        assert refused[text] & allowed, name  # the same command also passes the gate somewhere
    assert control.WAKEUP_UNKNOWN in refused and refused[control.WAKEUP_UNKNOWN] & allowed


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


def test_a_saved_favourite_in_the_current_frame_adds_to_the_latch():
    """The latch says slot 3 is empty, the current Mode-4 frame is the unit's save of slot 3:
    ``lighting_config`` adds that bit, so the gate allows it (the gate x Mode-4 composition)."""
    by_id = {c["id"]: c["expect"] for c in V["cases"]}
    assert by_id["lighting/profile/3@fav_latched"]["kind"] == "refused"
    assert by_id["lighting/profile/3@fav_latched_saved"]["kind"] == "frames"
