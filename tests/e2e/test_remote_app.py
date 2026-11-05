"""Browser e2e of the remote mode (R_UI_REMOTE_APP): the calictl web UI with ``window.OC_REMOTE = true`` reads
the van only from The Things Stack, here ``tests/e2e/fake_ttn.py``. No real TTS server is contacted.

A small static server serves ``calictl/webui`` with an index variant that adds the remote-only scripts
(``REMOTE_SCRIPTS``: ``strings.remote.de.js``, the vendored ``qr-scanner``, ``lora_setup.js``, ``ttn.js``, ``lora_decode.js``,
``remote.js``) before ``app.js``, as the remote build does; an init script sets ``OC_REMOTE`` and the
phone's configuration (``oc.remote.cfg``). The uplinks are the mock unit's frames packed as the firmware's
ports 2, 3 and 4 (``cali_lora.h``). OPT-IN like test_gui.py: skipped without Playwright + Chromium.

.. test:: The remote mode shows the van's LoRa data with ages and never calls the page's own /api/*
   :id: T_UI_REMOTE_NO_ROUTER
   :links: R_UI_REMOTE_APP
"""

import functools
import json
import sys
import threading
from datetime import UTC, datetime, timedelta
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from calictl import overrides, protocol
from tools import gen_lora_dict
from tools.mock_unit import MockCamperUnit, _pack_state

sync_api = pytest.importorskip("playwright.sync_api")
expect = sync_api.expect
try:
    with sync_api.sync_playwright() as _p:
        _p.chromium.launch().close()
except Exception as _e:  # noqa: BLE001 — any launch failure (missing executable, deps) -> skip
    pytest.skip("Playwright chromium browser not installed: %s" % _e, allow_module_level=True)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fake_ttn import FakeTtn  # noqa: E402

WEBUI = Path(__file__).resolve().parents[2] / "calictl" / "webui"
DEV = "van-000001"
REMOTE_SCRIPTS = (
    "strings.remote.de.js",
    "vendor/qr-scanner/qr-scanner.umd.min.js",
    "lora_setup.js",
    "ttn.js",
    "lora_decode.js",
    "remote.js",
)


def _frames():
    funcs, unit = protocol.load(), MockCamperUnit()
    overrides.apply(funcs)
    return {fn: _pack_state(funcs[fn], unit.state[fn]) for fn in ("energy", "water", "general", "cooler")}


def port2(version=1):
    """A port-2 uplink: the mock unit's energy + water frames, a Victron charger at 87 W, counters."""
    f = _frames()
    mppt = (
        (23).to_bytes(2, "big", signed=True)
        + (87).to_bytes(2, "big")
        + (42).to_bytes(2, "big")
        + bytes([5, 0])
    )
    ctr = b"".join(v.to_bytes(3, "big", signed=True) for v in (1234, 2345, -345))
    return (
        bytes([version])
        + (7).to_bytes(2, "big")
        + f["energy"]
        + f["water"]
        + mppt
        + (1305).to_bytes(2, "big")
        + ctr
    )


def port3():
    """A port-3 uplink with the cooler frame."""
    idx = [f["name"] for f in gen_lora_dict.functions()].index("cooler")
    cooler = _frames()["cooler"]
    return bytes([idx, len(cooler)]) + cooler


def port4():
    """A port-4 uplink: boot, uptime, the general frame, the firmware version."""
    return (
        bytes([1])
        + (7).to_bytes(2, "big")
        + (3600).to_bytes(4, "big")
        + _frames()["general"]
        + b"fw1\0\0\0\0\0"
    )


class _Static(SimpleHTTPRequestHandler):
    """calictl/webui, with ``/`` as the remote build's index (the remote-only scripts before app.js)."""

    def log_message(self, *a):
        pass

    def do_GET(self):
        if urlsplit(self.path).path in ("/", "/index.html"):
            tags = "".join('<script src="/%s"></script>\n' % n for n in REMOTE_SCRIPTS)
            html = (WEBUI / "index.html").read_text(encoding="utf-8")
            body = html.replace('<script src="/app.js">', tags + '<script src="/app.js">').encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()


@pytest.fixture
def site():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(_Static, directory=str(WEBUI)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield "http://127.0.0.1:%d" % httpd.server_address[1]
    httpd.shutdown()
    httpd.server_close()


@pytest.fixture
def ttn():
    with FakeTtn() as f:
        yield f


CLUSTER = "eu1.cloud.thethings.network"


def _init(ttn, secret, **over):
    """OC_REMOTE + the phone's configuration, as the remote build and the setup leave them. ``base`` is the test
    seam: the app honours it only for ``http://127.0.0.1:<port>``, so no real TTS server is contacted."""
    cfg = {
        "cluster": CLUSTER,
        "appId": ttn.app,
        "devId": DEV,
        "readKey": secret,
        "cmdKey": secret,
        "base": ttn.base,
    }
    cfg.update(over)
    return "window.OC_REMOTE = true; localStorage.setItem('oc.remote.cfg', %s);" % json.dumps(json.dumps(cfg))


def test_remote_never_calls_same_origin_api(site, ttn, error_gated_page):
    _, secret = ttn.make_key("read")
    now = datetime.now(UTC)
    ttn.add_uplink(DEV, 4, port4(), now - timedelta(hours=2))
    ttn.add_uplink(DEV, 3, port3(), now - timedelta(minutes=40))
    ttn.add_uplink(DEV, 2, port2(), now - timedelta(minutes=5))
    ttn.add_uplink(
        DEV, 12, b"\x00\x01\x02\x03\x04", now - timedelta(minutes=3)
    )  # a key-transfer confirmation
    reqs = []
    with error_gated_page(site, init_script=_init(ttn, secret), requests=reqs, service_workers="block") as pg:
        expect(pg.locator("#status")).to_have_text("LoRa · 5 min ago")
        summary = pg.locator(".summary")
        expect(summary).to_contain_text("Second battery")
        expect(summary).to_contain_text("100%")
        expect(summary).to_contain_text("11 / 29 l")
        expect(summary).to_contain_text("5 min ago")
        expect(pg.locator(".tile", has_text="Cooler")).to_contain_text("40 min ago")
        expect(pg.get_by_text("LoRa — display only")).to_be_visible()
        pg.locator(".tile", has_text="Energy").click()
        expect(pg.locator("#app")).to_contain_text("87 W")  # the solar charger, from port 2
        expect(pg.locator("#app")).to_contain_text("5 min ago")
        # every control is read-only (no camping-mode command yet, spec 5.4)
        expect(
            pg.locator(
                "#app :is(input,select):enabled, #app [role=switch]:not([disabled]), #app button:enabled"
            )
        ).to_have_count(0)
        # the guard itself: api(), satFetch() and the beacon refuse before any request
        n = len(reqs)
        refused = pg.evaluate(
            """async () => [await api("/api/state").then(() => "sent", (e) => e.message),
                           await satFetch("/api/wifi").then(() => "sent", (e) => e.message),
                           await api("/api/command", {method: "POST"}).then(() => "sent", (e) => e.message)]"""
        )
        assert all(r.startswith("remote: no /api/") for r in refused), refused
        assert len(reqs) == n
        assert secret not in pg.content()
        energy = json.loads(pg.evaluate("localStorage.getItem('oc.remote.energy')"))
        assert energy and energy[-1]["solar_wh"] == 123.4 and energy[-1]["boot"] == 7
    own = urlsplit(site).netloc
    for url in reqs:
        u = urlsplit(url)
        assert u.netloc in (own, urlsplit(ttn.base).netloc), url
        assert not (u.netloc == own and u.path.startswith("/api/")), url
    assert any("/packages/storage/uplink_message" in p for _, p, _ in ttn.calls)


def test_remote_refuses_an_unknown_version(site, ttn, error_gated_page):
    _, secret = ttn.make_key("read")
    now = datetime.now(UTC)
    ttn.add_uplink(DEV, 3, port3(), now - timedelta(minutes=5))
    ttn.add_uplink(
        DEV, 2, port2(version=99), now - timedelta(minutes=2)
    )  # not data: the badge keeps port 3's age
    with error_gated_page(
        site, init_script=_init(ttn, secret), service_workers="block", locale="de-DE"
    ) as pg:
        expect(pg.locator("#conn")).to_contain_text("App aktualisieren")
        expect(pg.locator("#status")).to_have_text("LoRa · vor 5 Min")


@pytest.mark.parametrize(
    "over",
    [
        {"cluster": "https://evil.example"},  # a URL in the cluster: refused
        {"cluster": "evil.example/x"},
        {"base": "https://evil.example"},  # the seam only ever points at 127.0.0.1
        {"base": "http://127.0.0.1.evil.example:80"},
        # router-like hosts: not under the page's connect-src domains, so never a key's destination
        {"cluster": "192.168.1.1", "base": None},
        {"cluster": "fritz.box", "base": None},
        {"cluster": "calictl-esp.local", "base": None},
        {"cluster": "thethings.network.evil.example", "base": None},
    ],
)
def test_remote_config_cannot_point_the_keys_elsewhere(site, ttn, error_gated_page, over):
    """The read key goes only to ``https://<validated host>`` (or the test's 127.0.0.1 fake)."""
    _, secret = ttn.make_key("read")
    reqs = []
    with error_gated_page(
        site, init_script=_init(ttn, secret, **over), requests=reqs, service_workers="block"
    ) as pg:
        base = pg.evaluate("() => { const c = remoteCfg(); return c && remoteBase(c); }")
        expect(pg.locator("#status")).to_contain_text("LoRa")
    assert base in (None, "https://" + CLUSTER), base
    if over.get("cluster"):
        assert base is None, base
    assert not any(h in u for u in reqs for h in ("evil", "192.168.", "fritz.box", ".local")), reqs


def test_remote_energy_points_are_spaced(site, ttn, error_gated_page):
    """A van that reboots often adds at most one energy point per minute (oc.remote.energy stays bounded)."""
    _, secret = ttn.make_key("read")
    now = datetime.now(UTC)
    for i in range(5):  # five boots, 10 s apart
        frame = bytearray(port2())
        frame[1:3] = (10 + i).to_bytes(2, "big")
        ttn.add_uplink(DEV, 2, bytes(frame), now - timedelta(seconds=60 - 10 * i))
    with error_gated_page(site, init_script=_init(ttn, secret), service_workers="block") as pg:
        expect(pg.locator("#status")).to_contain_text("LoRa ·")
        energy = json.loads(pg.evaluate("localStorage.getItem('oc.remote.energy')"))
    assert len(energy) == 1, energy


def test_stalled_read_times_out_and_polling_goes_on(site, ttn, error_gated_page):
    """TTS accepts the storage read and never answers: the read ends as a timeout (ttn.js, 20 s; shortened here
    through AbortSignal.timeout) and the next poll is answered, instead of one stalled request blocking every
    later poll."""
    _, secret = ttn.make_key("read")
    ttn.add_uplink(DEV, 2, port2(), datetime.now(UTC) - timedelta(minutes=5))
    ttn.stall_reads = 1
    short = "const _to = AbortSignal.timeout.bind(AbortSignal); AbortSignal.timeout = (ms) => _to(Math.min(ms, 500));"
    with error_gated_page(site, init_script=short + _init(ttn, secret), service_workers="block") as pg:
        pg.clock.install()
        pg.wait_for_timeout(1500)  # the first read stalled, then timed out (real time)
        expect(pg.locator("#status")).not_to_contain_text("5 min ago")
        pg.clock.fast_forward("05:30")  # the next poll (REMOTE_POLL_MS)
        expect(pg.locator(".summary")).to_contain_text("11 / 29 l")
