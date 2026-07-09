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
    env = dict(os.environ,
               CALICTL_ADDR="MO:CK:CA:MP:ER:00", PYTHONUNBUFFERED="1",
               CALICTL_ARM_DELAY_S="0.3", CALICTL_SETTLE_S="0.3", CALICTL_HEARTBEAT_PERIOD_S="0.1",
               CALICTL_HEARTBEAT_WARMUP_S="0")
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
    page.get_by_role("button", name="Activate").click()
    expect(page.get_by_text("Applied")).to_be_visible(timeout=15000)


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
