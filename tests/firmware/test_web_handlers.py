"""cali_web: the firmware's HTTP endpoints and status/setup page, on the portable HTTP core.

``firmware/components/cali_core/web.c`` answers ``/``, ``/api/state``, ``/api/wifi`` (GET/POST/
DELETE) and, in setup mode, the OS captive-portal probes. Driven here through ``test/web_cli.c``:
the real ``web.c`` + ``http_core.c`` + ``json.c`` + ``snapshot.c`` + ``csrc/codec.c`` over a
scripted socket, with fakes for the session (a cooler + a roof frame), runner, transport, kv store,
clock, log and the WiFi runtime. Every assertion is on the bytes the core sent.

.. test:: Web handlers: /api/state shape and atomicity, /api/wifi validation, probes, page
   :id: T_FW_WEB_HANDLERS
   :links: R_FW_HTTP_STATUS
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tools.wifi_consts import CONSTS

ROOT = Path(__file__).resolve().parents[2]
CORE = ROOT / "firmware" / "components" / "cali_core"
PAGE = ROOT / "firmware" / "web" / "index_gen.html"
IDENTITY = "C0:FF:EE:CA:11:F0"
PROBES = ["/generate_204", "/gen_204", "/hotspot-detect.html", "/library/test/success.html",
          "/connecttest.txt", "/ncsi.txt", "/canonical.html", "/success.txt"]


def _build(tmp_path_factory, name, *defines):
    cc = shutil.which("cc") or pytest.skip("no C compiler")
    out = tmp_path_factory.mktemp("web") / name
    subprocess.run([cc, "-std=c99", "-Wall", "-Wextra", "-Werror", "-DCODEC_NO_ENCODE", *defines,
                    "-I", str(CORE / "include"), "-I", str(ROOT / "csrc"),
                    "-I", str(ROOT / "firmware" / "components" / "platform" / "include"),
                    "-I", str(ROOT / "firmware" / "web"),
                    str(CORE / "web.c"), str(CORE / "http_core.c"), str(CORE / "captive_dns.c"),
                    str(CORE / "json.c"), str(CORE / "snapshot.c"), str(ROOT / "csrc" / "codec.c"),
                    str(CORE / "test" / "web_cli.c"), "-o", str(out)], check=True)
    return out


@pytest.fixture(scope="module")
def web_cli(tmp_path_factory):
    return _build(tmp_path_factory, "web_cli")


@pytest.fixture(scope="module")
def web_cli_tiny(tmp_path_factory):
    """The same driver with a 64-byte JSON buffer, so /api/state overflows."""
    return _build(tmp_path_factory, "web_cli_tiny", "-DCALI_WEB_JSON_MAX=64")


def _esc(b):
    return b.replace(b"\\", b"\\\\").replace(b"\r", b"\\r").replace(b"\n", b"\\n").replace(b"\0", b"\\0")


class Resp:
    def __init__(self, raw):
        head, _, self.body = raw.partition(b"\r\n\r\n")
        lines = head.decode("latin-1").split("\r\n")
        self.status = int(lines[0].split(" ")[1])
        self.headers = {k.lower(): v for k, _, v in (ln.partition(": ") for ln in lines[1:])}
        assert int(self.headers["content-length"]) == len(self.body)

    def json(self):
        return json.loads(self.body)


def drive(cli, lines):
    """Run the driver on script ``lines``; returns ``(responses, other output lines)``."""
    proc = subprocess.run([str(cli)], input=b"\n".join(lines) + b"\n", capture_output=True, check=True,
                          timeout=30)
    assert proc.stderr == b"init=0\n"
    out, resps, other = proc.stdout, [], []
    while out:
        line, _, out = out.partition(b"\n")
        if line.startswith(b"RESP "):
            n = int(line[5:])
            resps.append(Resp(out[:n]))
            assert out[n:n + 1] == b"\n"
            out = out[n + 1:]
        else:
            other.append(line.decode())
    return resps, other


def req(method, path, body=None):
    text = "%s %s HTTP/1.1\r\nHost: x\r\n" % (method, path)
    b = text.encode()
    if body is not None:
        body = body.encode() if isinstance(body, str) else body
        b += b"Content-Length: %d\r\n\r\n" % len(body) + body
    else:
        b += b"\r\n"
    return b"req " + _esc(b)


def one(cli, method, path, body=None, setup=()):
    resps, other = drive(cli, [s.encode() for s in setup] + [req(method, path, body)])
    assert len(resps) == 1
    return resps[0], other


def get(cli, path, setup=()):
    r, _ = one(cli, "GET", path, setup=setup)
    assert r.status == 200 and r.headers["content-type"] == "application/json"
    return r.body


def post(cli, path, body, setup=()):
    r, _ = one(cli, "POST", path, body, setup=setup)
    return r.status, r.json()


def kv(cli, key, body='{"ssid":"minsel","psk":"test-psk-1234"}'):
    _, other = drive(cli, [req("POST", "/api/wifi", body), b"kv " + key.encode()])
    return next(line[3:] for line in other if line.startswith("KV "))


# ---- /api/state ------------------------------------------------------------------------------

def test_api_state_shape(web_cli):
    body = json.loads(get(web_cli, "/api/state", setup=["now 5000", "stamp 4000", "active 1", "bond 1",
                                                        "wifi online minsel 192.168.1.23 -61"]))
    assert set(body) == {"t", "fn", "device"} and body["fn"]["cooler"]["Installed"] == 1
    assert set(body["device"]) == {"pairing", "link", "wifi", "uptime_ms", "fw"}
    assert list(body["fn"]) == ["cooler", "roof"]                # CODEC_CHARS order, frames held only
    assert body["fn"]["cooler"]["Level"] == 3
    assert body["fn"]["roof"] == {"Position": 1, "Installed": 1, "SafetyCounterValid": 0, "InfoPopUp": 0}
    d = body["device"]
    assert d["uptime_ms"] == body["t"] and 5000 < body["t"] < 5100 and d["fw"] == "test"
    assert d["pairing"] == {"state": "idle", "address": IDENTITY}
    assert d["link"] == {"up": True, "last_snap_age_ms": body["t"] - 4000}
    assert d["wifi"] == {"mode": "station", "ssid": "minsel", "ip": "192.168.1.23", "rssi": -61}


def test_api_state_nulls(web_cli):
    body = json.loads(get(web_cli, "/api/state", setup=["nofn", "wifi setup_ap"]))
    assert body["fn"] == {}
    assert body["device"]["pairing"] == {"state": "idle", "address": None}
    assert body["device"]["link"] == {"up": False, "last_snap_age_ms": None}
    assert body["device"]["wifi"] == {"mode": "setup", "ssid": None, "ip": None, "rssi": None}


@pytest.mark.parametrize("pair,bond,address", [("bonded", 1, IDENTITY), ("idle", 1, IDENTITY),
                                               ("idle", 0, None), ("scanning", 1, None),
                                               ("waiting_passkey", 1, None)])
def test_api_state_pairing_address_like_console_status(web_cli, pair, bond, address):
    body = json.loads(get(web_cli, "/api/state", setup=["pair " + pair, "bond %d" % bond]))
    assert body["device"]["pairing"] == {"state": pair, "address": address}


@pytest.mark.parametrize("state,joined,mode", [
    ("unprovisioned", 0, "off"), ("setup_ap", 0, "setup"), ("setup_ap_retrying", 1, "setup"),
    ("online", 1, "station"), ("connecting", 1, "station"), ("retrying", 1, "station"),
    ("connecting", 0, "off")])
def test_wifi_mode(web_cli, state, joined, mode):
    setup = ["wifi " + state, "joined %d" % joined]
    assert json.loads(get(web_cli, "/api/state", setup=setup))["device"]["wifi"]["mode"] == mode
    assert json.loads(get(web_cli, "/api/wifi", setup=setup))["mode"] == mode


def test_api_state_is_atomic(web_cli):
    # the driver flips the cooler's Level after every poll while the response is still being sent
    # (64 bytes a poll), and once more between requests: every body must parse and hold one whole
    # snapshot, the one of its request
    resps, _ = drive(web_cli, [b"sendmax 64", b"flipping 1"] + [req("GET", "/api/state"), b"flip"] * 20)
    assert len(resps) == 20
    levels = set()
    for r in resps:
        assert r.status == 200 and len(r.body) > 3 * 64           # really sent across several polls
        body = r.json()
        levels.add(body["fn"]["cooler"]["Level"])
        assert body["fn"]["cooler"]["Level"] in (3, 4)
    assert levels == {3, 4}                                      # the flips really happened


def test_api_state_overflow_is_500_and_logged(web_cli_tiny):
    r, other = one(web_cli_tiny, "GET", "/api/state")
    assert r.status == 500 and b"cooler" not in r.body
    assert "LOG http: overflow" in other


# ---- /api/wifi -------------------------------------------------------------------------------

def test_post_wifi_validates_and_stores(web_cli):
    assert post(web_cli, "/api/wifi", '{"ssid":"minsel","psk":"test-psk-1234"}') == (200, {"ok": True})
    assert post(web_cli, "/api/wifi", '{"ssid":"","psk":"test-psk-1234"}') == (400, {"ok": False, "error": "ssid"})
    assert post(web_cli, "/api/wifi", '{"ssid":"minsel","psk":"short"}') == (400, {"ok": False, "error": "psk"})
    assert kv(web_cli, "wifi_ssid") == "minsel"
    assert kv(web_cli, "wifi_psk") == "test-psk-1234"


def test_post_wifi_hands_creds_to_the_runtime(web_cli):
    r, other = one(web_cli, "POST", "/api/wifi", '{"ssid":"minsel","psk":"test-psk-1234"}')
    assert r.status == 200 and r.headers["content-type"] == "application/json"
    assert other == ["CALL set_creds [minsel] [test-psk-1234]"]


def test_post_wifi_escapes_whitespace_and_key_order(web_cli):
    body = ' { "psk" : "pa\\"ss\\\\word" ,\r\n "ssid":"caf\u00e9 \\\\x" } '
    r, other = one(web_cli, "POST", "/api/wifi", body)
    assert (r.status, r.json()) == (200, {"ok": True})
    assert other == ['CALL set_creds [caf\u00e9 \\x] [pa"ss\\word]']


@pytest.mark.parametrize("ssid,psk,status,error", [
    ("s" * CONSTS["NET_SSID_MAX"], "p" * CONSTS["NET_PSK_MAX"], 200, None),
    ("s" * (CONSTS["NET_SSID_MAX"] + 1), "p" * 8, 400, "ssid"),
    ("s", "p" * CONSTS["NET_PSK_MIN"], 200, None),
    ("s", "p" * (CONSTS["NET_PSK_MIN"] - 1), 400, "psk"),
    ("s", "p" * (CONSTS["NET_PSK_MAX"] + 1), 400, "psk"),
    ("s" * 200, "p" * 200, 400, "ssid"),                        # far too long: still ssid, not json
])
def test_post_wifi_length_bounds(web_cli, ssid, psk, status, error):
    r, other = one(web_cli, "POST", "/api/wifi", json.dumps({"ssid": ssid, "psk": psk}, separators=(",", ":")))
    assert r.status == status
    assert r.json() == ({"ok": True} if error is None else {"ok": False, "error": error})
    assert other == (["CALL set_creds [%s] [%s]" % (ssid, psk)] if error is None else [])


@pytest.mark.parametrize("body", [
    b"", b"x", b"{}", b"[]", b'{"ssid":"a"}', b'{"psk":"12345678"}',
    b'{"ssid":"a","psk":"12345678","x":"y"}', b'{"ssid":"a","ssid":"b"}',
    b'{"ssid":1,"psk":"12345678"}', b'{"ssid":"a","psk":12345678}', b'{"ssid":null,"psk":"12345678"}',
    b'{"ssid":"a\\n","psk":"12345678"}', b'{"ssid":"a\\u0041","psk":"12345678"}',
    b'{"ssid":"a\nb","psk":"12345678"}', b'{"ssid":"a\0b","psk":"12345678"}',
    b'{"ssid":"a","psk":"12345678"}x', b'{"ssid":"a","psk":"12345678"', b'{"ssid":"a,"psk":"12345678"}',
    b'{"ssid":"a" "psk":"12345678"}', b'{"ssid":"a",,"psk":"12345678"}', b'{"ssid":"a","psk":"12345678",}',
    b'{ssid:"a","psk":"12345678"}', b'{"ssid":"a","psk":"12345678\\', b'\0{"ssid":"a","psk":"12345678"}',
    b'{"ssid":"a","psk":"12345678"}\0',
])
def test_post_wifi_malformed_is_json_error(web_cli, body):
    r, other = one(web_cli, "POST", "/api/wifi", body)
    assert (r.status, r.json()) == (400, {"ok": False, "error": "json"})
    assert other == []


def test_post_wifi_store_failure_is_500_and_creds_not_applied(web_cli):
    r, other = one(web_cli, "POST", "/api/wifi", '{"ssid":"minsel","psk":"test-psk-1234"}', setup=["kvfail 1"])
    assert (r.status, r.json()) == (500, {"ok": False, "error": "store"})
    assert not any(line.startswith("CALL") for line in other)


def test_delete_wifi_forgets(web_cli):
    r, other = one(web_cli, "DELETE", "/api/wifi")
    assert (r.status, r.json()) == (200, {"ok": True}) and other == ["CALL forget"]


def test_get_wifi_in_setup_lists_last_scan_and_rescans(web_cli):
    r, other = one(web_cli, "GET", "/api/wifi", setup=["wifi setup_ap", "ap minsel -48 1", "ap cafe -80 0"])
    assert r.status == 200 and other == ["CALL scan"]
    assert r.json() == {"mode": "setup", "ssid": None, "ip": None, "rssi": None,
                        "scan": [{"ssid": "minsel", "rssi": -48, "secure": True},
                                 {"ssid": "cafe", "rssi": -80, "secure": False}]}


@pytest.mark.parametrize("pair", ["scanning", "connecting", "waiting_passkey", "pairing", "verifying", "resetting"])
def test_get_wifi_never_scans_during_ble_pairing(web_cli, pair):
    r, other = one(web_cli, "GET", "/api/wifi", setup=["wifi setup_ap", "pair " + pair])
    assert r.status == 200 and other == []


def test_get_wifi_in_station_mode_does_not_scan(web_cli):
    r, other = one(web_cli, "GET", "/api/wifi", setup=["wifi online minsel 10.0.0.7 -55", "joined 1"])
    assert other == [] and r.json() == {"mode": "station", "ssid": "minsel", "ip": "10.0.0.7", "rssi": -55,
                                        "scan": []}


@pytest.mark.parametrize("method,path", [("PUT", "/api/wifi"), ("POST", "/api/state"), ("DELETE", "/api/state")])
def test_wrong_method_is_405(web_cli, method, path):
    r, other = one(web_cli, method, path, "" if method != "DELETE" else None)
    assert (r.status, r.json()) == (405, {"ok": False, "error": "method"}) and other == []


# ---- captive portal + routing ----------------------------------------------------------------

@pytest.mark.parametrize("state", ["setup_ap", "setup_ap_retrying"])
@pytest.mark.parametrize("probe", PROBES)
def test_setup_mode_redirects_probes(web_cli, state, probe):
    r, _ = one(web_cli, "GET", probe, setup=["wifi " + state])
    assert r.status == 302 and r.headers["location"] == "http://%s/" % CONSTS["NET_AP_ADDR"]


def test_setup_mode_redirects_unknown_paths_home(web_cli):
    for path in ("/nope", "/index.html", "/api/nope?x=1"):
        r, _ = one(web_cli, "GET", path, setup=["wifi setup_ap"])
        assert r.status == 302 and r.headers["location"] == "/"


@pytest.mark.parametrize("state,joined", [("online", 1), ("connecting", 1), ("unprovisioned", 0)])
def test_station_mode_404(web_cli, state, joined):
    for path in ["/nope", *PROBES]:
        r, _ = one(web_cli, "GET", path, setup=["wifi " + state, "joined %d" % joined])
        assert r.status == 404


@pytest.mark.parametrize("state", ["online", "setup_ap"])
def test_page_served(web_cli, state):
    r, _ = one(web_cli, "GET", "/", setup=["wifi " + state])
    assert r.status == 200 and r.headers["content-type"].startswith("text/html")
    assert r.body == PAGE.read_bytes()
    assert b'id="setup"' in r.body and b'id="functions"' in r.body
