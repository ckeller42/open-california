"""Browser e2e of the guided first-time setup of a fresh satellite (``#setup/<step>``): welcome, WiFi,
pair, Victron, sensors, remote (hub, LoRa switch, the remote app), home screen, done — over ``tools.ux_gallery.EspStub``, no firmware, BLE or
radio. WiFi cannot be skipped; every later step can. A reload on the station address resumes at the
first unfinished step (``setupStep``). Every test fails on an uncaught JS / console error.
"""

import json
import re

import pytest

from tests.e2e.test_satsettings import LOCALES, PHONE, _device_status, _open, _posts, _wait, tr

sync_api = pytest.importorskip("playwright.sync_api")
expect = sync_api.expect

from tools import ux_gallery  # noqa: E402

LOCAL = "http://calictl-esp.local/**"
NO_MDNS = ("ERR_NAME_NOT_RESOLVED", "ERR_FAILED", "calictl-esp.local")
GOVEE = [
    {"id": 0x2001 + 2 * i, "type": "govee", "model": "GVH5074_000%d" % i, "rssi": -60 - i} for i in range(3)
]
MPPT = {"id": 0x2011, "type": "victron_mppt", "model": "SmartSolar", "rssi": -72}


def _pending(stub, seen=(), sensors=False):
    """The station fixture as a satellite just joined from the hotspot: setup pending, no bond yet,
    the Sensors switch ``sensors``, ``seen`` nearby."""
    fx = stub.fixtures["station"]
    dev = fx["/api/state"]["device"]
    dev["setup"] = {"pending": True}
    dev["pairing"] = {"state": "idle", "address": None}
    dev["link"].update(up=False, ready=False, last_snap_age_ms=None)
    dev["experimental"] = {"sensors": sensors}
    fx["/api/pairing"].update(state="idle", address=None)
    fx["/api/sensors"] = {"configured": [], "seen": [dict(r) for r in seen], "scan": "ok"}
    return stub


@pytest.fixture
def setup():
    with ux_gallery.EspStub("setup") as s:
        yield s


@pytest.fixture
def station_pending():
    with ux_gallery.EspStub("station") as s:
        yield _pending(s)


@pytest.fixture
def station_pending_3govee():
    with ux_gallery.EspStub("station") as s:
        yield _pending(s, GOVEE + [MPPT], sensors=True)


def _page(stub, path, locale, **kw):
    return _open(stub, path, locale=locale, viewport=PHONE, **kw)


@pytest.mark.parametrize("locale,lang", LOCALES)
def test_a_fresh_satellite_lands_on_welcome(setup, locale, lang):
    with _page(setup, "/", locale) as pg:
        expect(pg).to_have_url(re.compile(r"#setup/welcome$"))
        expect(pg.locator("#title")).to_have_text(tr("Set up the satellite", lang))
        expect(pg.locator("#setup-welcome")).to_contain_text("VictronConnect")
        pg.get_by_role("button", name=tr("Next", lang), exact=True).click()
        expect(pg).to_have_url(re.compile(r"#setup/wifi$"))
        pg.go_back()
        expect(pg).to_have_url(re.compile(r"#setup/welcome$"))


@pytest.mark.parametrize("locale,lang", LOCALES)
def test_wifi_cannot_be_skipped(setup, locale, lang):
    with _page(setup, "/#setup/wifi", locale) as pg:
        expect(pg.locator("#wifi-form")).to_be_visible()
        expect(pg.locator("#setup-step")).to_contain_text(
            "Persönlicher Hotspot" if lang == "de" else "Personal Hotspot"
        )
        expect(pg.get_by_role("button", name=tr("Skip", lang))).to_have_count(0)


def test_over_the_hotspot_a_later_step_falls_back_to_welcome(setup):
    """Nothing after WiFi works without the home WiFi: a deep link there opens the welcome step."""
    with _page(setup, "/#setup/victron", "en-US") as pg:
        expect(pg).to_have_url(re.compile(r"#setup/welcome$"))


@pytest.mark.parametrize("locale,lang", LOCALES)
def test_after_the_join_the_page_names_the_new_address(setup, locale, lang):
    setup.joins = True
    with _page(setup, "/#setup/wifi", locale) as pg:
        pg.wait_for_selector("#ssid option[value=HomeNet]", state="attached")
        pg.select_option("#ssid", "HomeNet")
        pg.fill("#psk", "secret123")
        pg.click("#connect")
        addr = pg.locator("#setup-next-addr")
        expect(addr).to_contain_text("http://192.168.", timeout=15000)
        expect(addr).to_contain_text("scanne den neuen QR-Code" if lang == "de" else "scan the new QR code")
        expect(addr.locator('a[href="http://192.168.1.57/?lang=%s#setup"]' % lang)).to_be_visible()
        pg.wait_for_timeout(2500)  # a station poll later the page still shows it (no jump to pairing)
        expect(pg).to_have_url(re.compile(r"#setup/wifi$"))
    assert setup.wifi_posts == [{"ssid": "HomeNet", "psk": "secret123"}]


def test_a_reload_on_the_station_address_resumes_at_pairing(station_pending):
    with _page(station_pending, "/#setup/welcome", "en-US") as pg:
        expect(pg).to_have_url(re.compile(r"#setup/pair$"))


def test_the_qr_link_without_a_step_resumes_too(station_pending):
    """The display's station QR is ``http://<ip>/#setup``: no step, so the state decides."""
    with _page(station_pending, "/#setup", "en-US") as pg:
        expect(pg).to_have_url(re.compile(r"#setup/pair$"))


def test_a_pending_setup_on_home_lands_in_the_wizard(station_pending):
    with _page(station_pending, "/", "en-US") as pg:
        expect(pg).to_have_url(re.compile(r"#setup/pair$"))


def test_a_finished_setup_lands_on_home(station_pending):
    station_pending.fixtures["station"]["/api/state"]["device"]["setup"] = {"pending": False}
    with _page(station_pending, "/", "en-US") as pg:
        expect(pg.locator(".tilegrid")).to_be_visible()
        assert pg.evaluate("location.hash") == ""


@pytest.mark.parametrize("locale,lang", LOCALES)
def test_the_pair_step_pairs_with_a_countdown(station_pending, locale, lang):
    with _page(station_pending, "/#setup/pair", locale) as pg:
        step = pg.locator("#setup-step")
        expect(step).to_contain_text("Einstellungen → Bluetooth → Gerät verbinden")
        pg.get_by_role("button", name=tr("Pair now", lang), exact=True).click()
        expect(pg.locator("#setup-count")).to_contain_text(re.compile(tr("{n} s left", lang, n=r"\d+")))
        pg.fill("#pairing-passkey", "123456")
        pg.get_by_role("button", name=tr("Send", lang)).click()
        expect(pg.locator("#pairing-card")).to_contain_text(tr("✓ Paired — ", lang))
        pg.get_by_role("button", name=tr("Next", lang), exact=True).click()
        expect(pg).to_have_url(re.compile(r"#setup/victron$"))
    assert [b["action"] for b in station_pending.pairing_posts] == ["start", "passkey"]


def test_the_pair_step_disables_pair_now_in_flight_and_keeps_a_failure(station_pending):
    """R_UI_VISIBLE_FAILURES on the setup step: "Pair now" is disabled while its POST runs (one
    request per tap), and a failed request stays as a row on the step (here: 409 busy)."""
    held = []
    with _page(station_pending, "/#setup/pair", "en-US", allow=("409",)) as pg:
        pg.route("**/api/pairing", lambda r: held.append(r) if r.request.method == "POST" else r.continue_())
        start = pg.get_by_role("button", name="Pair now", exact=True)
        start.click()
        expect(start).to_be_disabled()
        assert len(held) == 1
        held[0].fulfill(status=409, content_type="application/json", body='{"error": "busy"}')
        err = pg.locator("#setup-pair-err")
        expect(err).to_have_text(
            "The satellite is still sending the previous command — try again in a moment"
        )
        expect(err).to_be_visible()
        expect(start).to_be_enabled()


def test_the_pair_step_can_be_skipped(station_pending):
    with _page(station_pending, "/#setup/pair", "en-US") as pg:
        pg.get_by_role("button", name="Skip").click()
        expect(pg).to_have_url(re.compile(r"#setup/victron$"))
    assert station_pending.pairing_posts == []


@pytest.mark.parametrize("locale,lang", LOCALES)
def test_answering_victron_yes_turns_the_sensors_switch_on(station_pending, locale, lang):
    station_pending.fixtures["station"]["/api/sensors"]["seen"] = [dict(MPPT)] + [dict(g) for g in GOVEE]
    with _page(station_pending, "/#setup/victron", locale) as pg:
        pg.get_by_role("button", name=tr("Yes", lang), exact=True).click()
        expect(pg.locator("#setup-step")).to_contain_text("Instant readout details")
        rows = pg.locator("#setup-sens .seen-row")
        expect(rows).to_have_count(1)  # Victron only: the Govee ones come on the sensors step
        expect(rows).to_contain_text("SmartSolar")
        expect(
            pg.locator("#setup-victron-next")
        ).to_be_focused()  # the answered question took the focus along
    assert {"sensors": True} in _posts(station_pending, "/api/experimental")


def test_answering_victron_no_leaves_the_switch_off(station_pending):
    with _page(station_pending, "/#setup/victron", "en-US") as pg:
        pg.get_by_role("button", name="No", exact=True).click()
        expect(pg).to_have_url(re.compile(r"#setup/sensors$"))
    assert _posts(station_pending, "/api/experimental") == []


@pytest.mark.parametrize("locale,lang", LOCALES)
def test_all_seen_govee_sensors_are_listed(station_pending_3govee, locale, lang):
    with _page(station_pending_3govee, "/#setup/sensors", locale) as pg:
        expect(pg.locator("#setup-step")).to_contain_text("innen, dach")
        expect(pg.locator("#setup-sens .seen-row")).to_have_count(3)


@pytest.mark.parametrize("locale,lang", LOCALES)
def test_no_mdns_still_shows_the_ip_and_the_reservation_hint(station_pending, locale, lang):
    with _page(station_pending, "/#setup/home", locale, allow=NO_MDNS) as pg:
        pg.route(LOCAL, lambda r: r.abort())
        home = pg.locator("#setup-home")
        # the probe can start before the route: a real .local lookup ends by its 3 s timeout
        expect(home).to_contain_text(
            "DHCP-Reservierung" if lang == "de" else "DHCP reservation", timeout=10000
        )
        expect(home.locator('a[href="http://192.168.1.57/"]')).to_be_visible()


@pytest.mark.parametrize("locale,lang", LOCALES)
def test_done_posts_setup_done_and_shows_skipped_rows(station_pending, locale, lang):
    with _page(station_pending, "/#setup/done", locale) as pg:
        check = pg.locator("#setup-check")
        expect(check).to_contain_text("HomeNet ✓")
        pg.get_by_role("button", name=tr("Done", lang), exact=True).click()
        expect(pg.locator("#setup-done-msg")).to_contain_text(tr("✓ Setup done", lang))
        assert {"done": True} in _posts(station_pending, "/api/setup")
        expect(check).to_contain_text(tr("not set up", lang))
        pg.get_by_role("button", name=tr("Open the app", lang)).click()
        expect(pg.locator(".tilegrid")).to_be_visible()
        expect(pg.locator("#pairing-card")).to_have_count(0)  # the wizard's pairing card stays in the wizard
        pg.go_back()
        check.get_by_role("button", name=tr("Satellite settings ›", lang)).first.click()
        expect(pg.locator("#title")).to_have_text(tr("Satellite settings", lang))


def test_leaving_after_wifi_counts_as_done(station_pending):
    """ "Done" = finished, or left the wizard after WiFi: ‹ walks the steps back (also from a landed
    step), from the first step it leaves for Home and posts it once. ⋮ then leads back in."""
    with _page(station_pending, "/#setup/pair", "en-US") as pg:
        pg.click("#back")
        expect(pg).to_have_url(re.compile(r"#setup/wifi$"))
        pg.click("#back")
        expect(pg).to_have_url(re.compile(r"#setup/welcome$"))
        assert _posts(station_pending, "/api/setup") == []
        pg.click("#back")
        expect(pg.locator(".tilegrid")).to_be_visible()
        expect(pg.locator("#pairing-card")).to_have_count(0)
        for _ in range(30):
            if _posts(station_pending, "/api/setup"):
                break
            pg.wait_for_timeout(100)
        pg.click("#menu")
        pg.get_by_role("menuitem", name="Continue setup").click()
        expect(pg).to_have_url(re.compile(r"#setup/pair$"))
    assert _posts(station_pending, "/api/setup") == [{"done": True}]


@pytest.mark.parametrize("locale,lang", LOCALES)
def test_back_goes_to_the_step_before_after_next(station_pending, locale, lang):
    with _page(station_pending, "/#setup/sensors", locale) as pg:
        pg.get_by_role("button", name=tr("Skip", lang)).click()
        expect(pg).to_have_url(re.compile(r"#setup/remote$"))
        pg.click("#back")
        expect(pg).to_have_url(re.compile(r"#setup/sensors$"))
        pg.click("#back")
        expect(pg).to_have_url(re.compile(r"#setup/victron$"))
        expect(pg.get_by_role("button", name=tr("Yes", lang), exact=True)).to_be_visible()
        pg.click("#menu")
        expect(pg.get_by_role("menuitem", name=tr("Continue setup", lang))).to_have_count(0)  # in the wizard


def test_a_pending_setup_offers_continue_setup_on_home(station_pending):
    """A detour to Home (a deep link) while device.setup.pending: ⋮ → Continue setup."""
    with _page(station_pending, "/#devicestatus", "en-US") as pg:
        pg.click("#menu")
        pg.get_by_role("menuitem", name="Continue setup").click()
        expect(pg).to_have_url(re.compile(r"#setup/pair$"))


def test_the_mdns_move_carries_the_language(station_pending):
    """localStorage is per origin: the move to calictl-esp.local takes the choice along as ?lang=."""
    seen = []

    def local(r):
        seen.append(r.request.url)
        r.fulfill(status=200, content_type="text/html", body="<html></html>")

    with _page(station_pending, "/#devicestatus", "en-US") as pg:
        pg.click("#menu")
        pg.get_by_role("menuitem", name="Deutsch").click()  # the choice the user made on this origin
        pg.route(LOCAL, local)
        pg.goto(station_pending.base + "/#setup/home")
        pg.wait_for_url("http://calictl-esp.local/**")
        assert pg.url == "http://calictl-esp.local/?lang=de#setup/home"


def test_a_lang_parameter_sets_the_language_once(station_pending):
    with _page(station_pending, "/?lang=de#setup/pair", "en-US") as pg:
        expect(pg.locator("#title")).to_have_text(tr("Set up the satellite", "de"))
        assert pg.evaluate("location.search + location.hash") == "#setup/pair"
        assert pg.evaluate("localStorage.getItem('calictl.lang')") == "de"
        pg.reload()
        expect(pg.locator("#title")).to_have_text(tr("Set up the satellite", "de"))


def _bond(stub):
    """The unit accepts the code (as the firmware's runner would report it)."""
    fx = stub.fixtures["station"]
    fx["/api/pairing"].update(state="bonded", address="C0:FF:EE:CA:11:F0")
    fx["/api/state"]["device"]["pairing"] = {"state": "bonded", "address": "C0:FF:EE:CA:11:F0"}


def test_a_detour_to_device_status_does_not_end_the_setup(station_pending):
    """Only Home ends the wizard: ⋮ → Device status and back returns to the step, nothing posted."""
    with _page(station_pending, "/#setup/victron", "en-US") as pg:
        _device_status(pg)
        pg.go_back()
        expect(pg).to_have_url(re.compile(r"#setup/victron$"))
        expect(pg.get_by_role("button", name="Yes", exact=True)).to_be_visible()
        pg.wait_for_timeout(500)
    assert _posts(station_pending, "/api/setup") == []


def test_the_pair_step_follows_the_flow_after_a_detour(station_pending):
    with _page(station_pending, "/#setup/pair", "en-US") as pg:
        pg.get_by_role("button", name="Pair now", exact=True).click()
        expect(pg.locator("#pairing-passkey")).to_be_visible()
        _device_status(pg)
        pg.go_back()
        _bond(station_pending)  # the code typed on another device / the unit's own answer
        expect(pg.locator("#pairing-card")).to_contain_text("✓ Paired")


def test_a_reload_in_the_code_window_polls_the_flow_again(station_pending):
    with _page(station_pending, "/#setup/pair", "en-US") as pg:
        pg.get_by_role("button", name="Pair now", exact=True).click()
        expect(pg.locator("#pairing-passkey")).to_be_visible()
        pg.reload()
        expect(pg.locator("#pairing-passkey")).to_be_visible()
        _bond(station_pending)
        expect(pg.locator("#pairing-card")).to_contain_text("✓ Paired")
    assert _posts(station_pending, "/api/setup") == []


@pytest.mark.parametrize("locale,lang", LOCALES)
def test_the_countdown_ends_in_a_retry_hint(station_pending, locale, lang):
    with _page(station_pending, "/#setup/pair", locale) as pg:
        pg.clock.install()
        pg.get_by_role("button", name=tr("Pair now", lang), exact=True).click()
        expect(pg.locator("#setup-count")).to_contain_text(re.compile(tr("{n} s left", lang, n=r"\d+")))
        pg.clock.run_for(31000)
        expect(pg.locator("#setup-count")).to_have_text(
            tr("The code may have expired — press New code.", lang)
        )


def test_a_timeout_after_the_code_says_so_and_new_code_restarts(station_pending):
    """The firmware's one "timeout" covers every phase: after the code showed, the page names the code wait,
    not "No vehicle found". "New code" cancels and starts again without the 60 s wait."""
    with _page(station_pending, "/#setup/pair", "en-US") as pg:
        pg.get_by_role("button", name="Pair now", exact=True).click()
        expect(pg.locator("#pairing-passkey")).to_be_visible()
        pg.click("#pairing-newcode")
        expect(pg.locator("#pairing-passkey")).to_be_visible()
        # every POST answered with a snapshot: no request error (a misplaced else once showed one)
        _wait(
            pg, lambda: len(station_pending.pairing_posts) >= 3, "the second start"
        )  # the old passkey shows until then: a race under load
        expect(pg.locator("#pairing-card")).not_to_contain_text("Pairing request failed")
        assert [b["action"] for b in station_pending.pairing_posts] == ["start", "cancel", "start"]
        station_pending.fixtures["station"]["/api/pairing"].update(state="error", error="timeout")
        expect(pg.locator("#pairing-card")).to_contain_text("The code was not entered in time.")
        expect(pg.locator("#pairing-card")).not_to_contain_text("No vehicle found")


def test_a_good_code_pairs_without_a_request_error(station_pending):
    """The run on the test board: "✓ Paired" came with "Pairing request failed" (an else bound to the wrong if)."""
    with _page(station_pending, "/#setup/pair", "en-US") as pg:
        pg.get_by_role("button", name="Pair now", exact=True).click()
        pg.fill("#pairing-passkey", station_pending.pairing_passkey)
        pg.get_by_role("button", name="Send").click()
        expect(pg.locator("#pairing-card")).to_contain_text("✓ Paired")
        expect(pg.locator("#pairing-card")).not_to_contain_text("Pairing request failed")


def test_a_timeout_before_any_code_still_says_no_vehicle(station_pending):
    with _page(station_pending, "/#setup/pair", "en-US") as pg:
        station_pending.pairing_scan_polls = 1000  # the search never finds the unit
        pg.get_by_role("button", name="Pair now", exact=True).click()
        pg.wait_for_timeout(1500)
        station_pending.fixtures["station"]["/api/pairing"].update(state="error", error="timeout")
        expect(pg.locator("#pairing-card")).to_contain_text("No vehicle found.")


def test_the_home_step_saves_the_apps_start_url(station_pending):
    """iPhone "Add to Home Screen" saves the page's URL: the manifest's start URL, not #setup/home."""
    with _page(station_pending, "/#setup/home", "en-US", allow=NO_MDNS) as pg:
        expect(pg.locator("#setup-home")).to_be_visible()
        assert pg.evaluate("location.pathname + location.hash") == "/app"


# ---- the Remote access step (rulings R39, R43), the home-screen names (R40, R44), the hub reason (R45) --

HUB_FORM = {
    "url": "wss://h.example:8084/mqtt",
    "van": "van-1",
    "user": "vanuser",
    "pass": "vanpass",
    "app_user": "appuser",
    "app_pass": "apppass",
}


def _hub(stub, **over):
    stub.fixtures["station"]["/api/state"]["device"]["hub"].update(over)


def test_the_remote_step_comes_after_sensors_and_can_be_skipped(station_pending):
    with _page(station_pending, "/#setup/sensors", "en-US") as pg:
        pg.get_by_role("button", name="Skip").click()
        expect(pg).to_have_url(re.compile(r"#setup/remote$"))
        expect(pg.locator("#setup-step")).to_contain_text("Step 6 of 8")
        expect(pg.locator("#setup-step h2").first).to_have_text("Remote access")
        pg.go_back()
        expect(pg).to_have_url(re.compile(r"#setup/sensors$"))
        pg.go_forward()
        expect(pg).to_have_url(re.compile(r"#setup/remote$"))
        pg.get_by_role("button", name="Skip").click()
        expect(pg.locator("#setup-home")).to_be_visible()
    assert _posts(station_pending, "/api/hub") == []


@pytest.mark.parametrize("locale,lang", LOCALES)
def test_the_remote_step_saves_the_hub_then_shows_the_app(station_pending, locale, lang):
    """The Hub card's form saves through POST /api/hub; the app part (link, install steps, scan hint,
    come back) shows only once the hub is connected."""
    station_pending.fixtures["station"]["/api/state"]["device"]["experimental"]["lora"] = False
    station_pending.fixtures["station"]["/api/hub"]["van"] = "old-van"  # to see the GET land before typing
    with _page(station_pending, "/#setup/remote", locale) as pg:
        app = pg.locator("#setup-app")
        expect(pg.locator("#hub-status")).to_have_text(tr("Not set up", lang))
        expect(app).to_be_hidden()
        expect(pg.locator("#hub-van")).to_have_value("old-van")  # the form's GET /api/hub is in
        for k, v in HUB_FORM.items():
            pg.fill("#hub-" + k, v)
        pg.click("#hub-save")
        expect(pg.locator("#hub-msg")).to_have_text(tr("Saved", lang))
        expect(app).to_be_visible()
        expect(app.locator('a[href="https://open-california.github.io/"]')).to_be_visible()
        for text in (
            "iPhone: open the link in Safari, tap Share, then Add to Home Screen. The icon is called OC Remote.",
            "Then come back to this page and tap Next.",
            # G4 + S3: what the first code is for, and how close to hold the phone
            "The first code opens the app's web address (to install the app); the second is the van's code to scan in the app.",
            "Hold the phone about 10 cm in front of the satellite's screen.",
        ):
            expect(app).to_contain_text(tr(text, lang))
        pg.get_by_role("button", name=tr("Next", lang), exact=True).click()
        expect(pg.locator("#setup-home")).to_be_visible()
    assert _posts(station_pending, "/api/hub") == [HUB_FORM]


@pytest.mark.parametrize("locale,lang", LOCALES)
def test_load_from_file_fills_the_hub_form_and_save_posts_it(station_pending, locale, lang):
    """#555: an oc-hub.json (tools/make_hub_file.py) fills the six fields; nothing is sent before Save.
    Another file is refused with a reason and leaves the fields as they were."""
    with _page(station_pending, "/#setup/remote", locale) as pg:
        expect(pg.locator("#hub-status")).to_have_text(tr("Not set up", lang))
        pg.set_input_files(
            "#hub-file",
            files=[
                {
                    "name": "x.json",
                    "mimeType": "application/json",
                    "buffer": json.dumps({"url": "wss://x"}).encode(),
                }
            ],
        )
        expect(pg.locator("#hub-msg")).to_have_text(tr("Not a hub setup file (oc-hub.json)", lang))
        expect(pg.locator("#hub-msg")).to_have_class("warn")  # an error stays red
        expect(pg.locator("#hub-url")).to_have_value("")
        pg.set_input_files(
            "#hub-file",
            files=[
                {
                    "name": "oc-hub.json",
                    "mimeType": "application/json",
                    "buffer": json.dumps({"format": "oc-hub/1", **HUB_FORM}).encode(),
                }
            ],
        )
        expect(pg.locator("#hub-msg")).to_have_text(
            tr("Loaded: tap Save, then delete the file (it holds the van password).", lang)
        )
        expect(pg.locator("#hub-msg")).to_have_class("note")  # a success is not a warning
        for k, v in HUB_FORM.items():
            expect(pg.locator("#hub-" + k)).to_have_value(v)
        assert _posts(station_pending, "/api/hub") == []
        pg.click("#hub-save")
        expect(pg.locator("#hub-msg")).to_have_text(tr("Saved", lang))
        expect(pg.locator("#hub-msg")).to_have_class("note")
    assert _posts(station_pending, "/api/hub") == [HUB_FORM]
    assert _posts(station_pending, "/api/experimental") == []


def test_a_hub_file_with_lora_turns_the_lora_switch_on_at_save(station_pending):
    station_pending.fixtures["station"]["/api/state"]["device"]["experimental"]["lora"] = False
    with _page(station_pending, "/#setup/remote", "en-US") as pg:
        expect(pg.locator("#hub-status")).to_have_text("Not set up")
        pg.set_input_files(
            "#hub-file",
            files=[
                {
                    "name": "oc-hub.json",
                    "mimeType": "application/json",
                    "buffer": json.dumps({"format": "oc-hub/1", "lora": True, **HUB_FORM}).encode(),
                }
            ],
        )
        expect(pg.locator("#hub-url")).to_have_value(HUB_FORM["url"])
        expect(pg.locator("#hub-msg")).to_contain_text("LoRa goes on too")  # the switch is in another card
        assert pg.locator("#hub-pass").get_attribute("placeholder") in (
            None,
            "",
        )  # nothing stored: not "unchanged"
        assert _posts(station_pending, "/api/experimental") == []
        pg.click("#hub-save")
        expect(pg.locator("#hub-msg")).to_have_text("Saved")
    assert _posts(station_pending, "/api/hub") == [HUB_FORM]
    assert _posts(station_pending, "/api/experimental") == [{"lora": True}]


def test_the_lora_part_works_without_a_hub(station_pending):
    """R43: the LoRa part is independent: its switch and its next step show without any hub."""
    station_pending.fixtures["station"]["/api/state"]["device"]["experimental"]["lora"] = False
    with _page(station_pending, "/#setup/remote", "en-US") as pg:
        lora = pg.locator("#setup-app-lora")
        expect(lora).to_be_hidden()
        pg.click("#lora-toggle")
        expect(lora).to_be_visible()
        expect(lora).to_contain_text("It needs a TTN account and a full-rights key")
        expect(
            lora.locator('a[href*="howto-lora-remote-setup.html#the-things-network-console"]')
        ).to_be_visible()
        expect(pg.locator("#setup-app")).to_be_hidden()
    assert _posts(station_pending, "/api/experimental") == [{"lora": True}]
    assert _posts(station_pending, "/api/hub") == []


@pytest.mark.parametrize(
    "reason,text",
    [
        ("auth", "Not connected: the broker refused the login. Check the van user and the van password."),
        ("dns", "Not connected: the broker address was not found. Check the broker address."),
        (None, "Not connected yet"),
    ],
)
def test_a_configured_hub_that_is_not_connected_says_why(station_pending, reason, text):
    """R45 and review finding 7: configured but offline keeps the app part hidden and names the reason."""
    _hub(station_pending, configured=True, online=False, reason=reason)
    with _page(station_pending, "/#setup/remote", "en-US") as pg:
        expect(pg.locator("#hub-status")).to_have_text(text)
        expect(pg.locator("#setup-hub-wait")).to_be_visible()
        expect(pg.locator("#setup-app")).to_be_hidden()


@pytest.mark.parametrize("configured", [False, True])
def test_the_home_step_names_the_icons(station_pending, configured):
    """R44: OC Van (this page) always; OC Remote (from the Remote access step) only with a hub config."""
    _hub(station_pending, configured=configured)
    with _page(station_pending, "/#setup/home", "en-US", allow=NO_MDNS) as pg:
        expect(pg.locator("#setup-icon-local")).to_contain_text("Its icon is called OC Van")
        remote = pg.locator("#setup-icon-remote")
        expect(remote).to_be_visible() if configured else expect(remote).to_be_hidden()
        if configured:
            expect(remote).to_contain_text("OC Remote")


def test_done_lists_the_remote_hub(station_pending):
    """Not set up: the row goes back to the Remote access step (its install help), not to settings."""
    with _page(station_pending, "/#setup/done", "en-US") as pg:
        row = pg.locator("#setup-check").get_by_text("Remote hub").locator("..")
        row.get_by_role("button", name="Set up ›").click()
        expect(pg).to_have_url(re.compile(r"#setup/remote$"))


@pytest.mark.parametrize("online,text", [(True, "Connected ✓"), (False, "Not connected yet")])
def test_done_says_the_remote_hub_is_connected(station_pending, online, text):
    """The row says what the satellite knows (it reaches the broker), not that the app is installed."""
    _hub(station_pending, configured=True, online=online)
    with _page(station_pending, "/#setup/done", "en-US") as pg:
        row = pg.locator("#setup-check").get_by_text("Remote hub").locator("..")
        expect(row).to_contain_text(text)
        expect(row.get_by_role("button", name="Set up ›")).to_have_count(0 if online else 1)


@pytest.mark.parametrize("locale,lang", LOCALES)
def test_done_says_skipped_for_a_skipped_step(station_pending, locale, lang):
    """Victron "No" and Sensors "Skip" were a choice: neutral "skipped", the settings link stays."""
    with _page(station_pending, "/#setup/victron", locale) as pg:
        pg.get_by_role("button", name=tr("No", lang), exact=True).click()
        pg.get_by_role("button", name=tr("Skip", lang)).click()
        expect(pg).to_have_url(re.compile(r"#setup/remote$"))
        pg.evaluate("location.hash = '#setup/done'")
        check = pg.locator("#setup-check")
        for label in ("Victron", tr("Sensors", lang)):
            row = check.get_by_text(label, exact=True).locator("..")
            expect(row).to_contain_text(tr("skipped", lang))
            expect(row.get_by_role("button", name=tr("Satellite settings ›", lang))).to_be_visible()
        expect(check.get_by_text(tr("Camper unit", lang), exact=True).locator("..")).to_contain_text(
            tr("not set up", lang)
        )


@pytest.mark.parametrize("lora,text", [(None, "On, set up in the app"), ("ttn_keys", "Set up ✓")])
def test_done_lists_lora_while_its_switch_is_on(station_pending, lora, text):
    dev = station_pending.fixtures["station"]["/api/state"]["device"]
    with _page(station_pending, "/#setup/done", "en-US") as pg:
        expect(pg.locator("#setup-check")).to_contain_text("HomeNet ✓")
        expect(pg.locator("#setup-check")).not_to_contain_text("LoRa")  # switch off: no row
    dev["experimental"]["lora"] = True
    if lora:
        dev["lora"] = ux_gallery.lora_device(setup=lora)
    with _page(station_pending, "/#setup/done", "en-US") as pg:
        expect(pg.locator("#setup-check").get_by_text("LoRa", exact=True).locator("..")).to_contain_text(text)


def test_the_wifi_list_drops_searching_once_filled(setup):
    with _page(setup, "/#setup/wifi", "en-US") as pg:
        pg.wait_for_selector("#ssid option[value=HomeNet]", state="attached")
        expect(pg.locator("#setup-msg")).to_have_text("", timeout=1500)  # before the 4 s second read


@pytest.mark.parametrize("locale,lang", LOCALES)
def test_search_for_sensors_says_it_searches_then_none_found(station_pending, locale, lang):
    held = []
    with _page(station_pending, "/#setup/sensors", locale) as pg:
        step = pg.locator("#setup-step")
        expect(step.locator(".btn.primary:visible")).to_have_count(1)  # Search; Next is not a 2nd primary
        pg.route("**/api/experimental", lambda r: held.append(r))
        pg.get_by_role("button", name=tr("Search for sensors", lang)).click()
        expect(pg.locator("#setup-sens-wait")).to_have_text(tr("Searching for sensors…", lang))
        pg.route("**/api/sensors", lambda r: held.append(r))
        held[0].continue_()
        _wait(pg, lambda: len(held) > 1, "the first sensor list")
        expect(pg.locator("#setup-sens-wait")).to_have_text(
            tr("Searching for sensors…", lang)
        )  # until the list
        held[1].continue_()
        pg.unroute("**/api/sensors")
        expect(step).to_contain_text(tr("No sensors found yet (the scan runs every minute).", lang))
        expect(pg.locator("#setup-sens-wait")).to_have_text("")
        expect(step.locator(".btn.primary:visible")).to_have_count(1)  # now Next
        expect(pg.locator("#setup-sens-next")).to_have_class(re.compile(r"\bprimary\b"))


@pytest.mark.parametrize(
    "ua,first,folded",
    [
        ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X)", "setup-app-ios", "setup-app-android"),
        ("Mozilla/5.0 (Linux; Android 15; Pixel 9)", "setup-app-android", "setup-app-ios"),
        (None, None, None),
    ],
)
def test_the_app_install_text_shows_this_phone_first(station_pending, ua, first, folded):
    _hub(station_pending, configured=True, online=True)
    with _page(station_pending, "/#setup/remote", "en-US", **({"user_agent": ua} if ua else {})) as pg:
        app = pg.locator("#setup-app")
        if not ua:  # unknown platform: both, nothing folded
            expect(pg.locator("#setup-app-ios")).to_be_visible()
            expect(pg.locator("#setup-app-android")).to_be_visible()
            expect(app.locator("details")).to_have_count(0)
            return
        expect(pg.locator("#" + first)).to_be_visible()
        expect(pg.locator("#" + folded)).to_be_hidden()
        app.get_by_text("Other phone").click()
        expect(pg.locator("#" + folded)).to_be_visible()


def test_load_from_file_looks_like_a_button(station_pending):
    with _page(station_pending, "/#setup/remote", "en-US") as pg:
        lbl = pg.locator('label[for="hub-file"]')
        expect(lbl).to_be_visible()
        st = lbl.evaluate(
            "e => { const s = getComputedStyle(e); return [s.display, s.marginTop, s.fontSize, s.borderTopStyle] }"
        )
        save = pg.locator("#hub-save").evaluate("e => getComputedStyle(e).fontSize")
        assert st == ["flex", "0px", save, "solid"]


def test_a_late_hub_answer_after_leaving_the_step_is_harmless(station_pending):
    """The Hub form's GET /api/hub can answer after the user tapped Next: it must not write into the gone
    form (a pageerror "Cannot set properties of null" in CI). The answer is held until the done step shows."""
    held = []
    with _page(station_pending, "/#setup/victron", "en-US") as pg:
        pg.route("**/api/hub", lambda route: held.append(route))
        pg.get_by_role("button", name="No", exact=True).click()
        pg.get_by_role("button", name="Skip").click()
        expect(pg).to_have_url(re.compile(r"#setup/remote$"))
        for _ in range(50):
            if held:
                break
            pg.wait_for_timeout(100)
        assert held, "the remote step asked for /api/hub"
        pg.evaluate("location.hash = '#setup/done'")
        expect(pg.locator("#setup-check")).to_be_visible()
        held[0].continue_()  # the late answer arrives now
        pg.wait_for_timeout(500)


def _restore_file(pg, body, name="oc-backup-van-1.json"):
    pg.set_input_files(
        "#restore-file",
        files=[{"name": name, "mimeType": "application/json", "buffer": json.dumps(body).encode()}],
    )


@pytest.mark.parametrize("locale,lang", LOCALES)
def test_the_wifi_step_restores_a_backup_file(setup, locale, lang):
    """A fresh satellite's WiFi step takes the old one's backup file: another file is refused in the page
    (nothing sent); a backup is posted to /api/restore as it is, and the page says what is left to do (pair,
    LoRa). A satellite that is set up already answers 409 not_fresh, and the page says why.

    .. test:: The setup wizard restores a settings backup file on a fresh satellite
       :id: T_UX_SETTINGS_RESTORE
       :links: R_FW_SETTINGS_BACKUP
    """
    backup = {"format": "oc-backup/1", "fw": "abcdef12", "kv": {"wifi_ssid": "SG9tZU5ldA=="}}
    with _page(setup, "/#setup/wifi", locale, allow=("409",)) as pg:  # the not_fresh answer at the end
        msg = pg.locator("#restore-msg")
        expect(pg.locator('label[for="restore-file"]')).to_have_text(tr("Restore from backup file", lang))
        _restore_file(pg, {"format": "oc-hub/1"}, name="oc-hub.json")
        expect(msg).to_have_text(tr("Not a settings backup (oc-backup-….json)", lang))
        expect(msg).to_have_class("warn")
        assert _posts(setup, "/api/restore") == []
        _restore_file(pg, backup)
        expect(msg).to_have_text(
            tr(
                "Restarting… then pair with the camper (one passkey: the bond cannot be copied) and set up LoRa again.",
                lang,
            )
        )
        expect(msg).to_have_class("note")
        setup.post_reply["/api/restore"] = (
            200,
            {"ok": True, "restored": ["wifi_ssid"], "bond": "restored", "reboot": True},
        )
        _restore_file(pg, backup)  # the same satellite: the camper pairing came back
        expect(msg).to_have_text(
            tr("Restarting… The camper pairing was kept (the same satellite); set up LoRa again.", lang)
        )
        setup.post_reply["/api/restore"] = (409, {"ok": False, "error": "not_fresh"})
        _restore_file(pg, backup)
        expect(msg).to_have_text(
            tr("This satellite is set up already: a backup restores only onto a new one.", lang)
        )
    assert _posts(setup, "/api/restore") == [backup, backup, backup]
