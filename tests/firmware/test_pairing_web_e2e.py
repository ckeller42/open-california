"""Host firmware: the pairing wizard's endpoint over the real HTTP core + NimBLE + the Bumble fake unit.

``cali-host --http PORT --fake-wifi SCRIPT`` (see ``test_web_e2e.py``) driven only through
``GET/POST /api/pairing`` — calictl's wizard contract (``calictl/web.py`` +
``serve.pairing_snapshot``) — as the shared web UI does: start, poll, passkey, poll; then the
session's ``SNAP`` must flow. Covers station mode and the setup hotspot, a wrong passcode, a unit
whose "Gerät verbinden" screen is closed (the fake's ``pair off``), a stale bond after the unit's
Bluetooth reset, cancel/reset, ``409 busy`` while a control command holds the link, and the wizard
clicked in Chromium (EN over the hotspot's ``/app``, DE at ``/`` in station mode).

.. test:: The satellite's pairing wizard pairs the fake unit end to end over HTTP (host tier)
   :id: T_FW_PAIRING_WIZARD_E2E
   :links: R_FW_PAIRING_WIZARD, R_FW_PAIRING_RUNNER
"""

import json
import time

import pytest

from .conftest import HciUnit
from .test_web_e2e import AP_UP, PSK, _request, _require_chromium, _wifi_script, get_json

# One xdist worker: the host_fw fixture runs `make` in firmware/host (like test_host_e2e.py).
pytestmark = [pytest.mark.linux_only, pytest.mark.xdist_group("firmware-host-build")]

IDENTITY = "C0:FF:EE:CA:11:F0"
WIFI = "ap minsel -55 1\njoin minsel ok 192.168.1.42\n"


def _station(host_fw, hu, tmp_path):
    fw = host_fw(hu, http=True, fake_wifi=_wifi_script(tmp_path, WIFI))
    fw.send("wifi set minsel %s" % PSK)
    fw.expect("LOG", lambda line: line == "wifi: online 192.168.1.42")
    return fw


def _hotspot(host_fw, hu, tmp_path, **kw):
    fw = host_fw(hu, http=True, fake_wifi=_wifi_script(tmp_path, WIFI), **kw)
    fw.expect("LOG", lambda line: line == AP_UP)
    return fw


def _post(fw, body):
    r = _request(fw, "POST", "/api/pairing", body, timeout=15)
    return r.status, json.loads(r.body)


def _wait(fw, pred, timeout=40.0):
    """Poll GET /api/pairing (as the wizard does, a bit faster) until ``pred(snapshot)``."""
    end, seen = time.monotonic() + timeout, []
    while time.monotonic() < end:
        s = get_json(fw, "/api/pairing")
        if not seen or seen[-1] != s:
            seen.append(s)
        if pred(s):
            return s
        time.sleep(0.2)
    raise AssertionError(
        "no matching /api/pairing in %.0fs; seen %r; log tail:\n%s" % (timeout, seen, "\n".join(fw.log[-30:]))
    )


def _start(fw):
    status, s = _post(fw, {"action": "start"})
    assert status == 200 and s["state"] in ("scanning", "connecting", "pairing", "waiting_passkey"), s
    return _wait(fw, lambda s: s["state"] in ("waiting_passkey", "error"))


def _http_pair(fw, hu):
    """The wizard's happy path over HTTP -> the final snapshot (bonded or error)."""
    s = _start(fw)
    assert s["state"] == "waiting_passkey", s
    status, _ = _post(fw, {"action": "passkey", "value": "%06d" % hu.call(hu.unit.next_passkey)})
    assert status == 200
    return _wait(fw, lambda s: s["state"] in ("bonded", "error"))


BONDED = {"state": "bonded", "attempts": 0, "error": None, "address": IDENTITY, "radio_busy": False}


def test_wizard_over_http_bonds_then_snap_flows(host_fw, hci_unit, tmp_path):
    """Station mode: idle without a bond -> start -> passkey -> bonded with the unit's identity, the
    session reads (SNAP), /api/state agrees; a second start is idempotent (no new flow)."""
    fw = _station(host_fw, hci_unit, tmp_path)
    assert get_json(fw, "/api/pairing") == dict(BONDED, state="idle", address=None)
    assert _http_pair(fw, hci_unit) == BONDED
    fw.expect("SNAP", timeout=40)
    assert get_json(fw, "/api/state")["device"]["pairing"] == {"state": "bonded", "address": IDENTITY}
    assert _post(fw, {"action": "start"}) == (200, BONDED)
    time.sleep(1)
    assert get_json(fw, "/api/pairing") == BONDED


def test_wizard_over_the_setup_hotspot(host_fw, hci_unit, tmp_path):
    """No home WiFi: the whole wizard runs while the setup hotspot is up (WiFi stays in setup)."""
    fw = _hotspot(host_fw, hci_unit, tmp_path)
    assert _http_pair(fw, hci_unit) == BONDED
    fw.expect("SNAP", timeout=40)
    assert get_json(fw, "/api/wifi")["mode"] == "setup"


def test_wrong_passkey_retries_then_bonds(host_fw, hci_unit, tmp_path):
    """A mistyped code: the unit refuses, the runner retries (attempts 1) and asks for the next
    code — the right one then bonds."""
    fw = _station(host_fw, hci_unit, tmp_path)
    assert _start(fw)["state"] == "waiting_passkey"
    shown = hci_unit.call(hci_unit.unit.next_passkey)
    assert _post(fw, {"action": "passkey", "value": "%06d" % ((shown + 1) % 1000000)})[0] == 200
    s = _wait(fw, lambda s: s["state"] == "waiting_passkey" and s["attempts"] == 1 or s["state"] == "error")
    assert (s["state"], s["attempts"]) == ("waiting_passkey", 1), s
    assert (
        _post(fw, {"action": "passkey", "value": "%06d" % hci_unit.call(hci_unit.unit.next_passkey)})[0]
        == 200
    )
    s = _wait(fw, lambda s: s["state"] in ("bonded", "error"))
    assert (s["state"], s["address"]) == ("bonded", IDENTITY), s


async def _pairing_mode(unit, on):
    """The fake's console ``pair on|off``: the unit's "Gerät verbinden" screen open or closed."""
    unit.pairing_mode = on


def test_unit_not_in_pairing_mode_is_pairing_failed(host_fw, tmp_path):
    """The unit's pairing screen is closed: every attempt is refused -> error pairing_failed after
    3 attempts, and Try again (start) begins a fresh flow from error."""
    hu = HciUnit()
    try:
        hu.call(_pairing_mode, hu.unit, False)
        fw = _hotspot(host_fw, hu, tmp_path)
        assert _post(fw, {"action": "start"})[0] == 200
        s = _wait(fw, lambda s: s["state"] == "error", timeout=90)
        assert s == {
            "state": "error",
            "attempts": 3,
            "error": "pairing_failed",
            "address": None,
            "radio_busy": False,
        }
        hu.call(_pairing_mode, hu.unit, True)
        assert _http_pair(fw, hu)["state"] == "bonded"
    finally:
        hu.close()


def test_stale_bond_repairs_over_http(host_fw, hci_unit, tmp_path):
    """The unit's Bluetooth was reset (it dropped our bond) while the satellite kept it: the session's
    reconnects are refused, the wizard still shows the old address, and start -> passkey bonds
    afresh — the transport's probe proves the stored bond stale and drops it -> SNAP flows again."""
    store = tmp_path / "store"
    fw = _station(host_fw, hci_unit, tmp_path)
    assert _http_pair(fw, hci_unit) == BONDED
    fw.stop()
    hci_unit.call(hci_unit.unit.forget_bonds)
    fw2 = host_fw(hci_unit, store_dir=store, http=True, fake_wifi=_wifi_script(tmp_path, WIFI))
    fw2.expect("LOG session: reconnect in", timeout=40)  # the stale LTK is refused: no session
    assert get_json(fw2, "/api/pairing") == dict(BONDED, state="idle")  # the (stale) bond is kept
    mark = len(fw2.log)
    s = _http_pair(fw2, hci_unit)
    pair_log = [line for line in fw2.log[mark:] if line.startswith("LOG pair:")]
    # bonded; attempts 1 when the unit hung up on the stale key (the retry then pairs afresh)
    assert (s["state"], s["address"], s["error"]) == ("bonded", IDENTITY, None) and s["attempts"] <= 1, (
        s,
        pair_log,
    )
    assert any(line.startswith("LOG pair: the stored bond is stale") for line in pair_log), pair_log
    fw2.expect("SNAP", timeout=40)


def test_cancel_and_reset(host_fw, hci_unit, tmp_path):
    """Cancel mid-flow -> idle; reset needs confirm (400 confirm_required, bond kept), with it the
    bond is dropped -> idle without an address, and a fresh pair works."""
    fw = _station(host_fw, hci_unit, tmp_path)
    assert _start(fw)["state"] == "waiting_passkey"
    hci_unit.call(hci_unit.unit.next_passkey)
    status, s = _post(fw, {"action": "cancel"})
    assert status == 200 and s["state"] == "idle", s
    assert _http_pair(fw, hci_unit) == BONDED
    assert _post(fw, {"action": "reset"}) == (400, {"error": "confirm_required"})
    assert get_json(fw, "/api/pairing")["address"] == IDENTITY
    assert _post(fw, {"action": "reset", "confirm": True})[0] == 200
    s = _wait(fw, lambda s: s["state"] == "idle" and s["address"] is None, timeout=15)
    assert s == dict(BONDED, state="idle", address=None)
    assert _http_pair(fw, hci_unit) == BONDED


def test_start_while_a_command_runs_is_busy(host_fw, rec_unit, tmp_path):
    """A console ``set`` holds the sequencer (the unit ACKs 2.5 s late): start and reset answer
    409 busy and touch nothing; once the command ended (its writes ACKed or timed out), start is
    accepted again."""
    from .test_control_e2e import _ack_after, _online  # lazily: that module imports test_web_e2e

    hu, _ = rec_unit
    fw = _online(host_fw, hu, tmp_path)
    hu.call(_ack_after, hu.unit, 2.5)
    fw.send("set lighting kitchen 5")
    fw.expect("LOG", lambda line: line == "control: lighting/kitchen sending")
    assert _post(fw, {"action": "start"}) == (409, {"error": "busy"})
    assert _post(fw, {"action": "reset", "confirm": True}) == (409, {"error": "busy"})
    end = time.monotonic() + 15  # the command (two late ACKs) ends: start is accepted again
    while (got := _post(fw, {"action": "start"}))[0] == 409 and time.monotonic() < end:
        time.sleep(0.5)
    assert got == (200, BONDED)  # bonded: the SM ignores start
    assert get_json(fw, "/api/pairing") == BONDED


@pytest.mark.parametrize(
    "locale,hotspot,words",
    [
        (
            "en-US",
            True,
            ("Set up remote control", "I'm on that screen", "Connect now", "Send", "✓ Paired — "),
        ),
        (
            "de-DE",
            False,
            (
                "Fernsteuerung einrichten",
                "Ich bin auf diesem Bildschirm",
                "Jetzt verbinden",
                "Senden",
                "✓ Gekoppelt — ",
            ),
        ),
    ],
)
def test_wizard_in_browser(host_fw, hci_unit, tmp_path, locale, hotspot, words):
    """The shared wizard clicked in Chromium against the real firmware: over the setup hotspot at
    ``/app`` (EN) and at ``/`` in station mode (DE) — banner button, ready checkbox, Connect, the
    code the fake unit shows, ✓ Paired; no uncaught page error.

    .. test:: The shared wizard pairs the real host firmware from Chromium, EN + DE
       :id: T_FW_PAIRING_WIZARD_UI
       :links: R_FW_PAIRING_WIZARD
    """
    sync_playwright = _require_chromium()
    go, ready, connect, send, paired = words
    fw = _hotspot(host_fw, hci_unit, tmp_path) if hotspot else _station(host_fw, hci_unit, tmp_path)
    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_context(locale=locale).new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto("http://127.0.0.1:%d%s" % (fw.http_port, "/app" if hotspot else "/"))
        page.locator("#unpaired-banner").get_by_role("button", name=go).click(timeout=15000)
        page.get_by_label(ready).check()
        page.get_by_role("button", name=connect).click()
        page.locator("#pairing-passkey").wait_for(timeout=40000)
        page.locator("#pairing-passkey").fill("%06d" % hci_unit.call(hci_unit.unit.next_passkey))
        page.get_by_role("button", name=send).click()
        page.get_by_text(paired + IDENTITY).wait_for(timeout=40000)
        browser.close()
    assert not errors, errors
    fw.expect("SNAP", timeout=40)


async def _hold_connects(hu, on):
    """The firmware-side controller keeps every LE connection pending while on (no link completes)."""
    hu.fw_controller.hold_connects = on


def _bonded_and_reading(host_fw, hu, tmp_path):
    """A satellite bonded in an earlier boot (pairing SM idle, bond stored) whose session reads."""
    fw = _station(host_fw, hu, tmp_path)
    assert _http_pair(fw, hu) == BONDED
    fw.stop()
    fw2 = host_fw(hu, store_dir=tmp_path / "store", http=True, fake_wifi=_wifi_script(tmp_path, WIFI))
    fw2.expect("SNAP", timeout=40)
    assert get_json(fw2, "/api/pairing") == dict(BONDED, state="idle")
    return fw2


def test_cancelled_repair_resumes_the_bonded_session(host_fw, hci_unit, tmp_path):
    """Connect now on a bonded satellite, then Cancel before the pair (the connect held pending):
    back to idle with the bond, and the session reconnects by bond — SNAP flows again (review I1:
    it used to stay offline until a reboot)."""
    fw = _bonded_and_reading(host_fw, hci_unit, tmp_path)
    hci_unit.call(_hold_connects, hci_unit, True)
    assert _post(fw, {"action": "start"})[0] == 200
    _wait(fw, lambda s: s["state"] == "connecting", timeout=20)
    status, s = _post(fw, {"action": "cancel"})
    assert status == 200 and s == dict(BONDED, state="idle"), s
    mark = len(fw.log)
    hci_unit.call(_hold_connects, hci_unit, False)
    fw.expect("SNAP", timeout=40)
    assert any(line.startswith("SNAP") for line in fw.log[mark:])
    assert get_json(fw, "/api/state")["device"]["link"]["up"] is True


def test_failed_repair_resumes_the_bonded_session(host_fw, hci_unit, tmp_path):
    """Connect now on a bonded satellite that never gets a link (the connect held pending): error
    (NimBLE's connect timeout -> connect_failed after 3 attempts, or the SM's connecting timeout) before
    any pair(), so the bond is kept and reported, and the session reconnects by
    bond once the unit is reachable — SNAP flows again (review I1)."""
    fw = _bonded_and_reading(host_fw, hci_unit, tmp_path)
    hci_unit.call(_hold_connects, hci_unit, True)
    assert _post(fw, {"action": "start"})[0] == 200
    s = _wait(fw, lambda s: s["state"] == "error", timeout=60)
    assert s["error"] in ("connect_failed", "timeout") and s["address"] == IDENTITY, s
    mark = len(fw.log)
    hci_unit.call(_hold_connects, hci_unit, False)
    fw.expect("SNAP", timeout=60)
    assert any(line.startswith("SNAP") for line in fw.log[mark:])


def test_connect_now_on_a_working_bond_keeps_it(host_fw, hci_unit, tmp_path):
    """Probe before replace (calictl #201): "Connect now" on a bonded satellite while the unit is NOT
    on "Gerät verbinden" re-encrypts with the stored bond, which works — the flow ends bonded with no
    passkey and no SMP pairing, the bond is kept and SNAP flows. (It used to delete the bond before
    pairing, so the refused pair left the satellite unpaired.)

    .. test:: Starting the wizard never destroys a working bond on the satellite
       :id: T_FW_PAIRING_PROBE_KEEPS_BOND
       :links: R_FW_PAIRING_WIZARD
    """
    fw = _bonded_and_reading(host_fw, hci_unit, tmp_path)
    hci_unit.call(_pairing_mode, hci_unit.unit, False)
    mark = len(fw.log)
    assert _post(fw, {"action": "start"})[0] == 200
    s = _wait(fw, lambda s: s["state"] in ("bonded", "error"), timeout=60)
    assert s == BONDED, (s, fw.log[mark:][-30:])
    assert not any('"waiting_passkey"' in line for line in fw.log[mark:])
    assert any(line == "LOG pair: the stored bond works, keeping it" for line in fw.log[mark:])
    mark = len(fw.log)
    fw.expect("SNAP", timeout=40)
    assert get_json(fw, "/api/state")["device"]["link"]["up"] is True
