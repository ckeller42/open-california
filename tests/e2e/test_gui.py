"""Browser end-to-end tests of the OpenCalifornia web UI, driven against the mock unit.

OPT-IN: needs Playwright + Chromium. Skipped automatically when Playwright isn't installed
(so the stdlib pytest matrix stays green); the dedicated CI `e2e` job installs the browser
and runs these:

    pip install playwright && python -m playwright install chromium
    python -m pytest tests/e2e -q

The suite launches the REAL daemon + web UI over the in-process mock (`tools.run_against_mock
serve --web`), with the actuation delays shrunk via CALICTL_ARM_DELAY_S / CALICTL_SETTLE_S so a
command takes ~0.6 s — fast, but still long enough to observe the in-flight feedback (the
"Sending…" status + the result toast). It verifies the exact UX the mechanical first version
lacked: real data readouts, installed-gating, and action feedback — plus the state-gating the unit
imposes (camping lights/USB need master, quiet mode needs the box on, roof move-block) and the
roof press-and-hold → STOP traffic. Every test also doubles as a JS runtime-error gate: the `page`
fixture fails on any uncaught `pageerror`/console error, and one test opens every tile.
"""

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request

import pytest

from tools.mock_unit import FakePairingTransport

sync_api = pytest.importorskip("playwright.sync_api")
sync_playwright = sync_api.sync_playwright
expect = sync_api.expect

# Skip (don't ERROR) when Playwright is installed but its browser binary isn't — a dev who ran
# `pip install playwright` without `playwright install chromium` should get a clean skip, and the
# stdlib matrix stays green; the CI `gui-e2e` job installs the browser and runs these for real.
try:
    with sync_playwright() as _p:
        _p.chromium.launch().close()
except Exception as _e:  # noqa: BLE001 — any launch failure (missing executable, deps) -> skip the module
    pytest.skip("Playwright chromium browser not installed: %s" % _e, allow_module_level=True)

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# These tests share one module-scoped daemon + a real browser, so they must not be spread across
# xdist workers: keep the whole module on a single worker (`--dist loadgroup`, set by tools/ci.sh
# test (also the pre-push hook) and CI). Without it each worker started its OWN daemon, the module fixture below
# deleted the shared cache files out from under a peer mid-run, and the latency assertion competed
# with N browsers — two GUI tests flaked under `-n auto` while passing serially.
pytestmark = pytest.mark.xdist_group("e2e")


# Cache files the e2e daemon writes, namespaced per xdist worker (`PYTEST_XDIST_WORKER` is unset when
# running serially). Belt-and-braces alongside the loadgroup marker: it also keeps two CONCURRENT
# pytest runs (e.g. the pre-push hook while a manual run is open) from clobbering each other.
def _cache(name):
    worker = os.environ.get("PYTEST_XDIST_WORKER", "")
    return "/tmp/calictl_e2e%s_%s" % ("-" + worker if worker else "", name)


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _start_daemon(port, extra_env, expect_installed=True):
    """Launch `tools.run_against_mock serve --web <port>` and block until `/api/state` is ready --
    the subprocess-launch + readiness-poll dance shared by `base_url` (one server for the whole
    module), `pairing_url` (a fresh one per test, so the guided-pairing wizard's server-side state
    machine can't leak between tests), and `unconfigured_pairing_page` (a daemon with NO bond).

    :param expect_installed: when True (the default), wait until `/api/state` reports the mock's
        installed functions -- only true once a session can actually poll the (mock) unit. An
        UNPAIRED daemon (no bond -> `device.UNPAIRED_ADDR`) never gets there: `CamperDevice._session`
        now refuses before any BLE traffic, so no function is ever populated. Pass False for that
        case and this returns as soon as `/api/state` answers with a JSON object containing
        `_meta` -- proof the web server itself is up.
    """
    env = dict(
        os.environ,
        CALICTL_ADDR="MO:CK:CA:MP:ER:00",
        PYTHONUNBUFFERED="1",
        CALICTL_ARM_DELAY_S="0.3",
        CALICTL_SETTLE_S="0.3",
        CALICTL_HEARTBEAT_PERIOD_S="0.1",
        CALICTL_FAST_CONFIRM_S="0.2",  # lighting fast-path Mode-4 confirm window (real default 1.2 s)
        CALICTL_SESSION_WAIT_S="0.3",  # don't idle waiting for a session in the mock e2e
        CALICTL_HEARTBEAT_WARMUP_S="0",
        CALICTL_ENABLE_WRITES="1",  # e2e exercises control writes -> not read-only
        CALICTL_PERSISTENT_SESSION="1",
        CALICTL_WATER_SETTLE_S="0",  # the mock doesn't ramp: show its water on the first poll
    )  # default, explicit for the session-pill test's intent
    env.update(extra_env)
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "tools.run_against_mock",
            "serve",
            "--web",
            str(port),
            "--interval",
            "1",
            "--no-influx",
        ],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    url = "http://127.0.0.1:%d" % port
    deadline = time.time() + 30
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError("server exited early:\n" + proc.stdout.read().decode())
        try:
            with urllib.request.urlopen(url + "/api/state", timeout=1) as r:
                body = json.load(r)
                if not expect_installed:
                    if isinstance(body, dict) and "_meta" in body:
                        return proc, url
                elif len([k for k, v in body.items() if isinstance(v, dict) and v.get("installed")]) >= 5:
                    return proc, url
        except Exception:
            pass
        time.sleep(0.5)
    raise RuntimeError("server did not become ready in time")


@pytest.fixture(scope="module")
def base_url():
    port = _free_port()
    for stale in (_cache("state.json"), _cache("pairing.json")):
        try:  # a previous run's samples must not make this one pass
            os.unlink(stale)
        except OSError:
            pass
    # BOTH caches must be redirected: the daemon appends an energy sample per poll, so without
    # this the suite writes mock data into the developer's real ~/.cache. Same for the pairing
    # cache -- it's only read as a `pairing_snapshot()` fallback before any wizard run, but a
    # real cached address there would falsely suppress the "prominent setup card" case.
    proc, url = _start_daemon(
        port,
        {
            "CALICTL_STATE_CACHE": _cache("state.json"),
            "CALICTL_PAIRING_CACHE": _cache("pairing.json"),
        },
    )
    try:
        yield url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


@pytest.fixture
def page(base_url, error_gated_page):
    """A page whose uncaught JS errors FAIL the test that produced them.

    The web UI is un-built, client-side JS: a ReferenceError in a renderer only shows up when that
    screen is actually rendered in a browser. One shipped to buspi (#174: `roofControls()` used an
    undeclared name; the Roof screen went blank) because no test rendered that screen and nothing
    else could see it. Now EVERY e2e test doubles as a runtime-error detector: `pageerror`
    (uncaught exceptions) and console `error`s are collected and asserted empty at teardown.
    """
    with error_gated_page(base_url) as pg:
        yield pg


@pytest.fixture
def pairing_url(tmp_path):
    """A dedicated daemon per pairing test (own port + caches): the guided-pairing wizard is a
    server-side state machine (`calictl.pairing`), so sharing the module-scoped `base_url` server
    across pairing tests would let one test's end state (e.g. bonded) leak into the next."""
    port = _free_port()
    proc, url = _start_daemon(
        port,
        {
            "CALICTL_STATE_CACHE": str(tmp_path / "state.json"),
            "CALICTL_PAIRING_CACHE": str(tmp_path / "pairing.json"),
        },
    )
    try:
        yield url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


@pytest.fixture
def pairing_page(pairing_url):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        pg = browser.new_page()
        pg.goto(pairing_url)
        yield pg
        browser.close()


@pytest.fixture
def unconfigured_pairing_page(tmp_path):
    """A daemon with NO configured bond: `CALICTL_ADDR` blanked and no pairing cache, so the
    idle `pairing_snapshot()` reports `address: null`. This is the true first-run/unbonded
    scenario — the one where the menu's Unpair entry must stay hidden until a wizard bond lands
    (unlike the default e2e daemon, which always carries a mock `CALICTL_ADDR`)."""
    port = _free_port()
    proc, url = _start_daemon(
        port,
        {
            "CALICTL_ADDR": "",
            "CALICTL_STATE_CACHE": str(tmp_path / "state.json"),
            "CALICTL_PAIRING_CACHE": str(tmp_path / "pairing.json"),
        },
        expect_installed=False,
    )
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            pg = browser.new_page()
            pg.goto(url)
            yield pg
            browser.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


def test_dashboard_shows_installed_tiles_and_hides_uninstalled(page):
    # installed features render as tiles (the mock seeds every function the real van has fitted).
    for name in ("Cooler", "Camping mode", "Water", "Energy"):
        expect(page.get_by_text(name, exact=True).first).to_be_visible()
    # the mock now seeds a fitted pop-top (like the real van), so Roof is a tile too
    expect(page.get_by_text("Roof", exact=True).first).to_be_visible()


def test_pairing_chrome_hidden_when_paired_and_online(page):
    """UX counterpart to `test_pairing_hidden_until_opened_from_menu`: a PAIRED + ONLINE daemon
    (the `page`/`base_url` fixture's `CALICTL_ADDR=MO:CK:CA:MP:ER:00`) must show NO pairing chrome
    in the main flow -- neither the guided-pairing card (opens only from the ⋮ menu, or the
    unpaired banner, never automatically) nor that banner itself (`_meta.paired` is True here).
    Asserts on what the user sees, not the JS predicate's internals (`_meta.online`/`_meta.paired`)."""
    # let the dashboard finish rendering its tiles before checking for absent chrome
    expect(page.get_by_text("Cooler", exact=True).first).to_be_visible()
    expect(page.get_by_text("Set up remote control")).to_have_count(0)
    expect(page.get_by_role("button", name="Connect now")).to_have_count(0)
    expect(page.locator("#unpaired-banner")).to_have_count(0)
    page.get_by_role("button", name="Menu").click()
    expect(page.get_by_role("button", name="Bluetooth pairing…")).to_be_visible()


def test_water_screen_shows_level_percent(page):
    page.get_by_text("Water", exact=True).first.click()
    expect(page.get_by_text("Fresh water")).to_be_visible()
    assert "%" in page.locator("#app").inner_text()  # a real level readout, not a spec dump


def test_cooler_toggle_gives_feedback_then_applies(page):
    page.get_by_text("Cooler", exact=True).first.click()
    sw = page.locator(".switch").first
    expect(sw).to_have_attribute("aria-checked", "false")  # off at start
    sw.click()
    expect(page.locator("#status")).to_have_text("Sending…")  # immediate in-flight feedback
    expect(page.get_by_text("Applied")).to_be_visible(timeout=15000)  # completion toast
    expect(page.locator('.switch[aria-checked="true"]').first).to_be_visible()  # flipped on


def test_camping_lights_and_usb_greyed_until_master_on(page):
    # The app only lets you toggle interior/outside lights + rear USB while camping master is ON
    # (a UI gate, semantics.campingmode / tf/a.java). With master OFF, both dependent toggles must
    # render disabled + their whole row greyed (.ctl-off), while the master toggle stays enabled.
    page.get_by_text("Camping mode", exact=True).first.click()
    master = page.get_by_role("switch", name="Camping mode")
    # order-independent: the module-scoped mock is shared, so a prior test may have left master ON.
    # Drive it OFF first so this test asserts the master-off gate regardless of run order.
    if master.get_attribute("aria-checked") == "true":
        master.click()
        expect(page.get_by_text("Applied")).to_be_visible(timeout=15000)
        expect(master).to_have_attribute("aria-checked", "false")
    expect(page.get_by_role("switch", name="Exterior and interior lighting")).to_be_disabled()
    expect(page.get_by_role("switch", name="Rear USB ports")).to_be_disabled()
    expect(page.locator(".row.ctl-off")).to_have_count(2)  # exactly lights + usb greyed
    expect(master).to_be_enabled()  # master itself is usable


def test_cooler_quiet_and_timer_controls_follow_power(page):
    # The app's own gates: quiet mode is only settable while the refrigerator box is ON; the
    # cooling timer only while it is OFF. Both rows grey (.ctl-off) with the reason as tooltip.
    page.get_by_text("Cooler", exact=True).first.click()
    power = page.get_by_role("switch", name="Refrigerator box")
    quiet = page.locator("select").first  # the Quiet mode <select>
    start_timer = page.get_by_role("button", name="Start timer")
    was_on = power.get_attribute("aria-checked") == "true"

    def set_power(on):
        if (power.get_attribute("aria-checked") == "true") != on:
            power.click()
            expect(page.get_by_text("Applied")).to_be_visible(timeout=15000)
            expect(power).to_have_attribute("aria-checked", "true" if on else "false")

    set_power(False)
    expect(quiet).to_be_disabled()
    expect(start_timer).to_be_enabled()
    set_power(True)
    expect(quiet).to_be_enabled()
    expect(start_timer).to_be_disabled()
    expect(power).to_be_enabled()  # the power switch itself is never gated
    set_power(was_on)  # leave the shared mock as we found it


def test_heater_continuous_switch_off_only(page):
    # Continuous heating ("Dauerbetrieb") can only be STARTED from inside the vehicle — the only
    # remote write is OFF. With it off (mock: PermanentOperation=0) the switch must render greyed
    # with the reason, while Immediate heating stays usable.
    page.get_by_text("Air heater", exact=True).first.click()
    cont = page.get_by_role("switch", name="Continuous heating")
    expect(cont).to_be_visible()
    expect(cont).to_have_attribute("aria-checked", "false")
    expect(cont).to_be_disabled()
    expect(page.get_by_role("switch", name="Immediate heating")).to_be_enabled()
    assert page.locator(".row.ctl-off", has=cont).count() == 1


def test_heater_timer_buttons_arm_and_stop(page):
    # The heater page's departure timer is armed by OperationModeAirHeater=3 (the app's "Start
    # timer", 3f3b017f1f3f) and cleared by Mode=0 ("Stop") — app-observed 2026-09-16. Both buttons
    # render; arming goes through the fuel-burner confirm and lands as "Applied" against the mock.
    page.get_by_text("Air heater", exact=True).first.click()
    page.on("dialog", lambda d: d.accept())
    start, stop = (
        page.get_by_role("button", name="Start timer"),
        page.get_by_role("button", name="Stop timer"),
    )
    expect(start).to_be_visible()
    expect(stop).to_be_visible()
    start.click()
    expect(page.get_by_text("Applied")).to_be_visible(timeout=15000)
    # readout "Timer" shows the armed start time (mock seed 00:00) only while Mode is 3 …
    expect(page.get_by_text("00:00", exact=True)).to_be_visible(timeout=15000)
    stop.click()
    expect(page.get_by_text("Applied")).to_be_visible(timeout=15000)
    # … and falls back to "off" once the readback shows Mode 0 again.
    expect(page.get_by_text("00:00", exact=True)).to_have_count(0, timeout=15000)


def test_camping_master_toggle_applies(page):
    page.get_by_text("Camping mode", exact=True).first.click()
    sw = page.locator(".switch").first  # first control = master
    sw.click()
    expect(page.get_by_text("Applied")).to_be_visible(timeout=15000)
    expect(page.locator('.switch[aria-checked="true"]').first).to_be_visible()


def test_roof_screen_renders_move_buttons(page):
    # Regression guard for #174: roofControls() referenced an undeclared `btns` and threw a
    # ReferenceError on every Roof render — shipped to buspi because the mock had no pop-top, so
    # CI never executed the function. With a fitted roof seeded (Position closed, no InfoPopUp
    # alert) the screen must render all three controls, and with no alert none may be blocked.
    page.get_by_text("Roof", exact=True).first.click()
    for name in ("open", "close", "stop"):
        expect(page.get_by_role("button", name=name)).to_be_visible()
        expect(page.get_by_role("button", name=name)).to_be_enabled()
    expect(page.get_by_text("Alert")).to_be_visible()  # readouts rendered too


def test_every_tile_renders_without_js_errors(page):
    # The structural guard for the class of bug that shipped in #174: open EVERY dashboard tile so
    # every feature renderer actually executes in a browser. Any uncaught exception fails the test
    # via the `page` fixture's pageerror collector — no screen can silently blank out again.
    for name in ("Cooler", "Camping mode", "Lighting", "Air heater", "Water", "Energy", "Roof", "Vehicle"):
        page.get_by_text(name, exact=True).first.click()
        expect(page.locator("#title")).to_have_text(name)
        assert page.locator("#app").inner_text().strip(), "%s screen rendered empty" % name
        # JS click on the header's #back: deterministic (no pointer actionability / overlay
        # races) — this test is about renderers throwing, not about hitting the back arrow.
        page.evaluate("document.getElementById('back').click()")
    expect(page.get_by_text("Cooler", exact=True).first).to_be_visible()  # back on the dashboard


def test_roof_hold_release_sends_stop(page):
    # The hold-to-move contract: pressing Open streams the move, RELEASING must send STOP. Before the
    # fix, command() re-rendered synchronously, detaching the held button, so its closure-scoped
    # pointerup never fired and no STOP went out (the roof kept moving until the server's Position
    # auto-stop). The release is now caught at document level and the screen is not rebuilt while a
    # move is held. Assert the actual /api/command traffic: a roof "open" on press, "stop" on release.
    page.on("dialog", lambda d: d.accept())  # the "path clear?" confirm
    page.get_by_text("Roof", exact=True).first.click()
    open_btn = page.get_by_role("button", name="open")
    expect(open_btn).to_be_enabled()

    def is_cmd(req, what):
        return (
            "/api/command" in req.url
            and req.method == "POST"
            and '"roof"' in (req.post_data or "")
            and ('"%s"' % what) in (req.post_data or "")
        )

    open_btn.hover()
    try:
        with page.expect_request(lambda r: is_cmd(r, "open"), timeout=10000):
            page.mouse.down()
        with page.expect_request(lambda r: is_cmd(r, "stop"), timeout=10000):
            page.mouse.up()
    finally:
        # Never leave the module-scoped mock daemon mid-move: a failed assertion above would
        # otherwise hold the serve lock and stall every later test's command ("Sending…" forever).
        page.mouse.up()
        origin = page.url.split("/", 3)[0] + "//" + page.url.split("/", 3)[2]
        page.request.post(
            origin + "/api/command", data={"function": "roof", "what": "stop", "value": None, "confirm": True}
        )


def test_roof_page_opens_and_leaves_the_roof_view(page):
    # The app's roof screen streams its SafetyCounter while open (CAPTURE 2026-10-10); the web UI
    # tells the daemon the same: opening the Roof page posts {"action":"view"}, leaving it "leave".
    def is_roof(req, action):
        return "/api/roof" in req.url and req.method == "POST" and ('"%s"' % action) in (req.post_data or "")

    with page.expect_request(lambda r: is_roof(r, "view"), timeout=10000):
        page.get_by_text("Roof", exact=True).first.click()
    with page.expect_request(lambda r: is_roof(r, "leave"), timeout=10000):
        page.evaluate("document.getElementById('back').click()")


def test_lighting_screen_lamps_are_directly_controllable(page):
    # like the app: lamps are always controllable (no "activate a profile first" gate). Dragging
    # a lamp from the lights-off state applies directly — the SET_BRIGHTNESS self-carries profile 9.
    page.get_by_text("Lighting", exact=True).first.click()
    expect(page.get_by_text("Reading lights", exact=True)).to_be_visible()
    expect(page.get_by_text("Left", exact=True)).to_be_visible()  # a reading lamp
    # no manual Activate step, and controls are live from the start
    assert page.get_by_role("button", name="Activate").count() == 0
    slider = page.locator("input[type=range]:not([aria-label='Wake-up brightness'])").first
    expect(slider).to_be_enabled()
    assert page.locator(".switch").first.is_enabled()  # all-lights master too
    slider.fill("8")  # drag a lamp
    slider.dispatch_event("change")
    # applied-ness comes from the unit's 1502 notification carrying the real level, never from the
    # echo readback; the mock notifies at once, like the real unit (CAPTURE 2026-10-10, ~230 ms)
    expect(page.get_by_text("✓ Applied").first).to_be_visible(timeout=15000)


def test_dashboard_summary_card(page):
    # the "California Status" overview: fresh/waste water + second battery, above the tiles.
    card = page.locator(".card.summary")
    expect(card).to_be_visible()
    for label in ("Fresh water", "Waste water", "Second battery"):
        expect(card.get_by_text(label, exact=True)).to_be_visible()
    # a real "<liters> / <capacity> l" readout, not a spec/ghost row
    import re

    assert re.search(r"\d+ / \d+ l", card.inner_text())


def test_no_red_flag_text(page, base_url):
    # Auto-catches the UX-bug classes we hit by hand: raw compose-key ids, undefined/null/NaN,
    # nonsense dates, [object Object]. Scans every installed screen + the dashboard.
    RED = (
        "_text",
        "_toggle",
        "_widget",
        "_section",
        "_drawer",
        "undefined",
        "null",
        "NaN",
        "1900-",
        "[object Object]",
    )
    for name in ("Cooler", "Camping mode", "Lighting", "Air heater", "Water", "Energy", "Vehicle"):
        page.goto(base_url)
        page.locator(".tile", has_text=name).first.click()
        page.wait_for_timeout(500)
        text = page.locator("#app").inner_text()
        for flag in RED:
            assert flag not in text, "%r rendered on the %s screen" % (flag, name)
    page.goto(base_url)  # dashboard
    dtext = page.locator("#app").inner_text()
    for flag in RED:
        assert flag not in dtext, "%r rendered on the dashboard" % flag


def test_session_pill_shows_live(page, base_url):
    """With the persistent session up, the header shows a 'Live' session pill."""
    page.goto(base_url)
    page.wait_for_selector(".session-pill", timeout=20000)
    txt = page.locator(".session-pill").inner_text()
    assert "Live" in txt


def test_command_latency_is_subsecond(page, base_url):
    """The persistent session's payoff: a control write applies in well under a second
    (the e2e daemon runs with real ARM_DELAY defaults; only a live session makes this pass)."""
    import time

    page.goto(base_url)
    page.get_by_text("Cooler", exact=True).first.click()
    sw = page.locator(".switch").first
    sw.wait_for(state="visible", timeout=20000)
    t0 = time.time()
    sw.click()
    page.get_by_text("Applied").wait_for(timeout=10000)
    assert time.time() - t0 < 3.0, "command took too long -- persistent fast path not engaged"


def test_open_control_survives_a_state_poll(page):
    """Regression: a live state poll must NOT re-render and destroy a control the user is
    interacting with. The 2s poll rebuilds #app (app.innerHTML=""), which used to slam an open
    <select> (Profile / Save current as) shut mid-selection. refreshState() skips the
    repaint while a <select>/time/range control is focused. We mark the focused <select>, wait
    through 2+ poll cycles (the mock's telemetry changes each poll, so a repaint WOULD otherwise
    fire), and assert the SAME element is still there — a repaint would have replaced it, losing
    the marker (and, in a browser, closing the dropdown)."""
    page.get_by_text("Lighting", exact=True).first.click()
    sel = page.locator("select").first  # the 'Profile' dropdown
    expect(sel).to_be_visible()
    sel.evaluate("el => { el.dataset.probe = 'keep'; el.focus(); }")
    assert page.evaluate("document.activeElement && document.activeElement.tagName") == "SELECT"
    page.wait_for_timeout(5000)  # span 2+ poll cycles
    # if refreshState re-rendered, the marked <select> was replaced -> marker gone / focus lost
    assert sel.get_attribute("data-probe") == "keep", "the open <select> was destroyed by a state poll"
    assert page.evaluate("document.activeElement && document.activeElement.tagName") == "SELECT"


def _open_pairing_via_menu(page):
    """The wizard lives behind the topbar context menu — no card in the main flow when the
    daemon is paired/online (UX: the guided flow is only prominent on true first-run)."""
    page.get_by_role("button", name="Menu").click()
    page.get_by_role("button", name="Bluetooth pairing…").click()


def _open_and_start_pairing(page):
    _open_pairing_via_menu(page)
    page.get_by_role("checkbox", name="I'm on that screen").check()
    page.get_by_role("button", name="Connect now").click()


def _complete_bonding(page):
    """Drive the wizard through to `bonded` against `tools.mock_unit.FakePairingTransport`."""
    _open_and_start_pairing(page)
    passkey = page.locator("#pairing-passkey")
    expect(passkey).to_be_visible(timeout=10000)
    passkey.fill(str(FakePairingTransport.RIGHT_PASSKEY))
    page.get_by_role("button", name="Send").click()
    expect(page.get_by_text("CALICTL_ADDR=" + FakePairingTransport.FOUND_ADDR)).to_be_visible(timeout=10000)


def test_pairing_wizard_reaches_bonded(pairing_page):
    """The full happy path: Setup card -> checkbox -> Start -> passkey -> bonded, showing the
    address and the env line to persist it (design spec step 2)."""
    _complete_bonding(pairing_page)
    assert FakePairingTransport.FOUND_ADDR in pairing_page.locator("#app").inner_text()


def test_pairing_checklist_before_connect(pairing_page):
    page = pairing_page
    page.get_by_role("button", name="Menu").click()
    page.get_by_text("Bluetooth pairing…").click()
    text = page.locator("#app").inner_text()
    assert "Gerät verbinden" in text  # the unit's own screen name
    assert "Passcode: ---" in text  # what the unit shows before we connect
    assert "phone" in text.lower()  # disconnect the CaliforniaOnTour app
    assert "Home Assistant" in text  # other scanners on the Pi


def test_a_failed_pairing_request_toasts_translated_text_not_an_enum(tmp_path):
    """A POST /api/pairing that returns an error body (e.g. HTTP 500 {"error":"pairing_failed"}
    when the daemon bridge times out) must toast translated text, never the raw enum."""
    port = _free_port()
    proc, url = _start_daemon(
        port,
        {
            "CALICTL_FAKE_PAIRING": "connect_failed",
            "CALICTL_PAIRING_CACHE": str(tmp_path / "pairing.json"),
            "CALICTL_STATE_CACHE": str(tmp_path / "state.json"),
        },
    )
    try:
        with sync_playwright() as p:
            page = p.chromium.launch().new_page()

            def _fail_post(route):
                if route.request.method == "POST":
                    route.fulfill(
                        status=500, content_type="application/json", body='{"error": "pairing_failed"}'
                    )
                else:
                    route.continue_()

            page.route("**/api/pairing", _fail_post)
            page.goto(url)
            _open_and_start_pairing(page)
            toast = page.locator("#toasts .toast")
            expect(toast).to_contain_text("did not answer the pairing request", timeout=10000)
            assert "pairing_failed" not in toast.inner_text()
    finally:
        proc.terminate()


def test_connect_failed_shows_its_own_guidance(tmp_path):
    port = _free_port()
    proc, url = _start_daemon(
        port,
        {
            "CALICTL_FAKE_PAIRING": "connect_failed",
            "CALICTL_PAIRING_CACHE": str(tmp_path / "pairing.json"),
            "CALICTL_STATE_CACHE": str(tmp_path / "state.json"),
        },
    )
    try:
        with sync_playwright() as p:
            page = p.chromium.launch().new_page()
            page.goto(url)
            _open_and_start_pairing(page)
            expect(page.get_by_role("button", name="Try again")).to_be_visible(timeout=20000)
            text = page.locator("#app").inner_text()
            assert "connect_failed" not in text
            assert "may still hold its single connection" in text  # the phone-slot hint
            assert "may be asleep" in text  # the asleep-unit hint (bond kept)
    finally:
        proc.terminate()


def test_radio_busy_banner(tmp_path):
    port = _free_port()
    proc, url = _start_daemon(
        port,
        {
            "CALICTL_FAKE_PAIRING": "radio_busy",
            "CALICTL_PAIRING_CACHE": str(tmp_path / "pairing.json"),
            "CALICTL_STATE_CACHE": str(tmp_path / "state.json"),
        },
    )
    try:
        with sync_playwright() as p:
            page = p.chromium.launch().new_page()
            page.goto(url)
            _open_and_start_pairing(page)
            expect(page.locator("#pairing-radio-busy")).to_be_visible(timeout=10000)
    finally:
        proc.terminate()


def test_unpaired_daemon_offers_setup(unconfigured_pairing_page):
    page = unconfigured_pairing_page
    expect(page.locator("#unpaired-banner")).to_be_visible(timeout=10000)
    page.locator("#unpaired-banner").get_by_role("button").click()
    expect(page.get_by_text("Gerät verbinden")).to_be_visible()


def test_pairing_wizard_restarts_cleanly_after_daemon_restart(tmp_path):
    env = {
        "CALICTL_PAIRING_CACHE": str(tmp_path / "pairing.json"),
        "CALICTL_STATE_CACHE": str(tmp_path / "state.json"),
    }
    port = _free_port()
    proc, url = _start_daemon(port, env)
    with sync_playwright() as p:
        page = p.chromium.launch().new_page()
        page.goto(url)
        _open_and_start_pairing(page)
        expect(page.locator("#pairing-passkey")).to_be_visible(timeout=10000)
        proc.terminate()
        proc.wait(timeout=5)
        proc, url = _start_daemon(port, env)  # same port: the tab reconnects
        try:
            page.reload()
            page.get_by_role("button", name="Menu").click()
            page.get_by_text("Bluetooth pairing…").click()
            expect(page.get_by_role("button", name="Connect now")).to_be_visible()
            page.locator("#pairing-ready").check()
            page.get_by_role("button", name="Connect now").click()
            expect(page.locator("#pairing-passkey")).to_be_visible(timeout=10000)
        finally:
            proc.terminate()


def test_pairing_wizard_never_flashes_a_stale_step_after_daemon_restart(tmp_path):
    """The no-reload counterpart to `test_pairing_wizard_restarts_cleanly_after_daemon_restart`:
    close the wizard mid-flow (still `waiting_passkey` in THIS process), restart the daemon on the
    same port (a fresh process starts `idle`), then reopen the wizard from the ⋮ menu WITHOUT
    reloading the tab. The client's cached `PAIRING` object is still the stale `waiting_passkey`
    snapshot from before the restart -- `openPairingWizard`'s synchronous render (added so the
    wizard opens instantly from the idle case) must not flash that stale passkey step; it must
    show a neutral loading step until the fresh `pairingFetch()` proves the real (idle) state."""
    env = {
        "CALICTL_PAIRING_CACHE": str(tmp_path / "pairing.json"),
        "CALICTL_STATE_CACHE": str(tmp_path / "state.json"),
    }
    port = _free_port()
    proc, url = _start_daemon(port, env)
    with sync_playwright() as p:
        page = p.chromium.launch().new_page()
        page.goto(url)
        _open_and_start_pairing(page)
        expect(page.locator("#pairing-passkey")).to_be_visible(timeout=10000)
        page.get_by_role(
            "button", name="Close", exact=True
        ).click()  # close mid-flow -- server stays waiting_passkey
        proc.terminate()
        proc.wait(timeout=5)
        proc, url = _start_daemon(port, env)  # same port, a FRESH process -> idle
        try:
            page.get_by_role("button", name="Menu").click()
            page.get_by_text("Bluetooth pairing…").click()
            # immediately after the click -- before the fresh fetch can possibly have landed --
            # the stale waiting_passkey step must never appear.
            expect(page.locator("#pairing-passkey")).to_have_count(0)
            # once the real state lands, the idle checklist renders (never the stale passkey step).
            expect(page.get_by_role("checkbox", name="I'm on that screen")).to_be_visible(timeout=10000)
            expect(page.locator("#pairing-passkey")).to_have_count(0)
        finally:
            proc.terminate()


def test_pairing_wizard_wrong_passkey_ends_in_error_with_retry(pairing_page):
    """A wrong passkey each attempt: the real SM (`calictl.pairing`) retries up to MAX_ATTEMPTS
    times (cycling back through scanning/connecting/waiting_passkey) before giving up -> `error`
    with a Try-again button (design spec step 2's error case)."""
    page = pairing_page
    _open_and_start_pairing(page)
    passkey = page.locator("#pairing-passkey")
    for _ in range(3):  # calictl.pairing.MAX_ATTEMPTS
        expect(passkey).to_be_visible(timeout=10000)
        passkey.fill("000000")
        page.get_by_role("button", name="Send").click()
        # Wait for THIS attempt to actually register server-side (the passkey input disappears --
        # to scanning/connecting on a retry, or straight to error) before sending the next one.
        # Without this, a fast double-click can hit the same stale "waiting_passkey" input/button
        # twice before the first POST's response re-renders, and the SM silently no-ops a
        # "passkey" event that doesn't arrive in `waiting_passkey` -- undercounting attempts.
        expect(passkey).to_be_hidden(timeout=10000)
    expect(page.get_by_role("button", name="Try again")).to_be_visible(timeout=10000)
    assert "pairing_failed" not in page.locator("#app").inner_text()  # friendly text, not the raw enum
    assert "Pairing was refused" in page.locator("#app").inner_text()


def test_pairing_hidden_until_opened_from_menu(unconfigured_pairing_page):
    """UX: a genuinely unpaired daemon (no `CALICTL_ADDR`, no pairing cache ->
    `device.UNPAIRED_ADDR`) is the true first-run case -- since Task 5's `_session` guard now
    refuses before any BLE traffic, `_meta.online` genuinely stays False here (before Task 5 the
    mock's address-agnostic `BleakClient` masked this: it happily "connected" even to the
    unpaired placeholder address, so this same daemon used to read as online).

    Task 11 replaced app.js's old `main()` rule that auto-OPENED the guided-pairing card itself on
    this exact case (`_meta.online === false && PAIRING.address == null`) with a persistent
    `#unpaired-banner` on the dashboard (`_meta.paired === false && !pairingOpen`,
    see `test_unpaired_daemon_offers_setup`): a working install never had pairing chrome forced
    open, and now neither does a fresh one -- the banner is an entry point alongside the ⋮ menu,
    not a full-card takeover. This test now asserts the banner (not the old auto-opened card) plus
    the menu entry point, and that Unpair stays hidden until a bond exists."""
    page = unconfigured_pairing_page
    expect(page.get_by_role("button", name="Menu")).to_be_visible()
    expect(page.locator("#unpaired-banner")).to_be_visible()
    page.get_by_role("button", name="Menu").click()
    expect(page.get_by_role("button", name="Bluetooth pairing…")).to_be_visible()
    # no bond configured in this daemon -> no Unpair entry
    expect(page.get_by_role("button", name="Unpair…")).to_have_count(0)


def test_menu_a11y_escape_closes_and_lang_syncs(page):
    """a11y: the ⋮ menu tracks aria-expanded and closes on Escape; switching language keeps
    <html lang> in sync for screen readers."""
    menu = page.get_by_role("button", name="Menu")
    expect(menu).to_have_attribute("aria-expanded", "false")
    menu.click()
    expect(menu).to_have_attribute("aria-expanded", "true")
    page.keyboard.press("Escape")
    expect(page.locator(".menupop")).to_have_count(0)
    expect(menu).to_have_attribute("aria-expanded", "false")
    # language toggle keeps <html lang> honest
    assert page.evaluate("document.documentElement.lang") == "en"
    menu.click()
    page.get_by_role("button", name="Deutsch").click()
    assert page.evaluate("document.documentElement.lang") == "de"


def test_menu_unpair_workflow_after_bonding(pairing_page):
    """The explicit unpair workflow: after bonding, the context menu gains 'Unpair…' which
    confirm()s, removes the bond (reset), and leaves the wizard open guiding a re-pair."""
    page = pairing_page
    page.on("dialog", lambda d: d.accept())
    _complete_bonding(page)
    page.get_by_role("button", name="Menu").click()
    unpair = page.get_by_role("button", name="Unpair…")
    expect(unpair).to_be_visible()
    unpair.click()
    # reset drives the SM resetting -> idle; the wizard stays open showing the guided first step
    expect(page.get_by_role("checkbox", name="I'm on that screen")).to_be_visible(timeout=10000)


def test_pairing_wizard_reset_demands_confirm_then_idle(pairing_page):
    """The reset/re-pair button gates on a native confirm() (design spec step 3); accepting it
    removes the bond and returns the wizard to `idle`."""
    page = pairing_page
    page.on("dialog", lambda d: d.accept())  # Playwright auto-dismisses confirm() with no listener
    _complete_bonding(page)
    page.get_by_role("button", name="Bluetooth reset / re-pair").click()
    expect(page.get_by_role("checkbox", name="I'm on that screen")).to_be_visible(timeout=10000)


def test_auto_camper_toggle_present_and_flips(page):
    """The Auto-camper toggle renders on the Camping card and flips (a persisted daemon setting,
    not a BLE write — so it works over the mock without actuation)."""
    page.get_by_text("Camping mode", exact=True).first.click()
    expect(page.get_by_text("Restore camping after you park")).to_be_visible()
    # the last .switch on the camping screen is the auto-camper toggle (after master/lights/usb)
    sw = page.locator('button[aria-label="Auto camper mode"]')
    expect(sw).to_have_attribute("aria-checked", "false")
    sw.click()
    expect(sw).to_have_attribute("aria-checked", "true", timeout=8000)


def test_language_toggle_to_german(page):
    """The ⋮ menu language toggle switches the served UI to German and persists the choice across
    a reload (localStorage); toggling back restores English."""
    expect(page.get_by_text("Cooler", exact=True).first).to_be_visible()
    page.get_by_role("button", name="Menu").click()
    page.get_by_role("button", name="Deutsch").click()
    expect(page.get_by_text("Kühlbox", exact=True).first).to_be_visible()
    assert page.get_by_text("Cooler", exact=True).count() == 0
    # persists across a reload
    page.reload()
    expect(page.get_by_text("Kühlbox", exact=True).first).to_be_visible()
    # toggle back to English
    page.get_by_role("button", name="Menu").click()
    page.get_by_role("button", name="English").click()
    expect(page.get_by_text("Cooler", exact=True).first).to_be_visible()
    assert page.get_by_text("Kühlbox", exact=True).count() == 0


def test_calictl_is_never_satellite(page):
    # The same app.js runs on the ESP32 satellite; on calictl the satellite gates must stay off and
    # semantics.js must still load (index.html serves it; unused here).
    assert page.evaluate("() => typeof adaptSatellite") == "function"
    assert page.evaluate("() => !!(STATE._meta && STATE._meta.satellite)") is False
    page.click("#menu")
    expect(page.locator(".menupop").get_by_text("Bluetooth pairing…")).to_be_visible()


def test_menu_device_status_screen_on_calictl(page):
    """The Device-status screen on the calictl flavor: daemon/session rows, no satellite rows and
    no setup link (the /device page exists only on the ESP firmware)."""
    page.click("#menu")
    page.locator(".menupop").get_by_text("Device status").click()
    expect(page.locator("#title")).to_have_text("Device status")
    expect(page.get_by_text("Camper unit", exact=True)).to_be_visible()
    expect(page.get_by_text("Last update", exact=True)).to_be_visible()
    assert page.get_by_text("Open setup").count() == 0
    assert page.get_by_text("Satellite firmware").count() == 0


def test_unknown_runtime_is_restrictive(base_url):
    # The ESP32 satellite runs this same app.js. Until a `_meta` answers we cannot tell it from calictl, so
    # the page must stay restrictive: controls read-only, no pairing menu, and no request beyond
    # "/" + static assets + /api/state (never /api/pairing|command|session|...).
    seen = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        pg = browser.new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.on("request", lambda r: seen.append(r.url.replace(base_url, "")))
        pg.route(
            "**/api/state",
            lambda route: route.fulfill(status=200, content_type="text/plain", body="not json"),
        )
        pg.goto(base_url)
        pg.wait_for_function("() => typeof STATE !== 'undefined' && document.querySelector('#app *')")
        assert pg.evaluate("() => !STATE._meta && readOnly()") is True
        pg.click("#menu")
        menu = pg.locator(".menupop")
        expect(menu).to_be_visible()
        assert menu.get_by_text("Bluetooth pairing…").count() == 0
        assert menu.get_by_text("Unpair…").count() == 0
        pg.wait_for_timeout(2500)  # past one refresh tick: still nothing fetched but /api/state
        browser.close()
    assert not errs, errs
    api = [u for u in seen if u.startswith("/api/")]
    assert set(api) == {"/api/state"}, api


def test_a_failed_first_poll_still_loads_pairing_once(base_url):
    # Deploy restart on buspi: the page's first /api/state fails (503 {error}, no `_meta`). The
    # one-off /api/pairing fetch must still happen once a calictl `_meta` answers on a later poll,
    # or "Unpair…" (and the setup card's prominence) is lost for the page's whole life.
    seen = []
    fails = [1]
    with sync_playwright() as p:
        browser = p.chromium.launch()
        pg = browser.new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.on("request", lambda r: seen.append(r.url.replace(base_url, "")))

        def _first_fails(route):
            if fails[0]:
                fails[0] -= 1
                route.fulfill(status=503, content_type="application/json", body='{"error": "state_failed"}')
            else:
                route.continue_()

        pg.route("**/api/state", _first_fails)
        pg.goto(base_url)
        pg.wait_for_function("() => typeof STATE !== 'undefined' && !!STATE._meta", timeout=10000)
        pg.click("#menu")
        expect(pg.locator(".menupop").get_by_text("Unpair…")).to_be_visible()
        pg.wait_for_timeout(2500)  # more polls: still fetched exactly once
        browser.close()
    assert not errs, errs
    assert seen.count("/api/pairing") == 1, seen


def _state(base_url, page):
    return page.request.get(base_url + "/api/state").json()


def _poll(page, base_url, pred, what, timeout=15.0):
    """Poll the daemon's /api/state until ``pred(state)`` holds — the UNIT path (the daemon's latch
    of the mock's 1502 frames), never the UI's optimistic overlay. (page.wait_for_function with an
    ``async`` predicate returns a truthy Promise and never waits.)"""
    import time

    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            if pred(_state(base_url, page)):
                return
        except (KeyError, TypeError):
            pass
        time.sleep(0.1)
    raise AssertionError("state never reached: " + what)


def test_wakeup_light_time_and_switch_reach_the_unit(page, base_url):
    """
    .. test:: The web UI edits the wake-up time and switch; an edit keeps the unit-reported switch
       :id: T_E2E_LIGHT_WAKEUP
       :links: R_LIGHT_WAKEUP
    """
    # seed the unit-reported config (the card is disabled until the unit has reported one)
    r = page.request.post(
        base_url + "/api/command", data={"function": "lighting", "what": "wakeup", "value": "06:00 1 0 0 on"}
    )
    assert r.ok
    _poll(page, base_url, lambda st: st["lighting"]["wakeup"]["time"] == "06:00", "seeded wake-up 06:00")
    page.get_by_text("Lighting", exact=True).first.click()
    tm = page.get_by_label("Wake-up time")
    sw = page.get_by_role("switch", name="Wake-up light")
    expect(tm).to_have_value("06:00", timeout=15000)
    expect(sw).to_have_attribute("aria-checked", "true")
    bodies = []
    page.on("request", lambda rq: bodies.append(rq.post_data) if rq.url.endswith("/api/command") else None)
    tm.fill("07:00")  # one change, like a user; the field must keep it through re-renders
    expect(tm).to_have_value("07:00")
    _poll(
        page,
        base_url,
        lambda st: (
            st["lighting"]["wakeup"]["time"] == "07:00" and st["lighting"]["wakeup"]["enabled"] is True
        ),
        "unit-reported wake-up 07:00, still enabled (the edit carried the reported switch)",
    )
    expect(tm).to_have_value("07:00")
    sw.click()
    _poll(
        page, base_url, lambda st: st["lighting"]["wakeup"]["enabled"] is False, "unit-reported wake-up off"
    )
    expect(sw).to_have_attribute("aria-checked", "false")
    page.wait_for_timeout(300)  # let the request events drain
    sent = [json.loads(b)["value"] for b in bodies]
    clocks = [json.loads(b)["local_now"] for b in bodies]
    assert all(isinstance(c, int) and c > 1767225600 for c in clocks)  # the page's clock, ignored by calictl
    # a time edit carries NO on/off (the daemon fills the unit-reported one); the switch's does
    assert sent[0].startswith("07:00") and sent[0].split()[-1] not in ("on", "off")
    assert sent[1].endswith(" off")


@pytest.fixture
def fresh_url(tmp_path):
    """A dedicated daemon over a fresh mock: the unit has never reported a wake-up config (the shared
    ``base_url`` daemon's mock is seeded by other tests)."""
    proc, url = _start_daemon(
        _free_port(),
        {
            "CALICTL_STATE_CACHE": str(tmp_path / "state.json"),
            "CALICTL_PAIRING_CACHE": str(tmp_path / "pairing.json"),
            "CALICTL_CONFIG_PULL_S": "0.5",
        },
    )
    try:
        yield url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


def test_wakeup_time_edit_with_no_config_pulls_then_shows_the_refusal(fresh_url, error_gated_page):
    """Review m2: with no config reported, the card does not dead-end — only the time is live, an edit
    sends the time alone; the daemon pulls REQUEST_CONFIG (R5), the mock answers with no wake-up frame,
    and the refusal reason reaches the user. Nothing is invented: the card stays unknown."""
    with error_gated_page(fresh_url) as pg:
        pg.get_by_text("Lighting", exact=True).first.click()
        tm = pg.get_by_label("Wake-up time")
        expect(tm).to_be_enabled(timeout=15000)
        expect(tm).to_have_value("")
        expect(pg.get_by_role("switch", name="Wake-up light")).to_be_disabled()
        expect(pg.get_by_label("Wake-up brightness")).to_be_disabled()
        bodies = []
        pg.on("request", lambda rq: bodies.append(rq.post_data) if rq.url.endswith("/api/command") else None)
        tm.fill("07:00")
        expect(pg.locator(".toast").last).to_have_text(
            "wake-up config not known yet (the unit has not reported it): give on|off with the edit",
            timeout=15000,
        )
        assert _state(fresh_url, pg)["lighting"]["wakeup"] is None
    body = json.loads(bodies[0])
    assert (body["function"], body["what"], body["value"]) == ("lighting", "wakeup", "07:00")
    assert isinstance(body["local_now"], int)


def test_door_contact_switch_round_trips(page, base_url):
    """
    .. test:: The web UI toggles the sliding-door light; the state shows the mock's flag
       :id: T_E2E_LIGHT_DOOR
       :links: R_LIGHT_DOOR_CONTACT
    """
    page.get_by_text("Lighting", exact=True).first.click()
    sw = page.get_by_role("switch", name="Sliding door lighting")
    was = sw.get_attribute("aria-checked") == "true"
    sw.click()
    # the unit-reported flag (the mock's 1502 echo, latched by the daemon), not the optimistic switch
    _poll(
        page,
        base_url,
        lambda st: st["lighting"]["door_contact"] is (not was),
        "unit-reported door flag flipped",
    )
    expect(sw).to_have_attribute("aria-checked", "false" if was else "true")
    sw.click()  # restore
    _poll(page, base_url, lambda st: st["lighting"]["door_contact"] is was, "door flag restored")


def test_all_lights_master_stays_on(page, base_url):
    """The owner saw the All-lights switch flip back off: the mock stored PN 12 but lit no lamp,
    so the next poll read every zone dark. The switch must stay on from the UNIT's state.

    .. test:: The All-lights master lights the lamps over the mock daemon and stays on
       :id: T_E2E_LIGHT_ALL_LIGHTS
    """
    page.request.post(
        base_url + "/api/command", data={"function": "lighting", "what": "power", "value": "off"}
    )
    _poll(page, base_url, lambda st: st["lighting"]["any_on"] is False, "all lights off first")
    page.get_by_text("Lighting", exact=True).first.click()
    sw = page.get_by_role("switch", name="All lights")
    expect(sw).to_have_attribute("aria-checked", "false")
    sw.click()
    _poll(
        page, base_url, lambda st: st["lighting"]["any_on"] is True and st["lighting"]["profile"] == 12, "lit"
    )
    expect(page.locator("#toasts").get_by_text("✓ Applied")).to_be_visible(timeout=15000)
    page.wait_for_timeout(2500)  # a couple of polls later: still on (the bug flipped it back)
    expect(sw).to_have_attribute("aria-checked", "true")
    assert _state(base_url, page)["lighting"]["any_on"] is True
    sw.click()
    _poll(page, base_url, lambda st: st["lighting"]["any_on"] is False, "all lights off again")


def test_favourite_save_then_activate(page, base_url):
    """
    .. test:: Save favourite A then activate it from the web UI against the mock
       :id: T_E2E_LIGHT_FAVOURITE
       :links: R_LIGHT_FAVOURITE
    """
    page.get_by_text("Lighting", exact=True).first.click()
    before = _state(base_url, page)["lighting"]["profile"]
    assert before != 1  # else activating favourite 1 could not prove anything
    toasts = page.locator("#toasts")
    page.once("dialog", lambda d: d.accept())
    page.locator("select").nth(1).select_option("1")  # "Save current as" -> Profile A (= favourite 1)
    expect(
        toasts.get_by_text("Sent — check the lamp").or_(toasts.get_by_text("✓ Applied")).first
    ).to_be_visible(timeout=15000)
    # activating proves the save LANDED on the unit: the mock ignores (ACK-only) an empty favourite
    page.locator("select").nth(0).select_option("1")
    _poll(page, base_url, lambda st: st["lighting"]["profile"] == 1, "favourite 1 active after activate")
    expect(page.get_by_text("this favourite is empty on the unit — save it first")).to_have_count(0)
    r = page.request.post(
        base_url + "/api/command", data={"function": "lighting", "what": "color", "value": "red"}
    )
    assert r.status == 400 and "retired" in r.json()["error"]


def test_favourite_save_is_not_an_activation(page, base_url):
    """#284: the unit's 1502 read returns its last frame (here the save ack, Mode 4 / PN 5); a poll
    must not take it for the active profile (CAPTURE 2026-10-10: buspi read back config echoes).

    .. test:: Saving a favourite does not change the served active profile
       :id: T_E2E_LIGHT_SAVE_NOT_ACTIVE
       :links: R_LIGHT_ACTIVE_PROFILE
    """
    page.get_by_text("Lighting", exact=True).first.click()
    before = _state(base_url, page)["lighting"]["profile"]
    page.once("dialog", lambda d: d.accept())
    page.locator("select").nth(1).select_option("5")  # "Save current as" -> Profile B (= favourite 5)
    toasts = page.locator("#toasts")
    expect(
        toasts.get_by_text("Sent — check the lamp").or_(toasts.get_by_text("✓ Applied")).first
    ).to_be_visible(timeout=15000)
    time.sleep(3)  # a poll after the save
    assert _state(base_url, page)["lighting"]["profile"] == before


def test_favourite_tiles_are_a_b_c_d_mapped_to_1_5_6_7(page):
    """
    .. test:: The profile selectors offer the app's four tiles A-D = favourites 1/5/6/7
       :id: T_E2E_LIGHT_TILES
       :links: R_LIGHT_FAVOURITE
    """
    page.get_by_text("Lighting", exact=True).first.click()
    for sel in (page.locator("select").nth(0), page.locator("select").nth(1)):
        opts = {
            o.get_attribute("value"): o.inner_text()
            for o in sel.locator("option").all()
            if o.get_attribute("value")
        }
        letters = {v: t.replace(" ✓", "") for v, t in opts.items() if v in ("1", "5", "6", "7")}
        assert letters == {"1": "Profile A", "5": "Profile B", "6": "Profile C", "7": "Profile D"}
        assert "2" not in opts and "3" not in opts and "4" not in opts


def test_wakeup_areas_use_the_t7_labels_and_ranges(page):
    """
    .. test:: Wake-up areas carry the app's T7 labels; brightness 0..10; lead time 0/10/20/30
       :id: T_E2E_LIGHT_WAKEUP_LABELS
       :links: R_LIGHT_WAKEUP
    """
    page.get_by_text("Lighting", exact=True).first.click()
    for label in (
        "Living area reading lights",
        "Kitchen background lighting",
        "Pop-up roof reading lights",
        "Pop-up roof background lighting",
    ):
        expect(page.get_by_text(label, exact=False).first).to_be_visible()
    bright = page.get_by_label("Wake-up brightness")
    assert (bright.get_attribute("min"), bright.get_attribute("max")) == ("0", "10")
    ramps = [o.get_attribute("value") for o in page.locator("select").nth(2).locator("option").all()]
    assert ramps == ["0", "10", "20", "30"]


@pytest.mark.parametrize("locale,tile", [("en-US", "Lighting"), ("de-DE", "Beleuchtung")])
def test_wakeup_areas_are_a_vertical_checkbox_list(base_url, error_gated_page, locale, tile):
    """Owner screenshot (DE, phone): each area checkbox sat ABOVE a two-line label, four floating
    columns. Now: one area per line, the checkbox left of its label on the same row, no wrap, no
    horizontal page scroll at phone width.

    .. test:: Wake-up areas render as a one-per-line checkbox list (EN + DE, phone width)
       :id: T_E2E_LIGHT_WAKEUP_AREA_LAYOUT
    """
    with error_gated_page(base_url, locale=locale, viewport={"width": 360, "height": 800}) as pg:
        pg.get_by_text(tile, exact=True).first.click()
        items = pg.locator(".arealist label")
        expect(items).to_have_count(4)
        tops = []
        for i in range(4):
            lab = items.nth(i).bounding_box()
            cb = items.nth(i).locator("input[type=checkbox]").bounding_box()
            assert lab and cb
            assert abs((cb["y"] + cb["height"] / 2) - (lab["y"] + lab["height"] / 2)) < 3  # same row
            assert cb["x"] <= lab["x"] + 1  # checkbox first (left)
            assert lab["height"] < 2 * cb["height"] + 6, lab  # one line of text, not wrapped
            tops.append(lab["y"])
        assert tops == sorted(tops) and len(set(tops)) == 4  # stacked vertically
        assert pg.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")


def test_door_contact_row_hidden_on_grand_california(page, base_url):
    """
    .. test:: The sliding-door row is shown on every variant except Grand California (2)
       :id: T_E2E_LIGHT_DOOR_GATE
       :links: R_LIGHT_DOOR_CONTACT
    """

    def patch(route):
        r = route.fetch()
        body = r.json()
        body.setdefault("vehicle", {})["car_variant"] = 2
        route.fulfill(response=r, json=body)

    page.route("**/api/state", patch)
    page.goto(base_url)
    page.get_by_text("Lighting", exact=True).first.click()
    expect(page.get_by_label("Wake-up time")).to_be_visible()
    assert page.get_by_role("switch", name="Sliding door lighting").count() == 0


def test_energy_card_hides_power_sources_the_van_does_not_have(page, base_url):
    """A source the unit reports as not installed (the mock's solar, like this van) gets no row
    at all — owner 2026-10-10: "Why do you show stuff that is not available and installed in the
    car" (was "Solar power — not installed"). Installed sources keep their row."""
    page.goto(base_url)
    page.locator(".tile", has_text="Energy").first.click()
    card = page.locator("#app")
    expect(card.get_by_text("Shore power", exact=True)).to_be_visible()
    expect(card.get_by_text("Vehicle power", exact=True)).to_be_visible()
    expect(card.get_by_text("Solar power", exact=True)).to_have_count(0)
    assert "not installed" not in card.inner_text()
