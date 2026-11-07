"""The LoRa box of the shared UI's Satellite-settings screen (R_FW_LORA, R_FW_LORA_SETUP) against the
ESP stub: the LoRa switch posts ``{"lora": <bool>}`` to ``/api/experimental``; the values are
hidden while ``experimental.lora`` is off, ``device.lora`` in words while it is on (the last uplink
only as "confirmed"), and "Forget LoRa" with its in-page confirmation posting
``{"forget": true, "confirm": true}``. EN + DE; every test fails on an uncaught page error
(``error_gated_page``).

.. test:: Satellite settings' LoRa box: hidden while off, values in words, Forget LoRa asks then posts (EN + DE)
   :id: T_UI_SAT_LORA
   :links: R_FW_LORA_SETUP

.. test:: Satellite settings' LoRa switch: off -> on -> off posts {"lora": <bool>}, a refusal says so (EN + DE)
   :id: T_UI_SAT_LORA_SWITCH
   :links: R_FW_LORA

.. test:: No LoRa key or DevEUI reaches a usage beacon, after a forget and a status view
   :id: T_UX_NO_SECRETS_LORA
   :links: R_FW_LORA_SETUP, R_UX_TELEMETRY
"""

import json
import re
from pathlib import Path

import pytest

from tools import ux_gallery

sync_api = pytest.importorskip("playwright.sync_api")
expect = sync_api.expect
try:
    with sync_api.sync_playwright() as _p:
        _p.chromium.launch().close()
except Exception as _e:  # noqa: BLE001 — any launch failure (missing executable, deps) -> skip
    pytest.skip("Playwright chromium browser not installed: %s" % _e, allow_module_level=True)

_DE_SRC = (Path(__file__).resolve().parents[2] / "calictl/webui/strings.de.js").read_text(encoding="utf-8")
DE = {
    json.loads('"%s"' % k): json.loads('"%s"' % v)
    for k, v in re.findall(r'^\s*"((?:[^"\\]|\\.)*)":\s*"((?:[^"\\]|\\.)*)",?\s*(?://.*)?$', _DE_SRC, re.M)
}
LOCALES = [("en-US", "en"), ("de-DE", "de")]
FORGET_BODY = {"forget": True, "confirm": True}
HINT = (
    "Set up: hold the satellite's screen for 3 seconds and scan the code with the Open California remote app."
)
NOTE = "Only for a satellite with a LoRa module (RAK3172). Off: the LoRa pins stay untouched."
ASK = (
    "Forget LoRa? The satellite deletes its LoRa keys and switches LoRa off. "
    "The device stays registered at The Things Network until you delete it there."
)


def tr(en, lang, **kw):
    """The UI's text for ``en`` in ``lang`` (strings.de.js), ``{name}`` filled from ``kw``."""
    s = DE[en] if lang == "de" else en
    for k, v in kw.items():
        s = s.replace("{%s}" % k, v)
    return s


def _lora_on(stub, **over):
    dev = stub.fixtures["satellite"]["/api/state"]["device"]
    dev["experimental"]["lora"] = True
    dev["lora"] = ux_gallery.lora_device(**over)
    return dev


@pytest.fixture
def stub():
    """Satellite mode, the LoRa switch on, a joined and set-up van."""
    with ux_gallery.EspStub("satellite") as s:
        _lora_on(s)
        yield s


def _open(error_gated_page, stub, lang):
    return error_gated_page(stub.base + "/#satsettings", locale="de-DE" if lang == "de" else "en-US")


def _exp_posts(stub):
    return [b for p, b in stub.posts if p == "/api/experimental"]


def test_values_are_hidden_while_the_switch_is_off(error_gated_page):
    with ux_gallery.EspStub("satellite") as s:  # the default fixture: experimental.lora off
        with _open(error_gated_page, s, "en") as pg:
            expect(pg.locator("#lora-status")).to_have_text("LoRa: off")
            expect(pg.locator("#lora")).to_contain_text(NOTE)
            for gone in ("#lora-on", "#lora-forget", "#lora-module"):
                expect(pg.locator(gone)).to_be_hidden()


@pytest.mark.parametrize("locale,lang", LOCALES)
def test_the_switch_turns_lora_on_and_off(error_gated_page, locale, lang):
    with ux_gallery.EspStub("satellite") as s:
        with _open(error_gated_page, s, lang) as pg:
            expect(pg.locator("#lora-status")).to_have_text(tr("LoRa: off", lang))
            expect(pg.locator("#lora")).to_contain_text(tr(NOTE, lang))
            pg.click("#lora-toggle")
            expect(pg.locator("#lora-status")).to_have_text(tr("LoRa: on", lang))
            expect(pg.locator("#lora-toggle")).to_have_attribute("aria-checked", "true")
            expect(pg.locator("#lora-on")).to_be_visible()  # the values follow the switch
            expect(pg.locator("#lora-setup")).to_have_text(tr("Not set up", lang))
            assert _exp_posts(s) == [{"lora": True}]
            pg.click("#lora-toggle")
            expect(pg.locator("#lora-status")).to_have_text(tr("LoRa: off", lang))
            expect(pg.locator("#lora-on")).to_be_hidden()
            assert _exp_posts(s) == [{"lora": True}, {"lora": False}]


def test_a_refused_switch_says_so_and_stays_off(error_gated_page):
    with ux_gallery.EspStub("satellite") as s:
        s.post_reply["/api/experimental"] = (500, {"ok": False, "error": "store"})
        with error_gated_page(
            s.base + "/#satsettings", locale="en-US", allow_errors=("status of 500",)
        ) as pg:
            expect(pg.locator("#lora-status")).to_have_text("LoRa: off")
            pg.click("#lora-toggle")
            expect(pg.locator("#lora-msg")).to_have_text("Couldn't change the switch")
            assert _exp_posts(s) == [{"lora": True}]
            expect(pg.locator("#lora-toggle")).to_have_attribute("aria-checked", "false")
            expect(pg.locator("#lora-on")).to_be_hidden()


@pytest.mark.parametrize("locale,lang", LOCALES)
def test_values_in_words(stub, error_gated_page, locale, lang):
    with _open(error_gated_page, stub, lang) as pg:
        box = pg.locator("#lora")
        expect(box).to_be_visible()
        expect(box.locator("h2")).to_have_text("LoRa")
        expect(pg.locator("#lora-module")).to_have_text(tr("Found", lang))
        expect(pg.locator("#lora-net")).to_have_text(tr("Joined", lang))
        expect(pg.locator("#lora-last")).to_have_text(tr("confirmed {n} min ago", lang, n="7"))
        expect(pg.locator("#lora-air")).to_have_text(
            tr("{a} s confirmed / {c} s charged", lang, a="3.3", c="3.9")
        )
        expect(pg.locator("#lora-setup")).to_have_text(tr("Set up", lang))
        expect(pg.locator("#lora-eui")).to_have_text("70B3D57ED0012345")
        expect(box).to_contain_text(tr(HINT, lang))
        expect(box.get_by_role("button", name=tr("Forget LoRa", lang))).to_be_visible()


@pytest.mark.parametrize("locale,lang", LOCALES)
def test_states_before_the_first_confirmed_uplink(stub, error_gated_page, locale, lang):
    _lora_on(stub, module="absent", joined=False, last_s=None, airtime_ms_24h=0, airtime_charged_ms_24h=500,
             setup="keys_made", has_ttn_keys=False, dev_eui=None)  # fmt: skip
    with _open(error_gated_page, stub, lang) as pg:
        expect(pg.locator("#lora-module")).to_have_text(tr("Not found", lang))
        expect(pg.locator("#lora-net")).to_have_text(tr("Not joined", lang))
        expect(pg.locator("#lora-last")).to_have_text(tr("none confirmed yet", lang))  # never a bare "sent"
        expect(pg.locator("#lora-air")).to_have_text(
            tr("{a} s confirmed / {c} s charged", lang, a="0.0", c="0.5")
        )
        expect(pg.locator("#lora-setup")).to_have_text(tr("Keys made, waiting for the app", lang))
        expect(pg.locator("#lora-eui")).to_have_text("—")


@pytest.mark.parametrize("locale,lang", LOCALES)
def test_forget_asks_and_cancel_posts_nothing(stub, error_gated_page, locale, lang):
    with _open(error_gated_page, stub, lang) as pg:
        pg.on("dialog", lambda d: pytest.fail("a native dialog: %s" % d.message))
        box = pg.locator("#lora")
        box.get_by_role("button", name=tr("Forget LoRa", lang)).click()
        expect(box).to_contain_text(tr(ASK, lang))
        box.get_by_role("button", name=tr("Cancel", lang)).click()
        expect(box.get_by_role("button", name=tr("Forget LoRa", lang))).to_be_visible()
        expect(box).not_to_contain_text(tr(ASK, lang))
        assert [p for p in stub.posts if p[0] == "/api/lora"] == []


@pytest.mark.parametrize("locale,lang", LOCALES)
def test_forget_confirmed_posts_and_the_values_disappear(stub, error_gated_page, locale, lang):
    with _open(error_gated_page, stub, lang) as pg:
        box = pg.locator("#lora")
        box.get_by_role("button", name=tr("Forget LoRa", lang)).click()
        box.get_by_role("button", name=tr("Forget LoRa", lang)).click()  # the confirm button
        expect(pg.locator("#lora-on")).to_be_hidden()
        expect(pg.locator("#lora-status")).to_have_text(tr("LoRa: off", lang))
        assert stub.posts[-1] == ("/api/lora", FORGET_BODY)
        assert stub.fixtures["satellite"]["/api/state"]["device"]["experimental"]["lora"] is False


def test_a_failed_forget_keeps_the_box_and_says_so(stub, error_gated_page):
    stub.post_reply["/api/lora"] = (500, {"ok": False, "error": "store"})
    with error_gated_page(stub.base + "/#satsettings", locale="en-US", allow_errors=("status of 500",)) as pg:
        box = pg.locator("#lora")
        box.get_by_role("button", name="Forget LoRa").click()
        box.get_by_role("button", name="Forget LoRa").click()
        expect(pg.locator("#lora-msg")).to_have_text("Couldn't forget LoRa")
        expect(pg.locator("#lora-on")).to_be_visible()


def test_no_lora_secret_reaches_a_beacon(stub, error_gated_page):
    """A status view and a forget with the usage switch on: no beacon carries key material or the DevEUI.

    The fixture smuggles dummy key fields into ``device.lora`` (the firmware never sends them): the
    page must forward none of them.
    """
    key = bytes(range(0x00, 0x100, 0x11)).hex()  # 32 hex digits, built at runtime (gitleaks)
    _lora_on(stub, app_key=key, transfer_key=key[::-1], ttn_read_key="NNSXS." + key)
    with _open(error_gated_page, stub, "en") as pg:
        pg.wait_for_function("() => !!(STATE._meta && STATE._meta.ux)")
        box = pg.locator("#lora")
        expect(box).to_contain_text("70B3D57ED0012345")
        box.get_by_role("button", name="Forget LoRa").click()
        box.get_by_role("button", name="Forget LoRa").click()
        expect(pg.locator("#lora-on")).to_be_hidden()
        pg.evaluate("dispatchEvent(new PageTransitionEvent('pagehide'))")
        for _ in range(50):
            if stub.ux_beacons:
                break
            pg.wait_for_timeout(100)
    assert stub.ux_beacons, "no beacon reached the stub"
    beacons = json.dumps(stub.ux_beacons)
    for secret in (key, key[::-1], "NNSXS", "70B3D57ED0012345"):
        assert secret not in beacons
