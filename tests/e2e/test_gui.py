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
lacked: real data readouts, installed-gating, and action feedback.
"""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request

import pytest

sync_api = pytest.importorskip("playwright.sync_api")
sync_playwright = sync_api.sync_playwright
expect = sync_api.expect

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="module")
def base_url():
    port = _free_port()
    for stale in ("/tmp/calictl_e2e_history.jsonl", "/tmp/calictl_e2e_state.json"):
        try:                       # a previous run's samples must not make this one pass
            os.unlink(stale)
        except OSError:
            pass
    env = dict(os.environ,
               CALICTL_ADDR="MO:CK:CA:MP:ER:00", PYTHONUNBUFFERED="1",
               CALICTL_ARM_DELAY_S="0.3", CALICTL_SETTLE_S="0.3", CALICTL_HEARTBEAT_PERIOD_S="0.1",
               CALICTL_HEARTBEAT_WARMUP_S="0", CALICTL_STATE_CACHE="/tmp/calictl_e2e_state.json",
               # BOTH caches must be redirected: the daemon appends an energy sample per poll, so
               # without this the suite writes mock data into the developer's real ~/.cache.
               CALICTL_HISTORY_CACHE="/tmp/calictl_e2e_history.jsonl",
               CALICTL_ENABLE_WRITES="1",  # e2e exercises control writes -> not read-only
               CALICTL_PERSISTENT_SESSION="1")  # default, explicit for the session-pill test's intent
    proc = subprocess.Popen(
        [sys.executable, "-m", "tools.run_against_mock", "serve",
         "--web", str(port), "--interval", "1", "--no-influx"],
        cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    url = "http://127.0.0.1:%d" % port
    try:
        deadline = time.time() + 30
        while time.time() < deadline:
            if proc.poll() is not None:
                raise RuntimeError("server exited early:\n" + proc.stdout.read().decode())
            try:
                with urllib.request.urlopen(url + "/api/state", timeout=1) as r:
                    if len([k for k, v in json.load(r).items() if isinstance(v, dict)
                            and v.get("installed")]) >= 5:
                        break
            except Exception:
                pass
            time.sleep(0.5)
        else:
            raise RuntimeError("server did not become ready in time")
        yield url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


@pytest.fixture
def page(base_url):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        pg = browser.new_page()
        pg.goto(base_url)
        yield pg
        browser.close()


def test_dashboard_shows_installed_tiles_and_hides_uninstalled(page):
    # installed features render as tiles; the uninstalled roof (mock has no pop-top) does not.
    for name in ("Cooler", "Camping mode", "Water", "Energy"):
        expect(page.get_by_text(name, exact=True).first).to_be_visible()
    assert page.get_by_text("Roof", exact=True).count() == 0


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


def test_camping_master_toggle_applies(page):
    page.get_by_text("Camping mode", exact=True).first.click()
    sw = page.locator(".switch").first                        # first control = master
    sw.click()
    expect(page.get_by_text("Applied")).to_be_visible(timeout=15000)
    expect(page.locator('.switch[aria-checked="true"]').first).to_be_visible()


def test_lighting_screen_activate_profile(page):
    # the rebuilt lighting screen: grouped lamps render; activating a profile applies
    page.get_by_text("Lighting", exact=True).first.click()
    expect(page.get_by_text("Reading lights")).to_be_visible()
    expect(page.get_by_text("Left", exact=True)).to_be_visible()   # a reading lamp
    # with NO profile active, lamp controls must be disabled (they can't apply) — not a live trap
    assert page.locator("input[type=range]").first.is_disabled()
    assert page.locator(".switch").first.is_disabled()             # all-lights master too
    page.get_by_role("button", name="Activate").click()
    expect(page.get_by_text("Applied")).to_be_visible(timeout=15000)
    # after activating, the lamp sliders become controllable
    expect(page.locator("input[type=range]").first).to_be_enabled()


def test_dashboard_summary_card(page):
    # the app's "California Status" overview: fresh/grey water + leisure battery, above the tiles.
    card = page.locator(".card.summary")
    expect(card).to_be_visible()
    for label in ("Fresh water", "Grey water", "Second battery"):
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
