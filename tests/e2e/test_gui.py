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


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _start_daemon(port, extra_env):
    """Launch `tools.run_against_mock serve --web <port>` and block until `/api/state` reports
    the mock's installed functions -- the subprocess-launch + readiness-poll dance shared by
    `base_url` (one server for the whole module) and `pairing_url` (a fresh one per test, so the
    guided-pairing wizard's server-side state machine can't leak between tests)."""
    env = dict(os.environ,
               CALICTL_ADDR="MO:CK:CA:MP:ER:00", PYTHONUNBUFFERED="1",
               CALICTL_ARM_DELAY_S="0.3", CALICTL_SETTLE_S="0.3", CALICTL_HEARTBEAT_PERIOD_S="0.1",
               CALICTL_FAST_CONFIRM_S="0.2",  # lighting fast-path Mode-4 confirm window (real default 1.2 s)
               CALICTL_SESSION_WAIT_S="0.3",  # don't idle waiting for a session in the mock e2e
               CALICTL_HEARTBEAT_WARMUP_S="0",
               CALICTL_ENABLE_WRITES="1",  # e2e exercises control writes -> not read-only
               CALICTL_PERSISTENT_SESSION="1")  # default, explicit for the session-pill test's intent
    env.update(extra_env)
    proc = subprocess.Popen(
        [sys.executable, "-m", "tools.run_against_mock", "serve",
         "--web", str(port), "--interval", "1", "--no-influx"],
        cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    url = "http://127.0.0.1:%d" % port
    deadline = time.time() + 30
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError("server exited early:\n" + proc.stdout.read().decode())
        try:
            with urllib.request.urlopen(url + "/api/state", timeout=1) as r:
                if len([k for k, v in json.load(r).items() if isinstance(v, dict)
                        and v.get("installed")]) >= 5:
                    return proc, url
        except Exception:
            pass
        time.sleep(0.5)
    raise RuntimeError("server did not become ready in time")


@pytest.fixture(scope="module")
def base_url():
    port = _free_port()
    for stale in ("/tmp/calictl_e2e_history.jsonl", "/tmp/calictl_e2e_state.json", "/tmp/calictl_e2e_pairing.json"):
        try:                       # a previous run's samples must not make this one pass
            os.unlink(stale)
        except OSError:
            pass
    # BOTH caches must be redirected: the daemon appends an energy sample per poll, so without
    # this the suite writes mock data into the developer's real ~/.cache. Same for the pairing
    # cache -- it's only read as a `pairing_snapshot()` fallback before any wizard run, but a
    # real cached address there would falsely suppress the "prominent setup card" case.
    proc, url = _start_daemon(port, {"CALICTL_STATE_CACHE": "/tmp/calictl_e2e_state.json",
                                      "CALICTL_HISTORY_CACHE": "/tmp/calictl_e2e_history.jsonl",
                                      "CALICTL_PAIRING_CACHE": "/tmp/calictl_e2e_pairing.json"})
    try:
        yield url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


@pytest.fixture
def page(base_url):
    """A page whose uncaught JS errors FAIL the test that produced them.

    The web UI is un-built, client-side JS: a ReferenceError in a renderer only shows up when that
    screen is actually rendered in a browser. One shipped to buspi (#174: `roofControls()` used an
    undeclared name; the Roof screen went blank) because no test rendered that screen and nothing
    else could see it. Now EVERY e2e test doubles as a runtime-error detector: `pageerror`
    (uncaught exceptions) and console `error`s are collected and asserted empty at teardown.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch()
        pg = browser.new_page()
        js_errors = []
        pg.on("pageerror", lambda err: js_errors.append("pageerror: %s" % err))
        pg.on("console", lambda msg: js_errors.append("console.error: %s" % msg.text)
              if msg.type == "error" and "favicon" not in msg.text else None)
        pg.goto(base_url)
        try:
            yield pg
        finally:
            browser.close()
        assert not js_errors, "uncaught JS errors during this test:\n  " + "\n  ".join(js_errors)


@pytest.fixture
def pairing_url(tmp_path):
    """A dedicated daemon per pairing test (own port + caches): the guided-pairing wizard is a
    server-side state machine (`calictl.pairing`), so sharing the module-scoped `base_url` server
    across pairing tests would let one test's end state (e.g. bonded) leak into the next."""
    port = _free_port()
    proc, url = _start_daemon(port, {"CALICTL_STATE_CACHE": str(tmp_path / "state.json"),
                                      "CALICTL_HISTORY_CACHE": str(tmp_path / "history.jsonl"),
                                      "CALICTL_PAIRING_CACHE": str(tmp_path / "pairing.json")})
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
    proc, url = _start_daemon(port, {"CALICTL_ADDR": "",
                                     "CALICTL_STATE_CACHE": str(tmp_path / "state.json"),
                                     "CALICTL_HISTORY_CACHE": str(tmp_path / "history.jsonl"),
                                     "CALICTL_PAIRING_CACHE": str(tmp_path / "pairing.json")})
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


def test_water_screen_shows_level_percent(page):
    page.get_by_text("Water", exact=True).first.click()
    expect(page.get_by_text("Fresh water")).to_be_visible()
    assert "%" in page.locator("#app").inner_text()          # a real level readout, not a spec dump


def test_cooler_toggle_gives_feedback_then_applies(page):
    page.get_by_text("Cooler", exact=True).first.click()
    sw = page.locator(".switch").first
    expect(sw).to_have_attribute("aria-checked", "false")     # off at start
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
    expect(page.locator(".row.ctl-off")).to_have_count(2)          # exactly lights + usb greyed
    expect(master).to_be_enabled()                                  # master itself is usable


def test_cooler_quiet_and_timer_controls_follow_power(page):
    # The app's own gates: quiet mode is only settable while the refrigerator box is ON; the
    # cooling timer only while it is OFF. Both rows grey (.ctl-off) with the reason as tooltip.
    page.get_by_text("Cooler", exact=True).first.click()
    power = page.get_by_role("switch", name="Refrigerator box")
    quiet = page.locator("select").first                                   # the Quiet mode <select>
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
    expect(power).to_be_enabled()                     # the power switch itself is never gated
    set_power(was_on)                                 # leave the shared mock as we found it


def test_camping_master_toggle_applies(page):
    page.get_by_text("Camping mode", exact=True).first.click()
    sw = page.locator(".switch").first                        # first control = master
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
    expect(page.get_by_text("Alert")).to_be_visible()                 # readouts rendered too


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
    expect(page.get_by_text("Cooler", exact=True).first).to_be_visible()     # back on the dashboard


def test_roof_hold_release_sends_stop(page):
    # The hold-to-move contract: pressing Open streams the move, RELEASING must send STOP. Before the
    # fix, command() re-rendered synchronously, detaching the held button, so its closure-scoped
    # pointerup never fired and no STOP went out (the roof kept moving until the server's Position
    # auto-stop). The release is now caught at document level and the screen is not rebuilt while a
    # move is held. Assert the actual /api/command traffic: a roof "open" on press, "stop" on release.
    page.on("dialog", lambda d: d.accept())          # the "path clear?" confirm
    page.get_by_text("Roof", exact=True).first.click()
    open_btn = page.get_by_role("button", name="open")
    expect(open_btn).to_be_enabled()

    def is_cmd(req, what):
        return "/api/command" in req.url and req.method == "POST" \
            and '"roof"' in (req.post_data or "") and ('"%s"' % what) in (req.post_data or "")

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
        page.request.post(origin + "/api/command",
                          data={"function": "roof", "what": "stop", "value": None, "confirm": True})


def test_lighting_screen_lamps_are_directly_controllable(page):
    # like the app: lamps are always controllable (no "activate a profile first" gate). Dragging
    # a lamp from the lights-off state applies directly — the SET_BRIGHTNESS self-carries profile 9.
    page.get_by_text("Lighting", exact=True).first.click()
    expect(page.get_by_text("Reading lights")).to_be_visible()
    expect(page.get_by_text("Left", exact=True)).to_be_visible()   # a reading lamp
    # no manual Activate step, and controls are live from the start
    assert page.get_by_role("button", name="Activate").count() == 0
    slider = page.locator("input[type=range]").first
    expect(slider).to_be_enabled()
    assert page.locator(".switch").first.is_enabled()              # all-lights master too
    slider.fill("8")                                               # drag a lamp
    slider.dispatch_event("change")
    # lighting applied-ness is honestly "unknown" (the state char is a write-through echo),
    # so the toast says Sent — check the lamp, never a false green "Applied"
    expect(page.get_by_text("Sent — check the lamp")).to_be_visible(timeout=15000)


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
    RED = ("_text", "_toggle", "_widget", "_section", "_drawer",
           "undefined", "null", "NaN", "1900-", "[object Object]")
    for name in ("Cooler", "Camping mode", "Lighting", "Air heater", "Water", "Energy", "Vehicle"):
        page.goto(base_url)
        page.locator(".tile", has_text=name).first.click()
        page.wait_for_timeout(500)
        text = page.locator("#app").inner_text()
        for flag in RED:
            assert flag not in text, "%r rendered on the %s screen" % (flag, name)
    page.goto(base_url)                     # dashboard
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


def test_energy_chart_draws_from_daemon_history_not_influx(page, base_url):
    """The 24 h chart must render from the daemon's own append-only history.

    The e2e daemon runs with --no-influx, so if this draws a line at all, it proves the chart
    has no InfluxDB dependency -- the property the user explicitly required.
    """
    page.goto(base_url)
    page.locator(".tile", has_text="Energy").first.click()
    page.wait_for_selector("svg.echart", timeout=20000)      # polls every 1s -> samples accrue
    drawn = page.locator("svg.echart polyline.ec-v, svg.echart circle.ec-v-dot").count()
    assert drawn >= 1, "no voltage series rendered"
    assert "History unavailable" not in page.locator("#app").inner_text()


def test_open_control_survives_a_state_poll(page):
    """Regression: a live state poll must NOT re-render and destroy a control the user is
    interacting with. The 2s poll rebuilds #app (app.innerHTML=""), which used to slam an open
    <select> (Profile / Save current as) shut mid-selection. refreshState() skips the
    repaint while a <select>/time/range control is focused. We mark the focused <select>, wait
    through 2+ poll cycles (the mock's telemetry changes each poll, so a repaint WOULD otherwise
    fire), and assert the SAME element is still there — a repaint would have replaced it, losing
    the marker (and, in a browser, closing the dropdown)."""
    page.get_by_text("Lighting", exact=True).first.click()
    sel = page.locator("select").first                 # the 'Profile' dropdown
    expect(sel).to_be_visible()
    sel.evaluate("el => { el.dataset.probe = 'keep'; el.focus(); }")
    assert page.evaluate("document.activeElement && document.activeElement.tagName") == "SELECT"
    page.wait_for_timeout(5000)                         # span 2+ poll cycles
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


def test_pairing_wizard_wrong_passkey_ends_in_error_with_retry(pairing_page):
    """A wrong passkey each attempt: the real SM (`calictl.pairing`) retries up to MAX_ATTEMPTS
    times (cycling back through scanning/connecting/waiting_passkey) before giving up -> `error`
    with a Try-again button (design spec step 2's error case)."""
    page = pairing_page
    _open_and_start_pairing(page)
    passkey = page.locator("#pairing-passkey")
    for _ in range(3):   # calictl.pairing.MAX_ATTEMPTS
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
    assert "pairing_failed" not in page.locator("#app").inner_text()   # friendly text, not the raw enum
    assert "Connection failed" in page.locator("#app").inner_text()


def test_pairing_hidden_until_opened_from_menu(unconfigured_pairing_page):
    """UX: when the daemon is reachable there is NO pairing card in the main flow — the entry
    point is the topbar context menu only. Unpair is hidden there too until a bond exists (this
    daemon has no CALICTL_ADDR and no pairing cache, so no bond is configured)."""
    page = unconfigured_pairing_page
    expect(page.get_by_role("button", name="Menu")).to_be_visible()
    assert "Bluetooth" not in page.locator("#app").inner_text()
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
    page.on("dialog", lambda d: d.accept())   # Playwright auto-dismisses confirm() with no listener
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
