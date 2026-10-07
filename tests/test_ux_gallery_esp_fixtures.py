"""The ESP page fixtures behind the docs screenshots keep the firmware handlers' JSON shape.

``tools.ux_gallery --esp`` renders ``docs/screenshots/esp-setup-page.png`` / ``esp-status-page.png``
from a stdlib stub serving canned ``/api/state`` + ``/api/wifi`` JSON. These tests pin the canned key
sets to the ones ``tests/firmware/test_web_handlers.py`` asserts of the real C handlers (``web.c``)
— one shared copy, ``tests/firmware/api_shape.py`` — and check the stub serves the exact generated page bytes the firmware embeds — so the figures in
``docs/howto-esp-wifi-setup.md`` cannot drift from what the device serves. Stdlib only (no browser).

.. test:: The ESP docs-screenshot fixtures match the firmware API shape
   :id: T_FW_ESP_SCREENSHOT_FIXTURES
   :links: R_FW_HTTP_STATUS
"""

import json
import urllib.request

import pytest

from tests.firmware.api_shape import (
    AP_KEYS,
    CONTROL_KEYS,
    DEVICE_KEYS,
    LINK_KEYS,
    PAIRING_KEYS,
    STATE_KEYS,
    WIFI_GET_KEYS,
    WIFI_KEYS,
)
from tools import ux_gallery


@pytest.fixture(scope="module")
def fixtures():
    return ux_gallery.esp_fixtures()


@pytest.mark.parametrize("mode", ["setup", "station", "satellite"])
def test_state_fixture_shape(fixtures, mode):
    state = fixtures[mode]["/api/state"]
    assert set(state) == STATE_KEYS
    d = state["device"]
    assert set(d) == DEVICE_KEYS
    assert set(d["pairing"]) == PAIRING_KEYS
    assert set(d["link"]) == LINK_KEYS
    assert set(d["control"]) == CONTROL_KEYS and d["control"]["writes"] is (mode != "setup")
    assert set(d["wifi"]) == WIFI_KEYS and d["wifi"]["mode"] == ("station" if mode == "satellite" else mode)
    assert d["uptime_ms"] == state["t"]


@pytest.mark.parametrize("mode", ["setup", "station", "satellite"])
def test_wifi_fixture_shape(fixtures, mode):
    w = fixtures[mode]["/api/wifi"]
    assert set(w) == WIFI_GET_KEYS
    assert {k: w[k] for k in WIFI_KEYS} == fixtures[mode]["/api/state"]["device"]["wifi"]
    for ap in w["scan"]:
        assert set(ap) == AP_KEYS


def test_station_fn_is_codec_decoded(fixtures):
    from calictl import overrides, protocol

    funcs = protocol.load()
    overrides.apply(funcs)
    fn = fixtures["station"]["/api/state"]["fn"]
    assert list(fn) == sorted(fn) and fn  # CODEC_CHARS (sorted) order
    for name, fields in fn.items():
        placed = {f.name for f in funcs[name].state_fields if f.placed}
        assert set(fields) == placed  # a whole frame, decoded


def test_stub_serves_like_the_firmware(fixtures):
    from tools import gen_c_dict

    with open(ux_gallery.ESP_PAGE, "rb") as f:
        page = f.read()
    with open(ux_gallery.APP_BUNDLE_HEADER, encoding="utf-8") as f:
        bundle = gen_c_dict.header_array_bytes(f.read())
    with ux_gallery.EspStub("station") as stub:
        with urllib.request.urlopen(stub.base + "/", timeout=5) as r:  # urllib does not gunzip
            assert r.headers["Content-Encoding"] == "gzip" and r.read() == bundle
        with urllib.request.urlopen(stub.base + "/device", timeout=5) as r:
            assert r.read() == page
        for path in ("/api/state", "/api/wifi"):
            with urllib.request.urlopen(stub.base + path, timeout=5) as r:
                assert json.load(r) == json.loads(json.dumps(fixtures["station"][path]))
        stub.mode = "setup"
        with urllib.request.urlopen(stub.base + "/", timeout=5) as r:
            assert r.headers["Content-Encoding"] is None and r.read() == page
