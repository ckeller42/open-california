"""Browser e2e of the remote app's LoRa setup (R_UI_REMOTE_APP, spec 5.1 steps 2-8 and 5.2): paste the full-rights
key, scan the van's setup QR (a photo through the file input), register the van at the fake TTS
(``tests/e2e/fake_ttn.py``), make the phone's keys, delete the full-rights key, wait for the join, send the
encrypted keys as port-12 downlinks and wait for the van's confirmation. No real TTS server is contacted: the
phone's configuration carries the test seam ``base`` (honoured only for ``http://127.0.0.1:<port>``) and nothing
else. The QR images are made here with ``segno``; every key is a dummy built in code. OPT-IN like test_gui.py.

.. test:: The remote app sets the van up over TTN and LoRa, the full-rights key is deleted before the first downlink
   :id: T_UI_REMOTE_SETUP
   :links: R_UI_REMOTE_APP

.. test:: A reload during the setup resumes the wait; the full-rights key is never in the phone's storage
   :id: T_UI_REMOTE_SETUP_RESUME
   :links: R_UI_REMOTE_APP

.. test:: A confirmation with missing fragments sends only those again; a repair QR configures an empty phone
   :id: T_UI_REMOTE_SETUP_REPAIR
   :links: R_UI_REMOTE_APP
"""

import contextlib
import hashlib
import io
import json
import re
import threading
from datetime import UTC, datetime, timedelta

import pytest

from tests.e2e.test_remote_app import CLUSTER, port2, port4, site, ttn  # noqa: F401 — site/ttn are fixtures

segno = pytest.importorskip("segno")
AESGCM = pytest.importorskip("cryptography.hazmat.primitives.ciphers.aead").AESGCM

DEV_EUI = bytes([0x70, 0xB3, 0xD5, 0x7E, 0xD0, 0x0A, 0xBC, 0xDE]).hex().upper()
DEV = "van-0abcde"  # van- + the last 6 hex of the DevEUI, lower case
APP_KEY = bytes(range(0, 0x100, 0x11)).hex().upper()  # dummies (gitleaks): built, never literal keys
TK = bytes(range(0x20, 0x30))

# the texts each step shows, EN and DE (the flow is the same in both)
TEXT = {
    "en-US": {
        "setup": "Set up LoRa",
        "scan": "Scan the van's code",
        "remove": "Remove this van",
        "check": "Check the key",
        "scanhint": "setup code",
        "join": "Waiting for the van's first uplink",
        "sent": "Waiting for its confirmation",
        "done": "The van has the keys.",
        "show": "Show the van",
        "rescan": "scan the new setup code",
        "keyleft": "in The Things Network console",
        "userkey": "This is not an application key",
    },
    "de-DE": {
        "setup": "LoRa einrichten",
        "scan": "Code des Fahrzeugs scannen",
        "remove": "Dieses Fahrzeug entfernen",
        "check": "Schlüssel prüfen",
        "scanhint": "Einrichtungscode",
        "join": "Warte auf die erste Nachricht des Fahrzeugs",
        "sent": "Warte auf seine Bestätigung",
        "done": "Das Fahrzeug hat die Schlüssel.",
        "show": "Fahrzeug anzeigen",
        "rescan": "neuen Einrichtungscode",
        "keyleft": "in der Konsole von The Things Network",
        "userkey": "Das ist kein Anwendungsschlüssel",
    },
}


def qr_png(text):
    """The QR as the van draws it (alphanumeric, error correction L, 4-module quiet zone), as PNG bytes."""
    buf = io.BytesIO()
    segno.make(text, error="l", mode="alphanumeric", micro=False).save(buf, kind="png", scale=6, border=4)
    return buf.getvalue()


def setup_qr(tk=TK):
    return "OCS1*%s*%s*%s" % (DEV_EUI, APP_KEY, tk.hex().upper())


def _empty_app(fake):
    """OC_REMOTE and only the test seam: no usable configuration (an empty app), keys go to the fake. Set once: a
    reload keeps what the app stored."""
    return (
        "window.OC_REMOTE = true; localStorage.getItem('oc.remote.cfg') || localStorage.setItem('oc.remote.cfg', %s);"
        % json.dumps(json.dumps({"base": fake.base}))
    )


@contextlib.contextmanager
def _page(error_gated_page, site, ttn, locale="en-US", **kw):  # noqa: F811
    with error_gated_page(
        site, init_script=_empty_app(ttn), service_workers="block", locale=locale, **kw
    ) as pg:
        pg.clock.install()  # the 15 s setup poll: fast_forward instead of waiting
        yield pg


def _menu(pg, label):
    pg.locator("#menu").click()
    pg.get_by_role("menuitem", name=label).click()


def _photo(pg, text):
    pg.locator("#rs-file").set_input_files(
        files=[{"name": "qr.png", "mimeType": "image/png", "buffer": qr_png(text)}]
    )


def _poll(pg):
    pg.clock.fast_forward(16000)


def _storage(pg):
    return pg.evaluate(
        "() => [localStorage, sessionStorage].flatMap((s) => Object.keys(s).map((k) => k + '=' + s.getItem(k)))"
    )


def _no_key_anywhere(pg, secret):
    """The full-rights key is in no storage value, not in the DOM (input values included)."""
    assert not any(secret in v for v in _storage(pg))
    assert secret not in pg.content()
    assert not pg.evaluate(
        "(k) => Array.from(document.querySelectorAll('input')).some((i) => i.value.includes(k))", secret
    )


def _setup(pg, fake, tx, check=lambda s: None):
    """Paste the key, scan the setup QR; returns the full-rights key's id and secret. Ends in the join wait."""
    kid, secret = fake.make_key("all")
    _menu(pg, tx["setup"])
    pg.locator("#rs-key").fill(secret)
    pg.get_by_role("button", name=tx["check"]).click()
    pg.get_by_text(tx["scanhint"]).first.wait_for()
    check(secret)
    _photo(pg, setup_qr())
    pg.get_by_text(tx["join"]).wait_for()
    check(secret)
    return kid, secret


def _join(pg, fake):
    """The van's first uplink -> the app sends the keys (port 12). Returns the pushed fragments."""
    n = len(fake.downlinks)
    fake.add_uplink(DEV, 4, port4())
    _poll(pg)
    pg.wait_for_function("() => (JSON.parse(localStorage.getItem('oc.remote.setup')) || {}).step === 'sent'")
    return _pushed(pg, fake, n)


def _pushed(pg, fake, n):
    """The downlinks pushed after the first ``n`` (the app stores ``sent`` before its push answers)."""
    for _ in range(100):
        if len(fake.downlinks) > n:
            pg.wait_for_timeout(100)  # one push request: let the fake finish its loop
            return fake.downlinks[n:]
        pg.wait_for_timeout(50)
    raise AssertionError("no downlink pushed")


def _wait_pushes(pg, fake, n):
    """Wait until the fake has logged ``n`` downlink pushes (the push, not the page state, is the event)."""
    for _ in range(100):
        count = sum(1 for _, p, _ in fake.calls if p.endswith("/down/push"))
        if count >= n:
            assert count == n, f"{count} pushes, expected {n}"
            return
        pg.wait_for_timeout(50)
    raise AssertionError(f"{count} pushes, expected {n}")


def _plain(frags):
    """Decrypt the fragments as the ESP does (AES-128-GCM, AD = the DevEUI)."""
    blob = b"".join(f[1:] for f in frags)
    return AESGCM(TK).decrypt(blob[:12], blob[12:], bytes.fromhex(DEV_EUI))


def _confirm(pg, fake, payload):
    fake.add_uplink(DEV, 12, payload, datetime.now(UTC) + timedelta(seconds=1))
    _poll(pg)


@pytest.mark.parametrize("locale", ["en-US", "de-DE"])
def test_setup_happy_path(site, ttn, error_gated_page, locale):  # noqa: F811
    tx = TEXT[locale]
    ttn.add_uplink(DEV, 2, port2(), datetime.now(UTC) - timedelta(minutes=5))  # the cockpit's data at the end
    with _page(error_gated_page, site, ttn, locale) as pg:
        kid, secret = _setup(pg, ttn, tx)
        # registered with the scanned DevEUI and AppKey, storage on, the phone's keys made
        assert ttn.devices[DEV]["ids"]["dev_eui"] == DEV_EUI
        assert ttn.devices[DEV]["js"]["root_keys"]["app_key"]["key"] == APP_KEY
        assert ttn.associations == {100: "storage-integration"}
        assert not [k for k in ttn.keys.values() if k["kind"] == "all"], "the full-rights key is deleted"
        cfg = json.loads(pg.evaluate("localStorage.getItem('oc.remote.cfg')"))
        assert (cfg["cluster"], cfg["appId"], cfg["devId"]) == (CLUSTER, ttn.app, DEV)
        frags = _join(pg, ttn)
        # the full-rights key was deleted before the first downlink
        calls = [(m, p, k) for m, p, k in ttn.calls]
        deleted = calls.index(("DELETE", "/api/v3/applications/%s/api-keys/%s" % (ttn.app, kid), kid))
        pushed = next(i for i, c in enumerate(calls) if c[1].endswith("/down/push"))
        assert deleted < pushed
        cmd_id = next(k["id"] for k in ttn.keys.values() if k["name"] == "oc-remote-cmd")
        assert {(d, port, key) for d, port, _, key in frags} == {(DEV, 12, cmd_id)}
        frags = [f for _, _, f, _ in frags]
        assert all(len(f) <= 51 for f in frags) and frags[-1][0] & 0x80
        plain = _plain(frags)
        assert plain[0] == 1 and CLUSTER.encode() in plain and DEV.encode() in plain
        pg.get_by_text(tx["sent"]).wait_for()
        _confirm(pg, ttn, b"\x00" + hashlib.sha256(plain).digest()[:4])
        pg.get_by_text(tx["done"]).wait_for()
        assert pg.evaluate("localStorage.getItem('oc.remote.setup')") is None  # the transfer key is wiped
        pg.get_by_role("button", name=tx["show"]).click()
        pg.locator(".summary").get_by_text("11 / 29 l").wait_for()
        _no_key_anywhere(pg, secret)


def test_setup_refuses_a_user_key(site, ttn, error_gated_page):  # noqa: F811
    _, secret = ttn.make_key("user")
    with _page(error_gated_page, site, ttn) as pg:
        _menu(pg, "Set up LoRa")
        pg.locator("#rs-key").fill(secret)
        pg.get_by_role("button", name="Check the key").click()
        pg.get_by_text(TEXT["en-US"]["userkey"]).wait_for()
        _no_key_anywhere(pg, secret)
    assert not ttn.writes()


def test_setup_resumes_after_reload(site, ttn, error_gated_page):  # noqa: F811
    tx = TEXT["en-US"]
    with _page(error_gated_page, site, ttn) as pg:
        _, secret = _setup(pg, ttn, tx)
        pg.reload()  # the page clock stays installed
        pg.get_by_text(tx["join"]).wait_for()  # resumed at the wait, not at the key form
        st = json.loads(pg.evaluate("localStorage.getItem('oc.remote.setup')"))
        assert st["tk"] == TK.hex() and st["devEui"] == DEV_EUI and st["step"] == "join"
        assert not [k for k in ttn.keys.values() if k["kind"] == "all"], "no live full-rights key"
        frags = _join(pg, ttn)  # the resumed page goes on with the transfer
        assert len(frags) == 4
        _no_key_anywhere(pg, secret)
    assert sum(1 for m, p, _ in ttn.calls if m == "POST" and p.endswith("/devices")) == 1, "no second device"
    assert len(ttn.devices) == 1


def test_missing_fragment_resent(site, ttn, error_gated_page):  # noqa: F811
    tx = TEXT["en-US"]
    with _page(error_gated_page, site, ttn) as pg:
        _setup(pg, ttn, tx)
        first = [f for _, _, f, _ in _join(pg, ttn)]
        assert len(first) == 4
        n = len(ttn.downlinks)
        _confirm(pg, ttn, bytes([0x02, 0x0D]))  # fragments 0, 2, 3 received: 1 is missing
        pg.wait_for_function(
            "() => JSON.parse(localStorage.getItem('oc.remote.setup')).queued.join() === '1'"
        )
        again = [f for _, _, f, _ in _pushed(pg, ttn, n)]
        assert again == [first[1]], "exactly fragment 1, byte for byte (same nonce)"
        pg.get_by_text(tx["sent"]).wait_for()


def test_bad_tag_asks_for_a_new_scan(site, ttn, error_gated_page):  # noqa: F811
    """Status 1 (or 3): the van has no matching transfer key; a new setup QR (new TK) restarts the transfer."""
    tx = TEXT["en-US"]
    with _page(error_gated_page, site, ttn) as pg:
        _setup(pg, ttn, tx)
        _join(pg, ttn)
        _confirm(pg, ttn, b"\x01")
        pg.get_by_text(tx["rescan"]).wait_for()
        assert "tk" not in json.loads(pg.evaluate("localStorage.getItem('oc.remote.setup')"))
        n = len(ttn.downlinks)
        new_tk = bytes(range(0x40, 0x50))
        _photo(pg, setup_qr(new_tk))
        pg.get_by_text(tx["sent"]).wait_for()
        blob = b"".join(f[1:] for _, _, f, _ in _pushed(pg, ttn, n))
        assert AESGCM(new_tk).decrypt(blob[:12], blob[12:], bytes.fromhex(DEV_EUI))[0] == 1


def test_key_delete_failure_is_not_success(site, ttn, error_gated_page):  # noqa: F811
    """The full-rights key could not be deleted: the owner is told to delete it, on every screen up to the end."""
    tx = TEXT["en-US"]
    ttn.refuse_key_delete = True
    with _page(error_gated_page, site, ttn, allow_errors=("status of 500",)) as pg:
        _setup(pg, ttn, tx)
        pg.get_by_text(tx["keyleft"]).wait_for()
        frags = [f for _, _, f, _ in _join(pg, ttn)]
        _confirm(pg, ttn, b"\x00" + hashlib.sha256(_plain(frags)).digest()[:4])
        pg.get_by_text(tx["keyleft"]).wait_for()
        assert pg.get_by_text(tx["done"]).count() == 0


def test_repair_qr_configures_phone(site, ttn, error_gated_page):  # noqa: F811
    _, read = ttn.make_key("read")
    _, cmd = ttn.make_key("cmd")
    ttn.add_uplink(DEV, 2, port2(), datetime.now(UTC) - timedelta(minutes=5))
    text = "*".join(["OCR1", CLUSTER.upper(), ttn.app.upper(), DEV.upper(), read, cmd])
    with _page(error_gated_page, site, ttn) as pg:
        _menu(pg, "Scan the van's code")
        _photo(pg, text)
        pg.locator("#status").get_by_text("LoRa · 5 min ago").wait_for()
        pg.locator(".summary").get_by_text("11 / 29 l").wait_for()
        cfg = json.loads(pg.evaluate("localStorage.getItem('oc.remote.cfg')"))
        assert cfg | {"base": None} == {
            "cluster": CLUSTER, "appId": ttn.app, "devId": DEV, "readKey": read, "cmdKey": cmd, "base": None
        }  # fmt: skip


def test_storage_has_no_full_key(site, ttn, error_gated_page):  # noqa: F811
    """After every step: no localStorage / sessionStorage value, no input and no DOM text holds the pasted key."""
    tx = TEXT["en-US"]
    with _page(error_gated_page, site, ttn) as pg:
        _, secret = _setup(pg, ttn, tx, check=lambda s: _no_key_anywhere(pg, s))
        frags = [f for _, _, f, _ in _join(pg, ttn)]
        _no_key_anywhere(pg, secret)
        _confirm(pg, ttn, b"\x00" + hashlib.sha256(_plain(frags)).digest()[:4])
        pg.get_by_text(tx["done"]).wait_for()
        _no_key_anywhere(pg, secret)


def test_remove_this_van(site, ttn, error_gated_page):  # noqa: F811
    _, read = ttn.make_key("read")
    seed = {
        "cluster": CLUSTER,
        "appId": ttn.app,
        "devId": DEV,
        "readKey": read,
        "cmdKey": read,
        "base": ttn.base,
    }
    init = "window.OC_REMOTE = true; localStorage.setItem('oc.remote.cfg', %s);" % json.dumps(
        json.dumps(seed)
    )
    init += "localStorage.setItem('oc.remote.energy', '[]');"
    with error_gated_page(site, init_script=init, service_workers="block") as pg:
        pg.locator("#status").get_by_text("LoRa").wait_for()
        _menu(pg, "Remove this van")
        pg.get_by_text("stays registered at The Things Network").wait_for()
        assert (
            pg.evaluate("localStorage.getItem('oc.remote.cfg')") is not None
        )  # nothing gone before the confirmation
        pg.get_by_role("button", name="Remove", exact=True).click()
        pg.wait_for_function("() => !Object.keys(localStorage).some((k) => k.startsWith('oc.remote.'))")


def test_join_hint_after_10_min(site, ttn, error_gated_page):  # noqa: F811
    """No uplink 10 min after the registration: the screen says what to check (spec 5.1 step 6)."""
    tx = TEXT["en-US"]
    with _page(error_gated_page, site, ttn) as pg:
        _setup(pg, ttn, tx)
        hint = pg.get_by_text("check the antenna")
        assert not hint.is_visible()
        pg.clock.fast_forward("10:30")
        hint.wait_for()
        pg.get_by_text(re.compile(r"since .* \(1[01] min ago\)")).wait_for()


def test_reload_during_registration_warns_about_the_key(site, ttn, error_gated_page):  # noqa: F811
    """The page closes while the van is registered: the full-rights key is still live at TTN, so every remote
    screen names its id (never the secret) until the owner says it is deleted."""
    tx = TEXT["en-US"]
    kid, secret = ttn.make_key("all")
    ttn.create_gate = threading.Event()
    try:
        with _page(error_gated_page, site, ttn, allow_errors=("ERR_",)) as pg:
            _menu(pg, tx["setup"])
            pg.locator("#rs-key").fill(secret)
            pg.get_by_role("button", name=tx["check"]).click()
            pg.get_by_text(tx["scanhint"]).first.wait_for()
            assert pg.get_by_text(kid).count() == 0  # the flow is alive: no warning yet
            _photo(pg, setup_qr())
            pg.wait_for_function("() => document.body.textContent.includes('Talking to TTN')")
            pg.reload()
            warn = pg.get_by_text("Delete the API key %s in The Things Network console" % kid)
            warn.wait_for()
            _no_key_anywhere(pg, secret)
            _menu(pg, tx["scan"])
            warn.wait_for()  # on every remote screen
            pg.get_by_role("button", name="I have deleted it").click()
            assert pg.get_by_text(kid).count() == 0
            assert pg.evaluate("localStorage.getItem('oc.remote.keyleft')") is None
    finally:
        ttn.create_gate.set()


def test_resend_after_10_min_without_confirmation(site, ttn, error_gated_page):  # noqa: F811
    """A reload between storing "sent" and the push: no confirmation 10 min after ``sentAt`` -> the stored fragments
    are pushed again, once, byte-identical (same nonce, same TK); never sooner (fair use). After 3 resends without a
    confirmation the app asks for a new scan."""
    tx = TEXT["en-US"]
    with _page(error_gated_page, site, ttn) as pg:
        _setup(pg, ttn, tx)
        # "sent" stored, the push never made (the page closed in between)
        pg.evaluate(
            """() => { const st = JSON.parse(localStorage.getItem('oc.remote.setup'));
                       localStorage.setItem('oc.remote.setup', JSON.stringify({...st, step: 'sent',
                         queued: [0, 1, 2, 3], total: 4, sentAt: Date.now()})); }"""
        )
        pg.reload()
        pg.get_by_text(tx["sent"]).wait_for()
        pg.clock.fast_forward("09:00")
        pg.wait_for_timeout(500)
        assert not ttn.downlinks, "no resend before 10 min"
        pg.clock.fast_forward("01:30")
        frags = [f for _, _, f, _ in _pushed(pg, ttn, 0)]
        expected = pg.evaluate(
            """async () => { const st = JSON.parse(localStorage.getItem('oc.remote.setup'));
                 const fr = await sealTransfer(packPlain(remoteCfg()), loraUnhex(st.tk), st.devEui, loraUnhex(st.nonce));
                 return fr.map((f) => Array.from(f)); }"""
        )
        assert frags == [bytes(f) for f in expected]
        _wait_pushes(pg, ttn, 1)
        for n in (2, 3):  # two more resends, 10 min apart
            pg.clock.fast_forward("10:30")
            _wait_pushes(pg, ttn, n)
        pg.clock.fast_forward("10:30")
        pg.get_by_text("The van did not confirm").wait_for()
        st = json.loads(pg.evaluate("localStorage.getItem('oc.remote.setup')"))
        assert st["step"] == "rescan" and "tk" not in st
        _wait_pushes(pg, ttn, 3)
        # a new scan starts a new transfer with a fresh resend budget
        _photo(pg, setup_qr(bytes(range(0x40, 0x50))))
        pg.get_by_text(tx["sent"]).wait_for()
        st = json.loads(pg.evaluate("localStorage.getItem('oc.remote.setup')"))
        assert st["resends"] == 0
        _wait_pushes(pg, ttn, 4)  # 3 resends, then the new transfer's push
        pg.clock.fast_forward("10:30")
        _wait_pushes(pg, ttn, 5)  # exactly one resend
        pg.wait_for_timeout(500)
        _wait_pushes(pg, ttn, 5)
