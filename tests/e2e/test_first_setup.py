"""Browser e2e of the guided first-time setup of a fresh satellite (``#setup/<step>``): welcome, WiFi,
pair, Victron, sensors, remote (hub, LoRa switch, the remote app), home screen, done — over ``tools.ux_gallery.EspStub``, no firmware, BLE or
radio. WiFi cannot be skipped; every later step can. A reload on the station address resumes at the
first unfinished step (``setupStep``). Every test fails on an uncaught JS / console error.
"""

import re

import pytest

from tests.e2e.test_satsettings import LOCALES, PHONE, _device_status, _open, _posts, tr

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
        expect(addr.locator('a[href="http://192.168.1.57/#setup"]')).to_be_visible()
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
        expect(home).to_contain_text("DHCP-Reservierung" if lang == "de" else "DHCP reservation")
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
    """ "Done" = finished, or left the wizard after WiFi: ‹ from a step posts it once."""
    with _page(station_pending, "/#setup/pair", "en-US") as pg:
        pg.click("#back")
        expect(pg.locator(".tilegrid")).to_be_visible()
        expect(pg.locator("#pairing-card")).to_have_count(0)
        for _ in range(30):
            if _posts(station_pending, "/api/setup"):
                break
            pg.wait_for_timeout(100)
    assert _posts(station_pending, "/api/setup") == [{"done": True}]


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
            tr("The code may have expired — press Pair now again.", lang)
        )


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
            "Then come back here (Safari) and tap Next.",
        ):
            expect(app).to_contain_text(tr(text, lang))
        pg.get_by_role("button", name=tr("Next", lang), exact=True).click()
        expect(pg.locator("#setup-home")).to_be_visible()
    assert _posts(station_pending, "/api/hub") == [HUB_FORM]


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


def test_done_says_the_remote_hub_is_set_up(station_pending):
    _hub(station_pending, configured=True, online=True)
    with _page(station_pending, "/#setup/done", "en-US") as pg:
        expect(pg.locator("#setup-check").get_by_text("Remote hub").locator("..")).to_contain_text("Set up ✓")
