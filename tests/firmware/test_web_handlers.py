"""cali_web: the firmware's HTTP endpoints and status/setup page, on the portable HTTP core.

``firmware/components/cali_core/web.c`` answers ``/``, ``/api/state``, ``/api/wifi`` (GET/POST/
DELETE) and, in setup mode, the OS captive-portal probes. Driven here through ``test/web_cli.c``:
the real ``web.c`` + ``http_core.c`` + ``json.c`` + ``snapshot.c`` + ``status.c`` + ``csrc/codec.c`` over a
scripted socket, with fakes for the session (a cooler + a roof frame), runner, transport, kv store,
clock, log and the WiFi runtime. Every assertion is on the bytes the core sent.

.. test:: Web handlers: /api/state shape and atomicity, /api/wifi validation, probes, page
   :id: T_FW_WEB_HANDLERS
   :links: R_FW_HTTP_STATUS
"""

import gzip
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tools import gen_c_dict
from tools.wifi_consts import CONSTS

from .api_shape import (
    AP_KEYS,
    CONTROL_KEYS,
    DEVICE_KEYS,
    LINK_KEYS,
    PAIRING_API_KEYS,
    PAIRING_KEYS,
    STATE_KEYS,
    WIFI_GET_KEYS,
    WIFI_KEYS,
)

ROOT = Path(__file__).resolve().parents[2]
CORE = ROOT / "firmware" / "components" / "cali_core"
PAGE = ROOT / "firmware" / "web" / "index_gen.html"
BUNDLE = gen_c_dict.header_array_bytes(
    (ROOT / "firmware" / "web" / "app_bundle_gen.h").read_text(encoding="utf-8")
)
IDENTITY = "C0:FF:EE:CA:11:F0"
PROBES = [
    "/generate_204",
    "/gen_204",
    "/hotspot-detect.html",
    "/library/test/success.html",
    "/connecttest.txt",
    "/ncsi.txt",
    "/canonical.html",
    "/success.txt",
]


def _build(tmp_path_factory, name, *defines):
    cc = shutil.which("cc") or pytest.skip("no C compiler")
    out = tmp_path_factory.mktemp("web") / name
    subprocess.run(
        [
            cc,
            "-std=c99",
            "-Wall",
            "-Wextra",
            "-Werror",
            *defines,
            "-I",
            str(CORE / "include"),
            "-I",
            str(ROOT / "csrc"),
            "-I",
            str(ROOT / "firmware" / "components" / "platform" / "include"),
            "-I",
            str(ROOT / "firmware" / "web"),
            str(CORE / "web.c"),
            str(CORE / "http_core.c"),
            str(CORE / "captive_dns.c"),
            str(CORE / "json.c"),
            str(CORE / "snapshot.c"),
            str(CORE / "status.c"),
            str(ROOT / "csrc" / "codec.c"),
            str(CORE / "test" / "web_cli.c"),
            "-o",
            str(out),
        ],
        check=True,
    )
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
    proc = subprocess.run(
        [str(cli)], input=b"\n".join(lines) + b"\n", capture_output=True, check=True, timeout=30
    )
    assert proc.stderr == b"init=0\n"
    out, resps, other = proc.stdout, [], []
    while out:
        line, _, out = out.partition(b"\n")
        if line.startswith(b"RESP "):
            n = int(line[5:])
            resps.append(Resp(out[:n]))
            assert out[n : n + 1] == b"\n"
            out = out[n + 1 :]
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
    body = json.loads(
        get(
            web_cli,
            "/api/state",
            setup=[
                "now 5000",
                "stamp 4000",
                "active 1",
                "linkup 1",
                "bond 1",
                "wifi online minsel 192.168.1.23 -61",
            ],
        )
    )
    assert set(body) == STATE_KEYS and body["fn"]["cooler"]["Installed"] == 1
    assert set(body["device"]) == DEVICE_KEYS
    assert set(body["device"]["pairing"]) == PAIRING_KEYS and set(body["device"]["link"]) == LINK_KEYS
    assert set(body["device"]["wifi"]) == WIFI_KEYS
    assert body["device"]["control"] == {"writes": True} and set(body["device"]["control"]) == CONTROL_KEYS
    assert list(body["fn"]) == ["cooler", "roof"]  # CODEC_CHARS order, frames held only
    assert body["fn"]["cooler"]["Level"] == 3
    assert body["fn"]["roof"] == {"Position": 1, "Installed": 1, "SafetyCounterValid": 0, "InfoPopUp": 0}
    d = body["device"]
    assert d["uptime_ms"] == body["t"] and 5000 < body["t"] < 5100 and d["fw"] == "test"
    assert d["pairing"] == {"state": "idle", "address": IDENTITY}
    assert d["link"] == {"up": True, "last_snap_age_ms": body["t"] - 4000}
    assert d["wifi"] == {"mode": "station", "ssid": "minsel", "ip": "192.168.1.23", "rssi": -61}


# Pinned before the cali_status refactor (Task 2): the device block + /api/wifi bytes must not move.
_PIN_SETUP = [
    "now 5000",
    "stamp 4000",
    "active 1",
    "linkup 1",
    "bond 1",
    "wifi online minsel 192.168.1.23 -61",
]
EXPECTED_DEVICE_TAIL = (
    '"device":{"pairing":{"state":"idle","address":"C0:FF:EE:CA:11:F0"},"link":{"up":true,'
    '"last_snap_age_ms":1010},"wifi":{"mode":"station","ssid":"minsel","ip":"192.168.1.23","rssi":-61},'
    '"control":{"writes":true},"uptime_ms":5010,"fw":"test"}}'
)
EXPECTED_WIFI_BODY = (
    '{"mode":"station","ssid":"minsel","ip":"192.168.1.23","rssi":-61,"last_error":"auth","scan":[]}'
)


def test_api_state_device_block_bytes_are_pinned(web_cli):
    """Pins the exact /api/state device block (the cali_status refactor must not move a byte)."""
    body = get(web_cli, "/api/state", setup=_PIN_SETUP).decode()
    assert body[body.index('"device":') :] == EXPECTED_DEVICE_TAIL


def test_api_wifi_body_bytes_are_pinned(web_cli):
    """/api/wifi shares wifi_members with /api/state; pin its bytes too."""
    assert get(web_cli, "/api/wifi", setup=[_PIN_SETUP[-1], "lastfail auth"]).decode() == EXPECTED_WIFI_BODY


def test_api_state_nulls(web_cli):
    body = json.loads(get(web_cli, "/api/state", setup=["nofn", "wifi setup_ap"]))
    assert body["fn"] == {}
    assert body["device"]["pairing"] == {"state": "idle", "address": None}
    assert body["device"]["link"] == {"up": False, "last_snap_age_ms": None}
    assert body["device"]["wifi"] == {"mode": "setup", "ssid": None, "ip": None, "rssi": None}


def test_api_state_link_up_is_the_real_link_not_the_kept_bond(web_cli):
    """A session stays "active" (keeps/re-establishes a link for a stored bond) through reconnect
    backoff, while the link itself is down. ``link.up`` must report the link, not "active"."""
    body = json.loads(get(web_cli, "/api/state", setup=["active 1", "linkup 0", "bond 1", "stamp 0"]))
    assert body["device"]["link"]["up"] is False


@pytest.mark.parametrize(
    "pair,bond,address",
    [
        ("bonded", 1, IDENTITY),
        ("idle", 1, IDENTITY),
        ("idle", 0, None),
        ("error", 1, IDENTITY),
        ("error", 0, None),
        ("scanning", 1, None),
        ("waiting_passkey", 1, None),
    ],
)
def test_api_state_pairing_address_like_console_status(web_cli, pair, bond, address):
    body = json.loads(get(web_cli, "/api/state", setup=["pair " + pair, "bond %d" % bond]))
    assert body["device"]["pairing"] == {"state": pair, "address": address}


@pytest.mark.parametrize(
    "state,joined,mode",
    [
        ("unprovisioned", 0, "off"),
        ("setup_ap", 0, "setup"),
        ("setup_ap_retrying", 1, "setup"),
        ("online", 1, "station"),
        ("connecting", 1, "station"),
        ("retrying", 1, "station"),
        ("connecting", 0, "off"),
    ],
)
def test_wifi_mode(web_cli, state, joined, mode):
    setup = ["wifi " + state, "joined %d" % joined]
    assert json.loads(get(web_cli, "/api/state", setup=setup))["device"]["wifi"]["mode"] == mode
    assert json.loads(get(web_cli, "/api/wifi", setup=setup))["mode"] == mode


def test_api_state_is_atomic(web_cli):
    # the driver flips the cooler's Level after every poll while the response is still being sent
    # (64 bytes a poll), and once more between requests: every body must parse and hold one whole
    # snapshot, the one of its request
    # (the second half flips only between requests: the first half's flip count per request depends on
    # the body's length in 64-byte polls, so it alone cannot guarantee both levels are seen)
    resps, _ = drive(
        web_cli,
        [b"sendmax 64", b"flipping 1"]
        + [req("GET", "/api/state"), b"flip"] * 10
        + [b"flipping 0"]
        + [req("GET", "/api/state"), b"flip"] * 10,
    )
    assert len(resps) == 20
    levels = set()
    for r in resps:
        assert r.status == 200 and len(r.body) > 3 * 64  # really sent across several polls
        body = r.json()
        levels.add(body["fn"]["cooler"]["Level"])
        assert body["fn"]["cooler"]["Level"] in (3, 4)
    assert levels == {3, 4}  # the flips really happened


def test_api_state_overflow_is_500_and_logged(web_cli_tiny):
    r, other = one(web_cli_tiny, "GET", "/api/state")
    assert r.status == 500 and b"cooler" not in r.body
    assert "LOG http: overflow" in other


# ---- /api/wifi -------------------------------------------------------------------------------


def test_post_wifi_validates_and_stores(web_cli):
    assert post(web_cli, "/api/wifi", '{"ssid":"minsel","psk":"test-psk-1234"}') == (200, {"ok": True})
    assert post(web_cli, "/api/wifi", '{"ssid":"","psk":"test-psk-1234"}') == (
        400,
        {"ok": False, "error": "ssid"},
    )
    assert post(web_cli, "/api/wifi", '{"ssid":"minsel","psk":"short"}') == (
        400,
        {"ok": False, "error": "psk"},
    )
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


@pytest.mark.parametrize(
    "ssid,psk,status,error",
    [
        ("s" * CONSTS["NET_SSID_MAX"], "p" * CONSTS["NET_PSK_MAX"], 200, None),
        ("s" * (CONSTS["NET_SSID_MAX"] + 1), "p" * 8, 400, "ssid"),
        ("s", "p" * CONSTS["NET_PSK_MIN"], 200, None),
        ("s", "p" * (CONSTS["NET_PSK_MIN"] - 1), 400, "psk"),
        ("s", "p" * (CONSTS["NET_PSK_MAX"] + 1), 400, "psk"),
        ("s" * 200, "p" * 200, 400, "ssid"),  # far too long: still ssid, not json
    ],
)
def test_post_wifi_length_bounds(web_cli, ssid, psk, status, error):
    r, other = one(
        web_cli, "POST", "/api/wifi", json.dumps({"ssid": ssid, "psk": psk}, separators=(",", ":"))
    )
    assert r.status == status
    assert r.json() == ({"ok": True} if error is None else {"ok": False, "error": error})
    assert other == (["CALL set_creds [%s] [%s]" % (ssid, psk)] if error is None else [])


@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"x",
        b"{}",
        b"[]",
        b'{"ssid":"a"}',
        b'{"psk":"12345678"}',
        b'{"ssid":"a","psk":"12345678","x":"y"}',
        b'{"ssid":"a","ssid":"b"}',
        b'{"ssid":1,"psk":"12345678"}',
        b'{"ssid":"a","psk":12345678}',
        b'{"ssid":null,"psk":"12345678"}',
        b'{"ssid":"a\\n","psk":"12345678"}',
        b'{"ssid":"a\\u0041","psk":"12345678"}',
        b'{"ssid":"a\nb","psk":"12345678"}',
        b'{"ssid":"a\0b","psk":"12345678"}',
        b'{"ssid":"a","psk":"12345678"}x',
        b'{"ssid":"a","psk":"12345678"',
        b'{"ssid":"a,"psk":"12345678"}',
        b'{"ssid":"a" "psk":"12345678"}',
        b'{"ssid":"a",,"psk":"12345678"}',
        b'{"ssid":"a","psk":"12345678",}',
        b'{ssid:"a","psk":"12345678"}',
        b'{"ssid":"a","psk":"12345678\\',
        b'\0{"ssid":"a","psk":"12345678"}',
        b'{"ssid":"a","psk":"12345678"}\0',
    ],
)
def test_post_wifi_malformed_is_json_error(web_cli, body):
    r, other = one(web_cli, "POST", "/api/wifi", body)
    assert (r.status, r.json()) == (400, {"ok": False, "error": "json"})
    assert other == []


def test_post_wifi_store_failure_is_500_and_creds_not_applied(web_cli):
    r, other = one(
        web_cli, "POST", "/api/wifi", '{"ssid":"minsel","psk":"test-psk-1234"}', setup=["kvfail 1"]
    )
    assert (r.status, r.json()) == (500, {"ok": False, "error": "store"})
    assert not any(line.startswith("CALL") for line in other)


def test_delete_wifi_forgets(web_cli):
    r, other = one(web_cli, "DELETE", "/api/wifi")
    assert (r.status, r.json()) == (200, {"ok": True}) and other == ["CALL forget"]


def test_get_wifi_in_setup_lists_last_scan_and_rescans(web_cli):
    r, other = one(web_cli, "GET", "/api/wifi", setup=["wifi setup_ap", "ap minsel -48 1", "ap cafe -80 0"])
    assert r.status == 200 and other == ["CALL scan_auto"]
    assert set(r.json()) == WIFI_GET_KEYS and all(set(ap) == AP_KEYS for ap in r.json()["scan"])
    assert r.json() == {
        "mode": "setup",
        "ssid": None,
        "ip": None,
        "rssi": None,
        "last_error": None,
        "scan": [
            {"ssid": "minsel", "rssi": -48, "secure": True},
            {"ssid": "cafe", "rssi": -80, "secure": False},
        ],
    }


@pytest.mark.parametrize("reason", ["not_found", "auth", "other"])
def test_get_wifi_reports_the_last_join_failure(web_cli, reason):
    """I1/R23: the setup page says WHY a join failed — ``last_error`` is the runner's last
    ``WACT_LOG_REASON`` (cali_wifi_run_last_fail), in setup and every other mode."""
    for state in ("setup_ap", "online"):
        w = json.loads(get(web_cli, "/api/wifi", setup=["wifi " + state, "lastfail " + reason]))
        assert w["last_error"] == reason


def test_get_wifi_last_error_null_without_a_failure(web_cli):
    assert json.loads(get(web_cli, "/api/wifi", setup=["wifi setup_ap"]))["last_error"] is None
    assert (
        "last_error" not in json.loads(get(web_cli, "/api/state", setup=["lastfail auth"]))["device"]["wifi"]
    )


@pytest.mark.parametrize(
    "pair", ["scanning", "connecting", "waiting_passkey", "pairing", "verifying", "resetting"]
)
def test_get_wifi_leaves_the_ble_pairing_gate_to_the_runner(web_cli, pair):
    """R15: the one BLE-coex scan gate lives in the WiFi runner (cali_wifi_run_scan_auto defers a scan
    while a pairing flow is active — tests/firmware/test_session_fake.py); web.c asks regardless."""
    r, other = one(web_cli, "GET", "/api/wifi", setup=["wifi setup_ap", "pair " + pair])
    assert r.status == 200 and other == ["CALL scan_auto"]


def test_get_wifi_in_station_mode_does_not_scan(web_cli):
    r, other = one(web_cli, "GET", "/api/wifi", setup=["wifi online minsel 10.0.0.7 -55", "joined 1"])
    assert other == [] and r.json() == {
        "mode": "station",
        "ssid": "minsel",
        "ip": "10.0.0.7",
        "rssi": -55,
        "last_error": None,
        "scan": [],
    }


@pytest.mark.parametrize(
    "method,path", [("PUT", "/api/wifi"), ("POST", "/api/state"), ("DELETE", "/api/state")]
)
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


@pytest.mark.parametrize("state,joined", [("online", 1), ("online", 0), ("connecting", 1), ("retrying", 1)])
def test_station_root_serves_the_gzipped_calictl_ui(web_cli, state, joined):
    r, _ = one(web_cli, "GET", "/", setup=["wifi " + state, "joined %d" % joined])
    assert r.status == 200 and r.headers["content-type"] == "text/html; charset=utf-8"
    assert r.headers["content-encoding"] == "gzip" and r.headers["cache-control"] == "no-cache"
    assert r.body == BUNDLE
    assert gzip.decompress(r.body) == gen_c_dict.render_app_bundle().encode("utf-8")


@pytest.mark.parametrize("sendmax", [1000, 777])
def test_station_root_streams_the_real_bundle_across_polls(web_cli, sendmax):
    """The 51 KB bundle leaves the core in ``sendmax``-byte chunks over many polls and reassembles
    byte-exact (gunzips to the rendered document)."""
    r, _ = one(web_cli, "GET", "/", setup=["wifi online", "joined 1", "sendmax %d" % sendmax])
    assert len(BUNDLE) > 40 * sendmax / 2 and r.body == BUNDLE
    assert gzip.decompress(r.body) == gen_c_dict.render_app_bundle().encode("utf-8")


@pytest.mark.parametrize(
    "state,joined", [("setup_ap", 0), ("setup_ap_retrying", 1), ("unprovisioned", 0), ("connecting", 0)]
)
def test_root_outside_station_mode_is_the_setup_page(web_cli, state, joined):
    """Setup hotspot and a setup-flow join (mode "off": connecting, joined 0) keep the setup page at /."""
    r, _ = one(web_cli, "GET", "/", setup=["wifi " + state, "joined %d" % joined])
    assert r.status == 200 and r.headers["content-type"].startswith("text/html")
    assert r.body == PAGE.read_bytes()
    assert "content-encoding" not in r.headers and "cache-control" not in r.headers
    assert b'id="setup"' in r.body and b'id="functions"' in r.body


@pytest.mark.parametrize("state,joined", [("online", 1), ("setup_ap", 0), ("unprovisioned", 0)])
def test_device_is_the_status_page_in_every_mode(web_cli, state, joined):
    r, _ = one(web_cli, "GET", "/device", setup=["wifi " + state, "joined %d" % joined])
    assert r.status == 200 and r.body == PAGE.read_bytes() and "content-encoding" not in r.headers


def test_post_root_is_not_served(web_cli):
    r, _ = one(web_cli, "POST", "/", "", setup=["wifi online", "joined 1"])
    assert r.status == 404


# ---- POST /api/command -------------------------------------------------------------------------
# web_cli.c fakes the control module: "ctl <rc> [reason]" = what cali_ctl_submit answers,
# "ctldone <rc> <polls>" = the done callback fires that many polls into the next request.

STATION = ["wifi online minsel 192.168.1.23 -61"]
CMD = '{"function":"cooler","what":"power","value":"on","confirm":true}'
OK = {"ok": True, "applied": None, "state": None, "error": None, "function": "cooler"}
NOT_STATION = [("setup_ap", 0), ("setup_ap_retrying", 0), ("connecting", 0), ("unprovisioned", 0)]


def submits(other):
    return [line for line in other if line.startswith("CALL submit")]


def test_command_pends_until_the_write_completes(web_cli):
    """POST /api/command answers in calictl's shape (serve.ServeBackend.command) only after the
    sequencer's done callback: ``applied`` is null (no readback check), never true.

    .. test:: POST /api/command answers in calictl's shape after the write completed
       :id: T_FW_COMMAND_API
       :links: R_FW_CONTROL_API
    """
    r, other = one(web_cli, "POST", "/api/command", CMD, setup=[*STATION, "ctl pending", "ctldone ok 3"])
    assert (r.status, r.json()) == (200, OK) and r.headers["content-type"] == "application/json"
    # the same request with the write failing 3 polls in: the answer is 502, so it was NOT given early
    r, _ = one(web_cli, "POST", "/api/command", CMD, setup=[*STATION, "ctl pending", "ctldone failed 3"])
    assert (r.status, r.json()) == (502, {"ok": False, "error": "write_failed"})
    assert submits(other) == [
        "CALL submit [cooler] [power] [on]"
    ]  # 200 only after done: PENDING would be 502


def test_command_answer_waits_many_polls(web_cli):
    """A done callback 300 polls (3 s of driver time) in is still waited for — no idle timeout."""
    r, other = one(web_cli, "POST", "/api/command", CMD, setup=[*STATION, "ctl pending", "ctldone ok 300"])
    assert (r.status, r.json()) == (200, OK)


def test_command_refusal_is_calictls_refused_shape(web_cli):
    reason = "the cooling timer can only be set while the fridge is off (turn the cooler off first)"
    r, _ = one(web_cli, "POST", "/api/command", CMD, setup=[*STATION, "ctl refused " + reason])
    assert (r.status, r.json()) == (
        200,
        {"ok": True, "applied": False, "refused": reason, "state": None, "error": None, "function": "cooler"},
    )


def test_command_elsewhere_is_the_refused_shape_too(web_cli):
    body = '{"function":"roof","what":"open","confirm":true}'
    r, other = one(
        web_cli, "POST", "/api/command", body, setup=[*STATION, "ctl elsewhere Only via buspi or the app"]
    )
    assert (r.status, r.json()) == (
        200,
        {
            "ok": True,
            "applied": False,
            "refused": "Only via buspi or the app",
            "state": None,
            "error": None,
            "function": "roof",
        },
    )
    assert submits(other) == ["CALL submit [roof] [open] []"]


@pytest.mark.parametrize(
    "rc,status,error",
    [
        ("busy", 409, "busy"),
        ("notready", 503, "not_connected"),
        ("bad", 400, "bad_value"),
        ("none", 400, "unknown_control"),
    ],
)
def test_command_immediate_results_map_to_status(web_cli, rc, status, error):
    r, _ = one(web_cli, "POST", "/api/command", CMD, setup=[*STATION, "ctl " + rc])
    assert (r.status, r.json()) == (status, {"ok": False, "error": error})


@pytest.mark.parametrize(
    "rc,status,error", [("failed", 502, "write_failed"), ("timeout", 504, "write_timeout")]
)
def test_command_done_results_map_to_status(web_cli, rc, status, error):
    r, _ = one(web_cli, "POST", "/api/command", CMD, setup=[*STATION, "ctl pending", "ctldone %s 2" % rc])
    assert (r.status, r.json()) == (status, {"ok": False, "error": error})


def test_command_after_a_timeout_the_next_command_starts_fresh(web_cli):
    """A timed-out command (504) leaves nothing behind: the next request is submitted anew and gets
    its own answer (a late ACK only clears the sequencer's in-flight flag; done fires once)."""
    resps, other = drive(
        web_cli,
        [
            *(s.encode() for s in STATION),
            b"ctl pending",
            b"ctldone timeout 2",
            req("POST", "/api/command", CMD),
            b"ctldone ok 1",
            req("POST", "/api/command", CMD),
        ],
    )
    assert [(r.status, r.json()) for r in resps] == [
        (504, {"ok": False, "error": "write_timeout"}),
        (200, OK),
    ]
    assert len(submits(other)) == 2


def test_command_second_request_while_busy_is_409(web_cli):
    """The web core is single-connection, so the sequencer's BUSY (a command, or a timed-out write
    still unacknowledged) is what a second request gets."""
    resps, _ = drive(
        web_cli,
        [
            *(s.encode() for s in STATION),
            b"ctl pending",
            b"ctldone ok 1",
            req("POST", "/api/command", CMD),
            b"ctl busy",
            req("POST", "/api/command", CMD),
        ],
    )
    assert [(r.status, r.json()) for r in resps] == [(200, OK), (409, {"ok": False, "error": "busy"})]


@pytest.mark.parametrize("state,joined", NOT_STATION)
def test_command_refused_outside_station_mode(web_cli, state, joined):
    """Ruling B: no control write over the setup hotspot or during a setup-flow join (wifi.mode
    "setup" or "off") — 403 before anything reaches the control module.

    .. test:: No control write over the setup hotspot or a setup-flow join
       :id: T_FW_COMMAND_STATION_ONLY
       :links: R_FW_CONTROL_API
    """
    r, other = one(
        web_cli, "POST", "/api/command", CMD, setup=["wifi " + state, "joined %d" % joined, "ctl pending"]
    )
    assert (r.status, r.json()) == (403, {"ok": False, "error": "setup_mode"})
    assert not submits(other)


@pytest.mark.parametrize("state,joined", [("connecting", 1), ("retrying", 1)])
def test_command_accepted_while_a_station_reconnects(web_cli, state, joined):
    """mode "station" also covers a joined-before station reconnecting: the gate is the mode."""
    r, other = one(
        web_cli,
        "POST",
        "/api/command",
        CMD,
        setup=["wifi " + state, "joined %d" % joined, "ctl pending", "ctldone ok 1"],
    )
    assert r.status == 200 and len(submits(other)) == 1


@pytest.mark.parametrize(
    "body,status,error",
    [
        ("", 400, "bad_json"),
        ("not json", 400, "bad_json"),
        ("[]", 400, "bad_json"),
        ('{"function":"cooler","what":"power","value":true}', 400, "bad_json"),
        ('{"function":"cooler","what":"power","value":false}', 400, "bad_json"),
        ('{"function":"cooler","what":"power","value":1.5}', 400, "bad_json"),
        ('{"function":"cooler","what":"power","value":1e3}', 400, "bad_json"),
        ('{"function":"cooler","what":"power","value":01}', 400, "bad_json"),
        ('{"function":"cooler","what":"power","value":-}', 400, "bad_json"),
        ('{"function":"cooler","what":"power","value":{}}', 400, "bad_json"),
        ('{"function":"cooler","what":"power","value":[1]}', 400, "bad_json"),
        ('{"function":"cooler","what":"power","x":1}', 400, "bad_json"),
        ('{"function":"cooler","function":"cooler","what":"power"}', 400, "bad_json"),
        ('{"function":"cooler","what":"power","confirm":1}', 400, "bad_json"),
        ('{"function":"cooler","what":"power","confirm":"true"}', 400, "bad_json"),
        ('{"function":"cooler","what":"power",}', 400, "bad_json"),
        ('{"function":"cooler","what":"power"}x', 400, "bad_json"),
        ('{"function":1,"what":"power"}', 400, "bad_json"),
        ('{"function":"%s","what":"power"}' % ("c" * 30), 400, "bad_json"),
        ('{"function":"cooler","what":"%s"}' % ("w" * 40), 400, "bad_json"),
        ('{"function":"cooler","what":"power","value":"%s"}' % ("v" * 64), 400, "bad_json"),
        ('{"function":"cooler","what":"power","value":"a\\nb"}', 400, "bad_json"),
        ('{"function":"lighting","what":"wakeup","local_now":1,"local_now":2}', 400, "bad_json"),
        ('{"function":"lighting","what":"wakeup","local_now":{}}', 400, "bad_json"),
        ('{"function":"lighting","what":"wakeup","local_now":[1]}', 400, "bad_json"),
        ('{"function":"lighting","what":"wakeup","local_now":}', 400, "bad_json"),
        ('{"function":"lighting","what":"wakeup","local_now":1.}', 400, "bad_json"),
        ('{"function":"lighting","what":"wakeup","local_now":"x}', 400, "bad_json"),
        ("{}", 400, "missing_function_or_what"),
        ('{"what":"power"}', 400, "missing_function_or_what"),
        ('{"function":"cooler"}', 400, "missing_function_or_what"),
        ('{"function":"cooler","what":""}', 400, "missing_function_or_what"),
        ('{"function":"airheater","what":"power","value":"on"}', 400, "confirm_required"),
        ('{"function":"airheater","what":"power","value":"on","confirm":false}', 400, "confirm_required"),
        ('{"function":"roof","what":"open"}', 400, "confirm_required"),
    ],
)
def test_command_body_validation(web_cli, body, status, error):
    r, other = one(web_cli, "POST", "/api/command", body, setup=[*STATION, "ctl pending"])
    assert (r.status, r.json()) == (status, {"ok": False, "error": error})
    assert not submits(other)


WAKE = '{"function":"lighting","what":"wakeup","value":"07:00","local_now":%s}'
LN = 1791354615  # 2026-10-07 06:30:15, the page's wall clock read as UTC


def test_local_now_reaches_the_sequencer(web_cli):
    r, other = one(
        web_cli, "POST", "/api/command", WAKE % LN, setup=[*STATION, "ctl pending", "ctldone ok 1"]
    )
    assert r.status == 200 and submits(other) == ["CALL submit [lighting] [wakeup] [07:00] t=%d" % LN]
    # the floor itself (2026-01-01T00:00Z) is a clock
    _, other = one(web_cli, "POST", "/api/command", WAKE % 1767225600, setup=[*STATION, "ctl pending"])
    assert submits(other) == ["CALL submit [lighting] [wakeup] [07:00] t=1767225600"]


def test_local_now_missing_or_null_is_no_clock(web_cli):
    """No local_now (an old cached page, a script) or null: submitted without a clock, so the
    builder refuses with the clock reason in calictl's refused shape (200)."""
    from tools.gen_c_dict import ESP_WAKEUP_CLOCK_REASON

    for body in ('{"function":"lighting","what":"wakeup","value":"07:00"}', WAKE % "null"):
        r, other = one(
            web_cli,
            "POST",
            "/api/command",
            body,
            setup=[*STATION, "ctl elsewhere " + ESP_WAKEUP_CLOCK_REASON],
        )
        assert submits(other) == ["CALL submit [lighting] [wakeup] [07:00]"]
        assert (r.status, r.json()["refused"]) == (200, ESP_WAKEUP_CLOCK_REASON)


@pytest.mark.parametrize(
    "ln",
    [
        '"1791354615"',
        str(1767225599),
        "-5",
        "0",
        "-0",
        "1791354615.0",
        "1.791354615e9",
        "1791354615E0",
        "true",
        "false",
    ],
)
def test_local_now_validation(web_cli, ln):
    """Review focus 5 (ruling R1: no skew check): a string, a fraction/exponent, a bool, or a clock
    before 2026-01-01 -> 400 bad_value; nothing submitted.

    .. test:: A missing or implausible page clock never reaches the wake-up builder
       :id: T_FW_LOCAL_NOW
       :links: R_FW_WAKEUP
    """
    r, other = one(web_cli, "POST", "/api/command", WAKE % ln, setup=[*STATION, "ctl pending"])
    assert (r.status, r.json()) == (400, {"ok": False, "error": "bad_value"}) and not submits(other)


@pytest.mark.parametrize("ln,t", [(str(2**32), 2**32), ("9" * 30, 2**63 - 1)])
def test_local_now_past_the_32_bit_timestamp_reaches_the_builders_bad(web_cli, ln, t):
    """A huge integer is passed on (saturated at int64), never bad_json: the builder answers BAD for
    a clock past the 32-bit Timestamp (vectors u32/i64max), which the client gets as 400 bad_value."""
    r, other = one(web_cli, "POST", "/api/command", WAKE % ln, setup=[*STATION, "ctl bad"])
    assert submits(other) == ["CALL submit [lighting] [wakeup] [07:00] t=%d" % t]
    assert (r.status, r.json()) == (400, {"ok": False, "error": "bad_value"})


def test_a_refusal_after_the_pull_is_calictls_refused_shape(web_cli):
    """The sequencer's done(REFUSED, WAKEUP_UNKNOWN) after the REQUEST_CONFIG pull is answered on the
    resumed request with its reason (Task 4 review Minor-2)."""
    from calictl import control

    r, _ = one(
        web_cli,
        "POST",
        "/api/command",
        WAKE % LN,
        setup=[*STATION, "ctl pending", "ctldone refused 3 " + control.WAKEUP_UNKNOWN],
    )
    assert (r.status, r.json()) == (
        200,
        {
            "ok": True,
            "applied": False,
            "refused": control.WAKEUP_UNKNOWN,
            "state": None,
            "error": None,
            "function": "lighting",
        },
    )


@pytest.mark.parametrize(
    "value,want",
    [
        ("5", "[5]"),
        ("null", "[]"),
        ('"07:00"', "[07:00]"),
        ("-1", "[-1]"),
        ("0", "[0]"),
        ('"%s"' % ("v" * 63), "[%s]" % ("v" * 63)),
        ('" 4 "', "[ 4 ]"),
        ('"3 amber"', "[3 amber]"),
        ('"a\\"b\\\\c"', '[a"b\\c]'),
    ],
)
def test_command_value_forms(web_cli, value, want):
    """Strings pass as decoded text, integers as their decimal text, null as "" (plan decision 5)."""
    body = '{"function":"cooler","what":"level","value":%s}' % value
    r, other = one(web_cli, "POST", "/api/command", body, setup=[*STATION, "ctl pending", "ctldone ok 1"])
    assert r.status == 200 and submits(other) == ["CALL submit [cooler] [level] %s" % want]


def test_command_value_absent_is_empty(web_cli):
    """The UI always sends "value" (possibly null); a body without it means the same as null."""
    _, other = one(
        web_cli,
        "POST",
        "/api/command",
        '{"what":"timer_start","function":"cooler"}',
        setup=[*STATION, "ctl pending", "ctldone ok 1"],
    )
    assert submits(other) == ["CALL submit [cooler] [timer_start] []"]


def test_command_body_whitespace_and_key_order(web_cli):
    body = ' {\r\n "confirm" : true , "value" : 3 , "what":"level", "function" : "cooler" } '
    r, other = one(web_cli, "POST", "/api/command", body, setup=[*STATION, "ctl pending", "ctldone ok 1"])
    assert (r.status, r.json()) == (200, OK) and submits(other) == ["CALL submit [cooler] [level] [3]"]


@pytest.mark.parametrize("method", ["GET", "PUT", "DELETE"])
def test_other_methods_on_command_are_405(web_cli, method):
    r, other = one(web_cli, method, "/api/command", "" if method == "PUT" else None, setup=STATION)
    assert (r.status, r.json()) == (405, {"ok": False, "error": "method"}) and not submits(other)


def test_command_in_setup_mode_is_403_not_a_captive_redirect(web_cli):
    """The setup hotspot's catch-all 302 must not swallow the command route."""
    r, _ = one(web_cli, "POST", "/api/command", CMD, setup=["wifi setup_ap"])
    assert r.status == 403


@pytest.mark.parametrize("state,writes", [("online", True), ("setup_ap", False), ("unprovisioned", False)])
def test_api_state_reports_whether_writes_are_accepted(web_cli, state, writes):
    body = json.loads(get(web_cli, "/api/state", setup=["wifi %s minsel 192.168.1.23 -61" % state]))
    assert body["device"]["control"] == {"writes": writes}


# ---- /api/pairing (the shared wizard on the satellite) ------------------------------------------

PAIR_KEYS_ORDER = ["state", "attempts", "error", "address", "radio_busy"]  # calictl's snapshot dict order
IDLE_BONDED = {"state": "idle", "attempts": 0, "error": None, "address": IDENTITY, "radio_busy": False}


def pair_calls(other):
    return [line for line in other if line.startswith("CALL pair_")]


def test_pairing_get_is_calictls_snapshot(web_cli):
    """GET /api/pairing answers calictl's ``pairing_snapshot()`` shape, key for key; ``radio_busy``
    is always false (no co-resident scanner on the ESP).

    .. test:: GET/POST /api/pairing answer in calictl's shape with calictl's error codes
       :id: T_FW_PAIRING_API
       :links: R_FW_PAIRING_WIZARD
    """
    r, other = one(web_cli, "GET", "/api/pairing", setup=["bond 1"])
    assert r.status == 200 and r.headers["content-type"] == "application/json"
    assert r.json() == IDLE_BONDED and list(r.json()) == PAIR_KEYS_ORDER
    assert set(r.json()) == PAIRING_API_KEYS
    assert not pair_calls(other)


def test_pairing_get_reports_attempts_and_error(web_cli):
    r, _ = one(web_cli, "GET", "/api/pairing", setup=["pair error", "attempts 3", "pairerr pairing_failed"])
    assert r.json() == {
        "state": "error",
        "attempts": 3,
        "error": "pairing_failed",
        "address": None,
        "radio_busy": False,
    }


@pytest.mark.parametrize(
    "body,call",
    [
        ({"action": "start"}, "CALL pair_start"),
        ({"action": "passkey", "value": "012345"}, "CALL pair_passkey 012345"),
        ({"action": "cancel"}, "CALL pair_cancel"),
        ({"action": "reset", "confirm": True}, "CALL pair_forget"),
    ],
)
def test_pairing_post_actions_reach_the_runner_and_answer_the_snapshot(web_cli, body, call):
    """Each action is the console's runner call (pair / passkey N / forget, plus cancel); the answer
    is the post-action snapshot, like calictl's (the wizard renders the next step off it)."""
    r, other = one(web_cli, "POST", "/api/pairing", json.dumps(body), setup=["bond 1"])
    assert (r.status, r.json()) == (200, IDLE_BONDED)
    assert pair_calls(other) == [call]


@pytest.mark.parametrize(
    "body,error",
    [
        ("not json", "bad_json"),
        ('{"action":"start"', "bad_json"),
        ('{"action":"start","extra":1}', "bad_json"),
        ('{"action":"start","action":"cancel"}', "bad_json"),
        ('{"action":5}', "bad_json"),
        ('{"action":"start","confirm":"yes"}', "bad_json"),
        ("{}", "bad_action"),
        ('{"action":"pair"}', "bad_action"),
        ('{"action":"startstartstartstartstart"}', "bad_action"),
        ('{"action":"reset"}', "confirm_required"),
        ('{"action":"reset","confirm":false}', "confirm_required"),
        ('{"action":"passkey"}', "bad_passkey"),
        ('{"action":"passkey","value":null}', "bad_passkey"),
        ('{"action":"passkey","value":123456}', "bad_passkey"),
        ('{"action":"passkey","value":"12345"}', "bad_passkey"),
        ('{"action":"passkey","value":"1234567"}', "bad_passkey"),
        ('{"action":"passkey","value":"12a456"}', "bad_passkey"),
        ('{"action":"passkey","value":" 123456"}', "bad_passkey"),
    ],
)
def test_pairing_post_validation_is_web_pys(web_cli, body, error):
    """web.py's checks in its order: bad_action, confirm_required, bad_passkey (a 6-digit STRING),
    plus the ESP's fixed-shape parser (bad_json); nothing reaches the runner."""
    r, other = one(web_cli, "POST", "/api/pairing", body)
    assert (r.status, r.json()) == (400, {"error": error})
    assert not pair_calls(other)


@pytest.mark.parametrize(
    "body,status",
    [
        ({"action": "start"}, 409),
        ({"action": "reset", "confirm": True}, 409),
        ({"action": "cancel"}, 200),
        ({"action": "passkey", "value": "123456"}, 200),
    ],
)
def test_pairing_start_or_reset_while_a_command_runs_is_busy(web_cli, body, status):
    """The single link is in use by a control command: start/reset answer 409 busy and reach no
    runner call; cancel and passkey (no new link) still go through.

    .. test:: Pairing start/reset are refused while a control command is pending
       :id: T_FW_PAIRING_BUSY
       :links: R_FW_PAIRING_WIZARD
    """
    r, other = one(web_cli, "POST", "/api/pairing", json.dumps(body), setup=["ctlbusy 1"])
    assert r.status == status
    if status == 409:
        assert r.json() == {"error": "busy"} and not pair_calls(other)
    else:
        assert len(pair_calls(other)) == 1


@pytest.mark.parametrize(
    "state,joined",
    [
        ("online", 1),
        ("retrying", 1),
        ("setup_ap", 0),
        ("setup_ap_retrying", 1),
        ("connecting", 0),
        ("unprovisioned", 0),
    ],
)
def test_pairing_allowed_in_every_wifi_mode(web_cli, state, joined):
    """Pairing is connection management, not a control write: also over the setup hotspot (first
    setup from a phone at the van) and during a setup-flow join — never a captive redirect.

    .. test:: The pairing endpoint works over the setup hotspot too
       :id: T_FW_PAIRING_HOTSPOT
       :links: R_FW_PAIRING_WIZARD
    """
    setup = ["wifi " + state, "joined %d" % joined]
    r, _ = one(web_cli, "GET", "/api/pairing", setup=setup)
    assert r.status == 200 and r.json()["state"] == "idle"
    r, other = one(web_cli, "POST", "/api/pairing", '{"action":"start"}', setup=setup)
    assert r.status == 200 and pair_calls(other) == ["CALL pair_start"]


@pytest.mark.parametrize("method", ["PUT", "DELETE"])
def test_other_methods_on_pairing_are_405(web_cli, method):
    r, other = one(web_cli, method, "/api/pairing", setup=["wifi setup_ap"])
    assert (r.status, r.json()) == (405, {"ok": False, "error": "method"})
    assert not pair_calls(other)


@pytest.mark.parametrize(
    "state,joined", [("online", 1), ("setup_ap", 0), ("connecting", 0), ("unprovisioned", 0)]
)
def test_app_is_the_calictl_ui_in_every_mode(web_cli, state, joined):
    """GET /app serves the UI bundle in every WiFi mode, so a phone on the setup hotspot reaches the
    wizard (/ stays the setup page there)."""
    r, _ = one(web_cli, "GET", "/app", setup=["wifi " + state, "joined %d" % joined])
    assert r.status == 200 and r.headers["content-encoding"] == "gzip" and r.body == BUNDLE
