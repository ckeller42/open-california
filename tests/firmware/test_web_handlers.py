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
    BATTERY_KEYS,
    CONTROL_KEYS,
    DEVICE_KEYS,
    DISPLAY_KEYS,
    LINK_KEYS,
    OTA_KEYS,
    PAIRING_API_KEYS,
    PAIRING_KEYS,
    RES_KEYS,
    SD_KEYS,
    SENSOR_CFG_KEYS,
    SENSOR_SEEN_KEYS,
    SENSORS_KEYS,
    STATE_KEYS,
    TIME_KEYS,
    WIFI_GET_KEYS,
    WIFI_KEYS,
)
from .test_sensors import KEY

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
            str(CORE / "ota.c"),
            str(CORE / "time.c"),
            str(CORE / "sdlog.c"),
            str(CORE / "diag.c"),
            str(CORE / "auto_camper.c"),
            str(CORE / "auto_camper_run.c"),
            str(CORE / "display_cfg.c"),
            str(CORE / "battery.c"),
            str(CORE / "sensors.c"),
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
        assert int(self.headers.get("content-length", 0)) == len(self.body)

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


def req(method, path, body=None, headers=""):
    text = "%s %s HTTP/1.1\r\nHost: x\r\n%s" % (method, path, headers)
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
    assert body["device"]["ota"] == {
        "state": "idle",
        "available_sha": None,
        "progress_pct": 0,
        "error": None,
        "channel": "stable",
    }
    assert set(body["device"]["ota"]) == OTA_KEYS
    assert body["device"]["time"] == {
        "source": "none",
        "synced": False,
        "boot": 0,
        "up": 5010,
    }  # no cali_time_init
    assert set(body["device"]["time"]) == TIME_KEYS
    # no card on the host (cali_sdlog_init never called), no resource stats (web.c's weak stub)
    assert body["device"]["sd"] == {
        "state": "absent",
        "total_mb": None,
        "free_mb": None,
        "last_seq": None,
        "dropped": 0,
    }
    assert set(body["device"]["sd"]) == SD_KEYS
    assert body["device"]["res"] == dict.fromkeys(RES_KEYS)
    assert body["device"]["battery"] == dict.fromkeys(
        BATTERY_KEYS
    )  # no power chip on the host (R_FW_BATTERY)
    # no screen on the host: the live members null, the default settings (R_FW_DISPLAY_WAKE)
    assert body["device"]["display"] == dict(
        dict.fromkeys(DISPLAY_KEYS), auto=True, prox_wake=True, motion_wake=True, min=8, max=100, offset=0
    )
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
    '"control":{"writes":true},"uptime_ms":5010,"fw":"test",'
    '"ota":{"state":"idle","available_sha":null,"progress_pct":0,"error":null,"channel":"stable"},'
    '"time":{"source":"none","synced":false,"boot":0,"up":5010},'
    '"sd":{"state":"absent","total_mb":null,"free_mb":null,"last_seq":null,"dropped":0},'
    '"res":{"heap_free_kb":null,"heap_min_kb":null,"psram_free_kb":null,"cpu_pct":null},'
    '"battery":{"pct":null,"mv":null,"usb":null,"charging":null,"runtime_min":null},'
    '"auto_camper":{"enabled":true,"state":"idle"},'
    '"display":{"state":null,"lux":null,"prox":null,"motion":null,"brightness_pct":null,"sensor":null,'
    '"auto":true,"prox_wake":true,"motion_wake":true,"min":8,"max":100,"offset":0,"dfs":null,"pm":null},'
    '"ux":{"enabled":true},'
    '"experimental":{"sensors":false},'
    '"water_held":false}}'
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


def test_api_state_water_held_reflects_the_session(web_cli):
    """device.water_held mirrors cali_session_water_held() — the shared UI flags the tank stale."""
    assert json.loads(get(web_cli, "/api/state", setup=["waterheld 1"]))["device"]["water_held"] is True
    assert json.loads(get(web_cli, "/api/state"))["device"]["water_held"] is False


def test_api_state_water_held_age_only_when_held_and_adopted_this_boot(web_cli):
    """device.water_held_age_ms = uptime - the baseline's adopt time, only while the water is held
    AND the baseline was adopted on this boot (an NVS-restored baseline has no time: omitted)."""
    dev = json.loads(get(web_cli, "/api/state", setup=["now 5000", "waterheld 1", "watergood 4000"]))[
        "device"
    ]
    assert dev["water_held_age_ms"] == 1010  # uptime 5010 (the poll ticks +10) - 4000
    assert "water_held_age_ms" not in json.loads(get(web_cli, "/api/state", setup=["waterheld 1"]))["device"]
    live = json.loads(get(web_cli, "/api/state", setup=["watergood 4000"]))["device"]
    assert "water_held_age_ms" not in live


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
    assert r.headers["etag"] == etag(BUNDLE)
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
    assert "content-encoding" not in r.headers and r.headers["cache-control"] == "no-cache"
    assert r.headers["etag"] == etag(r.body)
    assert b'id="setup"' in r.body and b'id="device"' in r.body


@pytest.mark.parametrize("state,joined", [("online", 1), ("setup_ap", 0), ("unprovisioned", 0)])
def test_device_is_the_setup_page_in_every_mode(web_cli, state, joined):
    r, _ = one(web_cli, "GET", "/device", setup=["wifi " + state, "joined %d" % joined])
    assert r.status == 200 and r.body == PAGE.read_bytes() and "content-encoding" not in r.headers


# ---- "Add to Home Screen": the manifest + icons the bundle links (pwa_assets_gen.h) ---------------
PWA = gen_c_dict.pwa_assets()
MODES = [("online", 1), ("setup_ap", 0), ("unprovisioned", 0), ("connecting", 0)]


@pytest.mark.parametrize("state,joined", MODES)
@pytest.mark.parametrize("path,ctype,data", PWA, ids=[p for p, _, _ in PWA])
def test_pwa_assets_served_in_every_mode(web_cli, state, joined, path, ctype, data):
    r, _ = one(web_cli, "GET", path, setup=["wifi " + state, "joined %d" % joined])
    assert r.status == 200 and r.headers["content-type"] == ctype
    cc = "max-age=86400" if ctype.startswith("image/") else "no-cache"  # manifest + sw.js revalidate
    assert r.headers["cache-control"] == cc and "content-encoding" not in r.headers
    assert r.headers["etag"] == etag(data) and r.body == data


# ---- HTTP caching: a strong ETag on every static asset, If-None-Match -> 304 -------------------


def etag(data):
    """web.c's ETag: the quoted FNV-1a 64 of the exact bytes served."""
    h = 0xCBF29CE484222325
    for b in data:
        h = ((h ^ b) * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return '"%016x"' % h


def cond(cli, path, inm, setup=("wifi online", "joined 1")):
    resps, _ = drive(
        cli, [s.encode() for s in setup] + [req("GET", path, headers="If-None-Match: %s\r\n" % inm)]
    )
    return resps[0]


STATIC = ["/", "/app", "/device", "/manifest.webmanifest", "/sw.js", "/favicon.ico", "/icon-192.png"]


@pytest.mark.parametrize("path", STATIC)
def test_static_matching_if_none_match_is_304_without_body(web_cli, path):
    r, _ = one(web_cli, "GET", path, setup=["wifi online", "joined 1"])
    tag = r.headers["etag"]
    for inm in (tag, "W/" + tag, '"stale", ' + tag, "*"):
        nm = cond(web_cli, path, inm)
        assert nm.status == 304 and nm.body == b"", inm
        assert nm.headers["etag"] == tag and nm.headers["cache-control"] == r.headers["cache-control"]
        assert "content-length" not in nm.headers and "content-type" not in nm.headers


@pytest.mark.parametrize("path", STATIC)
def test_static_stale_if_none_match_is_200_with_body(web_cli, path):
    r = cond(web_cli, path, '"0000000000000000"')
    assert r.status == 200 and r.body and r.headers["etag"] == etag(r.body)


def test_root_etag_follows_the_mode_bytes(web_cli):
    """/ serves the bundle in station mode and the setup page elsewhere: a tag from one never
    validates the other."""
    r, _ = one(web_cli, "GET", "/", setup=["wifi online", "joined 1"])
    s = cond(web_cli, "/", r.headers["etag"], setup=("wifi setup_ap", "joined 0"))
    assert s.status == 200 and s.body == PAGE.read_bytes()


def test_api_state_is_never_cached(web_cli):
    r = cond(web_cli, "/api/state", "*")
    assert r.status == 200 and "etag" not in r.headers and r.headers["cache-control"] == "no-store"


def test_pwa_icons_are_the_calictl_webui_files():
    webui = ROOT / "calictl" / "webui"
    for path, _, data in PWA[1:]:
        assert data == (webui / path.lstrip("/")).read_bytes(), path


def test_pwa_manifest_lists_only_served_icons_and_opens_the_ui():
    src = json.loads((ROOT / "calictl" / "webui" / "manifest.webmanifest").read_text(encoding="utf-8"))
    man = json.loads(PWA[0][2])
    served = {p for p, _, _ in PWA}
    assert man["icons"] and all(i["src"] in served for i in man["icons"])
    assert {i["purpose"] for i in man["icons"]} == {"any", "maskable"}
    assert man["start_url"] == "/app" and man["scope"] == "/"  # /app = the UI in every WiFi mode
    assert {k: v for k, v in man.items() if k not in ("icons", "start_url")} == {
        k: v for k, v in src.items() if k not in ("icons", "start_url")
    }


def test_served_assets_are_exactly_the_bundle_links_plus_the_manifest_icons():
    import re

    links = set(re.findall(r'<link\b[^>]*\bhref="([^"]+)"', gen_c_dict.render_app_bundle()))
    icons = {i["src"] for i in json.loads(PWA[0][2])["icons"]}
    assert links | icons | {"/sw.js"} == {p for p, _, _ in PWA}


def test_service_worker_is_served_for_the_offline_shell(web_cli):
    """/sw.js: the calictl webui file byte-for-byte, as JavaScript, revalidated every load.

    .. test:: The satellite serves the offline shell's service worker
       :id: T_FW_SW_SERVED
       :links: R_UI_PWA_OFFLINE
    """
    r, _ = one(web_cli, "GET", "/sw.js", setup=["wifi online", "joined 1"])
    data = (ROOT / "calictl" / "webui" / "sw.js").read_bytes()
    assert r.status == 200 and r.body == data and r.headers["content-type"] == "text/javascript"
    assert r.headers["cache-control"] == "no-cache" and r.headers["etag"] == etag(data)


def test_pwa_post_is_not_served(web_cli):
    r, _ = one(web_cli, "POST", "/manifest.webmanifest", "", setup=["wifi online", "joined 1"])
    assert r.status == 404


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


def test_command_unconfirmed_answers_ok_flagged(web_cli):
    """A write that left before the link dropped is not a failure: the unit may have actuated it
    (#264). 200 with ``applied`` null and ``unconfirmed`` true, so the page confirms from state."""
    r, _ = one(web_cli, "POST", "/api/command", CMD, setup=[*STATION, "ctl pending", "ctldone unconfirmed 2"])
    assert (r.status, r.json()) == (200, {**OK, "unconfirmed": True})


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
    """Each action is the console's runner call (pair / passkey N / forget, plus cancel — here
    mid-flow, where cancel acts); the answer is the post-action snapshot, like calictl's (the wizard
    renders the next step off it)."""
    r, other = one(web_cli, "POST", "/api/pairing", json.dumps(body), setup=["bond 1", "pair scanning"])
    assert r.status == 200 and r.json()["state"] == "scanning"
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
        ({"action": "passkey", "value": "123456"}, 200),
    ],
)
def test_pairing_start_or_reset_while_a_command_runs_is_busy(web_cli, body, status):
    """The single link is in use by a control command: start/reset answer 409 busy and reach no
    runner call; passkey (no new link; the runner ignores it outside waiting_passkey) goes through.
    A cancel then is a no-op (no flow runs while a command does), see ``test_cancel_outside_a_flow_is_a_no_op``.

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


@pytest.mark.parametrize(
    "state,acts",
    [
        ("scanning", True),
        ("connecting", True),
        ("pairing", True),
        ("waiting_passkey", True),
        ("verifying", True),
        ("idle", False),
        ("bonded", False),
        ("error", False),
        ("resetting", False),
    ],
)
def test_cancel_outside_a_flow_is_a_no_op(web_cli, state, acts):
    """cancel ends a running flow only: from bonded/idle/error/resetting it changes nothing (200 with
    the unchanged snapshot), so a stale cancel from a 1 s-old snapshot can never drop a live bonded
    link or the control command riding on it (calictl's poll never depends on it either)."""
    r, other = one(
        web_cli, "POST", "/api/pairing", '{"action":"cancel"}', setup=["bond 1", "pair " + state, "ctlbusy 1"]
    )
    assert r.status == 200 and r.json()["state"] == state
    assert pair_calls(other) == (["CALL pair_cancel"] if acts else [])


# ---- POST /api/ota (R_FW_OTA) -----------------------------------------------------------------


def ota_wakes(other):
    return [ln for ln in other if ln == "CALL ota_wake"]


@pytest.mark.parametrize("action", ["check", "install"])
def test_ota_starts_on_the_ota_task_and_shows_in_state(web_cli, action):
    """The request only starts the OTA task (``wake``) and answers at once; device.ota says checking.

    .. test:: POST /api/ota: station mode only, shape and codes, progress in device.ota
       :id: T_FW_OTA_API
       :links: R_FW_OTA
    """
    resps, other = drive(
        web_cli,
        [
            *(s.encode() for s in STATION),
            b"otainit",
            req("POST", "/api/ota", '{"action":"%s"}' % action),
            req("GET", "/api/state"),
            req("POST", "/api/ota", '{"action":"check"}'),
        ],
    )
    assert (resps[0].status, resps[0].json()) == (200, {"ok": True})
    assert resps[1].json()["device"]["ota"]["state"] == "checking"
    assert (resps[2].status, resps[2].json()) == (409, {"ok": False, "error": "busy"})
    assert len(ota_wakes(other)) == 1


@pytest.mark.parametrize("body", ['{"action":"install"}', '{"channel":"latest"}'])
@pytest.mark.parametrize("state,joined", NOT_STATION)
def test_ota_refused_outside_station_mode(web_cli, state, joined, body):
    """Like /api/command: never over the setup hotspot or a setup-flow join (a channel change neither)."""
    r, other = one(
        web_cli,
        "POST",
        "/api/ota",
        body,
        setup=["wifi " + state, "joined %d" % joined, "otainit"],
    )
    assert (r.status, r.json()) == (403, {"ok": False, "error": "setup_mode"})
    assert not ota_wakes(other)


@pytest.mark.parametrize(
    "method,body,status,error",
    [
        ("GET", None, 405, "method"),
        ("POST", "", 400, "bad_json"),
        ("POST", '{"action":"install","x":1}', 400, "bad_json"),
        ("POST", '{"action":"update"}', 400, "bad_action"),
        ("POST", "{}", 400, "bad_action"),
        ("POST", '{"channel":"beta"}', 400, "bad_value"),
        ("POST", '{"channel":1}', 400, "bad_json"),
        ("POST", '{"action":"check","channel":"stable"}', 400, "bad_json"),
    ],
)
def test_ota_errors(web_cli, method, body, status, error):
    r, other = one(web_cli, method, "/api/ota", body, setup=[*STATION, "otainit"])
    assert (r.status, r.json()) == (status, {"ok": False, "error": error})
    assert not ota_wakes(other)


def test_ota_channel_persists_and_shows_in_state(web_cli):
    rs, other = drive(
        web_cli,
        [
            *(s.encode() for s in STATION),
            b"otainit",
            req("POST", "/api/ota", '{"channel":"latest"}'),
            req("GET", "/api/state"),
            b"kv ota_channel",
        ],
    )
    assert (rs[0].status, rs[0].json()) == (200, {"ok": True})
    assert rs[1].json()["device"]["ota"]["channel"] == "latest"
    assert "KV \x01" in other and not ota_wakes(other)  # a channel change never starts the OTA task
    rs, _ = drive(
        web_cli,
        [b"kvhex ota_channel 01", *(s.encode() for s in STATION), b"otainit", req("GET", "/api/state")],
    )
    assert rs[0].json()["device"]["ota"]["channel"] == "latest"
    # the host/QEMU build has no OTA ops but still stores the choice
    r, _ = one(web_cli, "POST", "/api/ota", '{"channel":"latest"}', setup=STATION)
    assert (r.status, r.json()) == (200, {"ok": True})


def test_ota_channel_store_failure(web_cli):
    r, _ = one(web_cli, "POST", "/api/ota", '{"channel":"latest"}', setup=STATION + ["kvfail 1"])
    assert (r.status, r.json()) == (500, {"ok": False, "error": "store"})


def test_ota_unavailable_without_an_ota_task(web_cli):
    """The host build and the QEMU image have no OTA I/O: 503, never a hang."""
    r, _ = one(web_cli, "POST", "/api/ota", '{"action":"check"}', setup=STATION)
    assert (r.status, r.json()) == (503, {"ok": False, "error": "ota_unavailable"})


# ---- /api/auto_camper (R_FW_AUTO_CAMPER) -------------------------------------------------------


def test_auto_camper_toggle_defaults_on_persists_and_shows_in_state(web_cli):
    """Default ON; ``{"enabled":false}`` answers serve's shape, lands in the kv store (``ac_on``) and
    in ``device.auto_camper``; on again restores it.

    .. test:: POST /api/auto_camper: default on, persisted, station mode only, in device.auto_camper
       :id: T_FW_AUTO_CAMPER_API
       :links: R_FW_AUTO_CAMPER
    """
    resps, other = drive(
        web_cli,
        [
            *(s.encode() for s in STATION),
            req("GET", "/api/state"),
            req("POST", "/api/auto_camper", '{"enabled": false}'),
            b"kv ac_on",
            req("GET", "/api/state"),
            req("POST", "/api/auto_camper", '{"enabled":true}'),
            b"kv ac_on",
        ],
    )
    assert resps[0].json()["device"]["auto_camper"] == {"enabled": True, "state": "idle"}
    assert (resps[1].status, resps[1].json()) == (200, {"ok": True, "auto_camper": False})
    assert resps[2].json()["device"]["auto_camper"] == {"enabled": False, "state": "idle"}
    assert (resps[3].status, resps[3].json()) == (200, {"ok": True, "auto_camper": True})
    assert [ln for ln in other if ln.startswith("KV ")] == ["KV ", "KV \x01"]  # 0 / 1 (%.*s stops at the NUL)
    assert "LOG auto-camper: disabled" in other and "LOG auto-camper: ENABLED" in other


def test_auto_camper_reenable_clears_a_stale_debt(web_cli):
    """Off in NVS but a debt still stored (an earlier off whose state write failed): turning it on
    clears the debt, persisted; a failed debt write stores no toggle (500, ``ac_on`` unchanged)."""
    stale = "01010101010001020202"  # owe, pre usb+lights, window open, prevs ign off / camping on
    resps, other = drive(
        web_cli,
        [
            *(s.encode() for s in STATION),
            b"kvhex ac_on 00",
            ("kvhex ac_state " + stale).encode(),
            b"acinit",
            req("GET", "/api/state"),
            b"kvfail 1",
            req("POST", "/api/auto_camper", '{"enabled":true}'),
            b"kvfail 0",
            b"kv ac_on",
            req("POST", "/api/auto_camper", '{"enabled":true}'),
            b"acinit",
            req("GET", "/api/state"),
        ],
    )
    assert resps[0].json()["device"]["auto_camper"] == {"enabled": False, "state": "restoring"}
    assert (resps[1].status, resps[1].json()) == (500, {"ok": False, "error": "store"})
    assert [ln for ln in other if ln.startswith("KV ")] == ["KV "]  # ac_on still 0 after the failed write
    assert (resps[2].status, resps[2].json()) == (200, {"ok": True, "auto_camper": True})
    assert resps[3].json()["device"]["auto_camper"] == {"enabled": True, "state": "idle"}  # after a "reboot"
    assert "LOG auto-camper: off (restore owed)" in other and other[-1] == "LOG auto-camper: on"


@pytest.mark.parametrize(
    "setup,method,body,status,error",
    [
        (STATION, "GET", None, 405, "method"),
        (STATION, "POST", "", 400, "bad_json"),
        (STATION, "POST", '{"enabled":1}', 400, "bad_json"),
        (STATION, "POST", '{"enabledX":true}', 400, "bad_json"),
        (STATION, "POST", '{"enabled":true,"x":1}', 400, "bad_json"),
        (STATION + ["kvfail 1"], "POST", '{"enabled":false}', 500, "store"),
        (["wifi setup_ap"], "POST", '{"enabled":false}', 403, "setup_mode"),
    ],
)
def test_auto_camper_errors(web_cli, setup, method, body, status, error):
    r, _ = one(web_cli, method, "/api/auto_camper", body, setup=setup)
    assert (r.status, r.json()) == (status, {"ok": False, "error": error})


# ---- /api/ux + /api/ux_setting (R_UX_TELEMETRY) ---------------------------------------------------

UX = {
    "sid": "3f9a1c0b7d2e4a65",
    "ua_class": "ios",
    "standalone": True,
    "origin": "lan",
    "events": [
        {"type": "view", "screen": "cooler"},
        {"type": "control", "fn": "cooler", "control": "power"},
        {"type": "timing", "ttfb": 120, "dcl": 340, "tiles": 560},
        {"type": "error", "msg": 'TypeError: "x" is undefined \\ @ app.js:123'},
    ],
}
UX_STAMP = {"seq": 1, "boot": 0, "up": "<int>", "ts": None, "tsrc": None}
UX_COMMON = {"ev": "ux", "sid": UX["sid"], "ua_class": "ios", "standalone": True, "origin": "lan"}


def _sd(other):
    """The SD text records the driver printed; ``up`` (the fake clock) as ``"<int>"``."""
    recs = [json.loads(ln[7:]) for ln in other if ln.startswith("SDTEXT ")]
    for r in recs:
        assert isinstance(r["up"], int)
        r["up"] = "<int>"
    return recs


def test_ux_beacon_writes_one_sd_record_per_event(web_cli):
    """A valid beacon -> 204 and one ``{"ev":"ux"}`` SD record per event, its keys in canonical order;
    switched off on /device -> 204 and nothing recorded, persisted as ``ux_on`` and in device.ux.

    .. test:: ESP POST /api/ux: validated, one SD record per event, station only, off switch -> 204 no record
       :id: T_UX_ESP_API
       :links: R_UX_TELEMETRY
    """
    resps, other = drive(
        web_cli,
        [
            *(s.encode() for s in STATION),
            b"sdcard",
            req("POST", "/api/ux", json.dumps(UX)),
            req("POST", "/api/ux_setting", '{"enabled":false}'),
            b"kv ux_on",
            req("GET", "/api/state"),
            req("POST", "/api/ux", json.dumps(UX)),
            req("POST", "/api/ux", "not json"),  # off: not even validated
            req("POST", "/api/ux_setting", '{"enabled":true}'),
            req("GET", "/api/state"),
        ],
    )
    assert [r.status for r in resps] == [204, 200, 200, 204, 204, 200, 200]
    assert resps[0].body == b""
    assert resps[1].json() == {"ok": True, "ux": False} and resps[5].json() == {"ok": True, "ux": True}
    assert resps[2].json()["device"]["ux"] == {"enabled": False}
    assert resps[6].json()["device"]["ux"] == {"enabled": True}
    assert [ln for ln in other if ln.startswith("KV ")] == ["KV "]  # 0 (%.*s stops at the NUL)
    recs = _sd(other)
    assert recs == [{**UX_STAMP, **UX_COMMON, **e} for e in UX["events"]]
    assert [list(r)[10:] for r in recs] == [
        ["type", "screen"],
        ["type", "fn", "control"],
        ["type", "ttfb", "dcl", "tiles"],
        ["type", "msg"],
    ]


def test_ux_long_error_is_cut_to_fit_the_sd_record(web_cli):
    """A 200-char message full of escapes is cut (never dropped) so the record fits
    CALI_SDLOG_DATA_MAX; the line stays valid JSON."""
    msg = '"\\' * 100  # 200 chars, 400 escaped
    resps, other = drive(
        web_cli,
        [
            *(s.encode() for s in STATION),
            b"sdcard",
            req("POST", "/api/ux", json.dumps(_ev(type="error", msg=msg))),
        ],
    )
    assert resps[0].status == 204
    (rec,) = _sd(other)
    assert rec["type"] == "error" and 0 < len(rec["msg"]) < 200 and msg.startswith(rec["msg"])


def test_ux_any_member_order_and_whitespace(web_cli):
    body = (
        '{ "events" : [ {"screen":"home" , "type":"view"} ] , "origin":"buspi","standalone":false,'
        '"ua_class":"other","sid":"0123456789abcdef0123456789abcdef" }'
    )
    resps, other = drive(web_cli, [*(s.encode() for s in STATION), b"sdcard", req("POST", "/api/ux", body)])
    assert resps[0].status == 204
    assert _sd(other) == [
        {
            **UX_STAMP,
            "ev": "ux",
            "sid": "0123456789abcdef0123456789abcdef",
            "ua_class": "other",
            "standalone": False,
            "origin": "buspi",
            "type": "view",
            "screen": "home",
        }
    ]


def _with(**kw):
    return {**UX, **kw}


def _ev(**e):
    return _with(events=[e])


@pytest.mark.parametrize(
    "body",
    [
        "not json",
        "[]",
        "{}",
        json.dumps(_with(sid="XYZXYZXYZ")),
        json.dumps(_with(sid="abc")),
        json.dumps(_with(ua_class="windows")),
        json.dumps(_with(origin="internet")),
        json.dumps(_with(standalone=1)),
        json.dumps(_with(events=[])),
        json.dumps(_with(events=[{"type": "view", "screen": "home"}] * 17)),
        json.dumps(_with(extra=1)),
        json.dumps({k: v for k, v in UX.items() if k != "origin"}),
        json.dumps(UX)[:-1] + ',"sid":"abcdef12"}',  # a member twice
        json.dumps(_ev(type="view")),
        json.dumps(_ev(type="view", screen="home", value=3)),  # never a value
        json.dumps(_ev(type="control", fn="cooler", control="level", value="7")),
        json.dumps(_ev(type="view", screen="Cooler")),
        json.dumps(_ev(type="view", screen="x" * 33)),
        json.dumps(_ev(type="timing", ttfb=-1, dcl=0, tiles=0)),
        json.dumps(_ev(type="timing", ttfb=True, dcl=0, tiles=0)),
        json.dumps(_ev(type="timing", ttfb=1.5, dcl=0, tiles=0)),
        json.dumps(_ev(type="timing", ttfb=600001, dcl=0, tiles=0)),
        json.dumps(_ev(type="timing", ttfb=0o1, dcl=0, tiles=0)).replace('"ttfb": 1', '"ttfb": 01'),
        json.dumps(_ev(type="error", msg="x" * 201)),
        json.dumps(_ev(type="error", msg="line\nbreak")),
        json.dumps(_ev(type="error", msg="")),
        json.dumps(_ev(type="error", msg="caf\u00e9")),
        json.dumps(_ev(type="click", id="x")),
        json.dumps(UX) + "x",
    ],
)
def test_ux_rejects_anything_but_the_shape(web_cli, body):
    """The same rejections as calictl's web.ux_batch (tests/test_web.py), and nothing recorded —
    not even the valid events before the bad one."""
    resps, other = drive(web_cli, [*(s.encode() for s in STATION), b"sdcard", req("POST", "/api/ux", body)])
    assert (resps[0].status, resps[0].json()) == (400, {"error": "bad_ux"})
    assert _sd(other) == []


def test_ux_half_bad_beacon_records_nothing(web_cli):
    bad = _with(events=[{"type": "view", "screen": "home"}, {"type": "view", "screen": "BAD"}])
    resps, other = drive(
        web_cli, [*(s.encode() for s in STATION), b"sdcard", req("POST", "/api/ux", json.dumps(bad))]
    )
    assert resps[0].status == 400 and _sd(other) == []


@pytest.mark.parametrize(
    "setup,method,body,status,error",
    [
        (STATION, "GET", None, 405, {"ok": False, "error": "method"}),
        (["wifi setup_ap"], "POST", json.dumps(UX), 403, {"ok": False, "error": "setup_mode"}),
        (STATION, "POST", "x" * 2100, 413, None),  # the core's 2 KB request buffer
    ],
)
def test_ux_errors(web_cli, setup, method, body, status, error):
    r, other = one(web_cli, method, "/api/ux", body, setup=[*setup, "sdcard"])
    assert r.status == status and _sd(other) == []
    if error:
        assert r.json() == error


@pytest.mark.parametrize(
    "setup,method,body,status,error",
    [
        (STATION, "GET", None, 405, "method"),
        (STATION, "POST", '{"enabled":1}', 400, "bad_json"),
        (STATION, "POST", '{"enabled":true,"x":1}', 400, "bad_json"),
        (STATION + ["kvfail 1"], "POST", '{"enabled":false}', 500, "store"),
        (["wifi setup_ap"], "POST", '{"enabled":false}', 403, "setup_mode"),
    ],
)
def test_ux_setting_errors(web_cli, setup, method, body, status, error):
    r, _ = one(web_cli, method, "/api/ux_setting", body, setup=setup)
    assert (r.status, r.json()) == (status, {"ok": False, "error": error})


def test_api_state_has_experimental_off_by_default(web_cli):
    """The sensor track switch is off on a fresh store.

    .. test:: device.experimental defaults off; POST /api/experimental stores and logs it
       :id: T_FW_EXPERIMENTAL
       :links: R_FW_EXPERIMENTAL
    """
    d = json.loads(get(web_cli, "/api/state", setup=STATION))["device"]
    assert d["experimental"] == {"sensors": False}


def test_experimental_switch_persists_and_shows_in_state(web_cli):
    rs, other = drive(
        web_cli,
        [
            *(s.encode() for s in STATION),
            req("POST", "/api/experimental", '{"sensors":true}'),
            req("GET", "/api/state"),
            b"kv exp_sensors",
        ],
    )
    assert (rs[0].status, rs[0].json()) == (200, {"ok": True, "sensors": True})
    assert json.loads(rs[1].body)["device"]["experimental"] == {"sensors": True}
    assert "KV \x01" in other
    assert any(l.startswith("LOG exp: sensors on") for l in other)
    # a fresh process with the byte stored reads it back
    rs, _ = drive(
        web_cli, [b"kvhex exp_sensors 01", *(s.encode() for s in STATION), req("GET", "/api/state")]
    )
    assert json.loads(rs[0].body)["device"]["experimental"] == {"sensors": True}


@pytest.mark.parametrize(
    "setup,method,body,status,error",
    [
        (STATION, "GET", None, 405, "method"),
        (STATION, "POST", '{"sensors":1}', 400, "bad_json"),
        (STATION, "POST", '{"sensors":true,"x":1}', 400, "bad_json"),
        (STATION + ["kvfail 1"], "POST", '{"sensors":false}', 500, "store"),
        (["wifi setup_ap"], "POST", '{"sensors":false}', 403, "setup_mode"),
    ],
)
def test_experimental_errors(web_cli, setup, method, body, status, error):
    r, _ = one(web_cli, method, "/api/experimental", body, setup=setup)
    assert (r.status, r.json()) == (status, {"ok": False, "error": error})


# ---- /api/sensors (R_FW_SENSORS) ----------------------------------------------------------------

GOVEE_SEEN = [
    "sensadv A4:C1:38:00:00:02 1 -70 GVH5074_0002 88ec005c09d8165a02"
]  # web_cli.c: cali_sensors_on_adv


def test_sensors_404_disabled_while_flag_off(web_cli):
    """Flag off: both methods 404, and /api/state carries no ``ext``.

    .. test:: /api/sensors: the flag gate, the shapes, add/rename/remove and every error code
       :id: T_FW_SENSORS_API
       :links: R_FW_SENSORS
    """
    for m, body in (("GET", None), ("POST", '{"remove":1}')):
        r, _ = one(web_cli, m, "/api/sensors", body, setup=STATION)
        assert (r.status, r.json()) == (404, {"ok": False, "error": "disabled"})
    assert "ext" not in json.loads(get(web_cli, "/api/state", setup=STATION))


def test_sensors_get_seen_add_remove_roundtrip(web_cli):
    on = ["kvhex exp_sensors 01", *STATION, *GOVEE_SEEN]
    rs, _ = drive(web_cli, [*(s.encode() for s in on), req("GET", "/api/sensors")])
    api = rs[0].json()
    sid = api["seen"][0]["id"]
    assert set(api) == SENSORS_KEYS and set(api["seen"][0]) == SENSOR_SEEN_KEYS
    assert api["scan"] == "ok" and api["configured"] == []
    rs, _ = drive(web_cli, [*(s.encode() for s in on), req("POST", "/api/sensors", '{"add":%d,"alias":"kitchen"}' % sid),
                            *(GOVEE_SEEN[0].encode(),), req("GET", "/api/state"), req("POST", "/api/sensors", '{"remove":%d}' % sid)])  # fmt: skip
    c = rs[0].json()["configured"]
    assert set(c[0]) == SENSOR_CFG_KEYS and set(json.loads(rs[1].body)) == STATE_KEYS | {"ext"}
    assert c == [
        {"id": sid, "type": "govee", "alias": "kitchen", "last_seen_s": None, "value": None, "has_key": False}
    ]
    assert json.loads(rs[1].body)["ext"] == {
        "kitchen": {"temp_c": 23.96, "humidity": 58.48, "battery": 90, "age_s": 0}
    }
    assert rs[2].json()["configured"] == []


def test_sensors_get_kicks_and_post_works_in_setup_mode(web_cli):
    on = ["kvhex exp_sensors 01", "wifi setup_ap", *GOVEE_SEEN]
    rs, _ = drive(web_cli, [*(s.encode() for s in on), req("GET", "/api/sensors")])
    sid = rs[0].json()["seen"][0]["id"]
    rs, _ = drive(web_cli, [*(s.encode() for s in on), req("POST", "/api/sensors", '{"add":%d}' % sid),
                            req("POST", "/api/sensors", '{"alias":"porch","id":%d}' % sid), req("PUT", "/api/sensors", "{}")])  # fmt: skip
    assert rs[0].status == 200 and [c["alias"] for c in rs[1].json()["configured"]] == ["porch"]
    assert (rs[2].status, rs[2].json()) == (405, {"ok": False, "error": "method"})


@pytest.mark.parametrize(
    "body,error",
    [
        ('{"add":"x"}', "bad_json"),
        ('{"add":1,"remove":1}', "bad_json"),
        ('{"add":1,"add":2}', "bad_json"),
        ('{"add":65536}', "bad_json"),
        ('{"add":1,"x":1}', "bad_json"),
        ('{"alias":"x"}', "bad_json"),
        ('{"add":1} x', "bad_json"),
        ('{"add":65535}', "unknown_id"),
        ('{"add":SID,"alias":"Küche"}', "bad_alias"),
        ('{"add":SID,"alias":"a-very-long-alias-x"}', "bad_alias"),
        ('{"add":SID,"key":"abc"}', "bad_key"),
        ('{"add":SID,"key":"%s"}' % ("0" * 33), "bad_key"),
        ('{"id":65535,"alias":"x"}', "unknown_id"),
        ('{"remove":65535}', "unknown_id"),
    ],
)
def test_sensors_post_errors(web_cli, body, error):
    on = ["kvhex exp_sensors 01", *STATION, *GOVEE_SEEN]
    rs, _ = drive(web_cli, [*(s.encode() for s in on), req("GET", "/api/sensors")])
    sid = rs[0].json()["seen"][0]["id"]
    r, _ = one(web_cli, "POST", "/api/sensors", body.replace("SID", str(sid)), setup=on)
    assert (r.status, r.json()) == (400, {"ok": False, "error": error})


VICTRON_SEEN = [
    "sensadv DA:4B:25:00:00:01 1 -55 SmartSolar_HQ00 e102100075a00134120005116a3653554c77df9955ba"
]  # test_sensors.victron(0xA075, 0x01, SOLAR): check byte 0x00 = the test key's first byte


@pytest.mark.parametrize(
    "body,error",
    [
        ('{"add":SID}', "bad_key"),  # a Victron row needs a key
        ('{"add":SID,"key":"ff%s"}' % KEY.hex()[2:], "key_mismatch"),
    ],
)
def test_sensors_post_victron_key_errors(web_cli, body, error):
    on = ["kvhex exp_sensors 01", *STATION, *VICTRON_SEEN]
    rs, _ = drive(web_cli, [*(s.encode() for s in on), req("GET", "/api/sensors")])
    sid = rs[0].json()["seen"][0]["id"]
    r, _ = one(web_cli, "POST", "/api/sensors", body.replace("SID", str(sid)), setup=on)
    assert (r.status, r.json()) == (400, {"ok": False, "error": error})
    ok, _ = one(web_cli, "POST", "/api/sensors", '{"add":%d,"key":"%s"}' % (sid, KEY.hex()), setup=on)
    assert ok.status == 200 and ok.json()["configured"][0]["has_key"] is True


def test_sensors_second_of_type_needs_alias(web_cli):
    on = [
        "kvhex exp_sensors 01",
        *STATION,
        *GOVEE_SEEN,
        "sensadv A4:C1:38:00:00:03 1 -71 GVH5074_0003 88ec005c09d8165a02",
    ]
    rs, _ = drive(web_cli, [*(s.encode() for s in on), req("GET", "/api/sensors")])
    a, b = [s["id"] for s in rs[0].json()["seen"]]
    rs, _ = drive(web_cli, [*(s.encode() for s in on), req("POST", "/api/sensors", '{"add":%d}' % a),
                            req("POST", "/api/sensors", '{"add":%d}' % b), req("POST", "/api/sensors", '{"add":%d,"alias":"bed"}' % b)])  # fmt: skip
    assert rs[0].status == 200 and rs[1].json() == {"ok": False, "error": "bad_alias"} and rs[2].status == 200
    assert sorted(c["alias"] for c in rs[2].json()["configured"]) == ["bed", "govee"]


def test_sensors_list_survives_flag_off(web_cli):
    # web_cli's kv fake is per process: everything that must share state goes into ONE drive() script
    on = ["kvhex exp_sensors 01", *STATION, *GOVEE_SEEN]
    rs, _ = drive(web_cli, [*(s.encode() for s in on), req("GET", "/api/sensors")])
    sid = rs[0].json()["seen"][0]["id"]
    rs, _ = drive(web_cli, [*(s.encode() for s in on), req("POST", "/api/sensors", '{"add":%d}' % sid),
                            req("POST", "/api/experimental", '{"sensors":false}'), req("GET", "/api/sensors"),
                            req("GET", "/api/state"), req("POST", "/api/experimental", '{"sensors":true}'), req("GET", "/api/sensors")])  # fmt: skip
    assert rs[0].status == 200 and rs[2].status == 404
    assert "ext" not in json.loads(rs[3].body)
    assert [c["alias"] for c in rs[5].json()["configured"]] == ["govee"]


def test_sensors_store_failure_is_500(web_cli):
    on = ["kvhex exp_sensors 01", *STATION, *GOVEE_SEEN]
    rs, _ = drive(web_cli, [*(s.encode() for s in on), req("GET", "/api/sensors")])
    sid = rs[0].json()["seen"][0]["id"]
    r, _ = one(web_cli, "POST", "/api/sensors", '{"add":%d}' % sid, setup=on + ["kvfail 1"])
    assert (r.status, r.json()) == (500, {"ok": False, "error": "store"})


# ---- /api/display (R_FW_DISPLAY_WAKE) ---------------------------------------------------------


def test_display_settings_merge_persist_and_show_in_state(web_cli):
    """A partial body changes only its members, lands in the kv store (``disp_*``, one byte each) and
    in ``device.display``; ``{}`` changes nothing.

    .. test:: POST /api/display: merged, persisted, range-checked, station mode only, in device.display
       :id: T_FW_DISPLAY_WAKE_API
       :links: R_FW_DISPLAY_WAKE
    """
    resps, other = drive(
        web_cli,
        [
            *(s.encode() for s in STATION),
            req("POST", "/api/display", '{"min": 20, "prox_wake":false}'),
            req("GET", "/api/state"),
            req("POST", "/api/display", "{ }"),
            req("POST", "/api/display", '{"auto":false,"max":60,"min":5,"prox_wake":true}'),
            req("GET", "/api/state"),
            b"kv disp_auto",
            b"kv disp_prox",
            b"kv disp_min",
            b"kv disp_max",
        ],
    )
    assert [(resps[i].status, resps[i].json()) for i in (0, 2, 3)] == [(200, {"ok": True})] * 3
    disp = {k: resps[1].json()["device"]["display"][k] for k in ("auto", "prox_wake", "min", "max")}
    assert disp == {"auto": True, "prox_wake": False, "min": 20, "max": 100}
    disp = {k: resps[4].json()["device"]["display"][k] for k in ("auto", "prox_wake", "min", "max")}
    assert disp == {"auto": False, "prox_wake": True, "min": 5, "max": 60}
    assert [ln for ln in other if ln.startswith("KV ")] == [
        "KV ",
        "KV \x01",
        "KV \x05",
        "KV <",
    ]  # 0 / 1 / 5 / 60 (%.*s stops at the NUL)
    assert "LOG display: settings auto=0 prox=1 motion=1 min=5 max=60 offset=0" in other


def test_display_motion_wake_switch_persists_and_shows_in_state(web_cli):
    """``motion_wake`` (a bool, default on) lands in kv ``disp_motion`` and in ``device.display``; a
    store without the key (firmware before it) keeps the default; ``device.display.motion`` is null
    without a BMI270 reading (the host has none)."""
    resps, other = drive(
        web_cli,
        [
            *(s.encode() for s in STATION),
            req("POST", "/api/display", '{"motion_wake":false}'),
            req("GET", "/api/state"),
            b"kv disp_motion",
        ],
    )
    disp = resps[1].json()["device"]["display"]
    assert resps[0].status == 200 and disp["motion_wake"] is False and disp["prox_wake"] is True
    assert disp["motion"] is None and "KV " in other
    resps, _ = drive(
        web_cli, [*(s.encode() for s in STATION), b"kvhex disp_motion 01", req("GET", "/api/state")]
    )
    assert resps[0].json()["device"]["display"]["motion_wake"] is True
    resps, other = drive(
        web_cli, [*(s.encode() for s in STATION), b"kvhex disp_motion 02", req("GET", "/api/state")]
    )
    assert "LOG display: stored settings out of range, using defaults" in other


def test_display_dfs_knob_is_applied_at_once_never_stored_and_reads_the_live_pm_state(web_cli):
    """``dfs`` (a bool) reaches the platform's ``cali_pm_dfs`` at once and lands in no kv key;
    ``device.display.dfs`` / ``pm`` come from the live PM config — ``null`` on the host (no PM).

    .. test:: POST /api/display {"dfs"}: a bool, applied at once, not persisted; device.display.dfs/pm live
       :id: T_FW_DISPLAY_DFS_KNOB
       :links: R_FW_DISPLAY_WAKE
    """
    resps, other = drive(
        web_cli,
        [
            *(s.encode() for s in STATION),
            req("POST", "/api/display", '{"dfs":false}'),
            req("POST", "/api/display", '{"dfs":true,"min":9}'),
            req("GET", "/api/state"),
            b"kv dfs",
            b"kv disp_dfs",
        ],
    )
    assert [(r.status, r.json()) for r in resps[:2]] == [(200, {"ok": True})] * 2
    assert [ln for ln in other if ln.startswith("CALL pm_dfs")] == ["CALL pm_dfs 0", "CALL pm_dfs 1"]
    disp = resps[2].json()["device"]["display"]
    assert disp["dfs"] is None and disp["pm"] is None and disp["min"] == 9
    assert other.count("KV <missing>") == 2


@pytest.mark.parametrize(
    "setup,method,body,status,error",
    [
        (STATION, "GET", None, 405, "method"),
        (["wifi setup_ap"], "POST", '{"min":10}', 403, "setup_mode"),
        (["wifi setup_ap"], "POST", '{"dfs":false}', 403, "setup_mode"),
        (STATION, "POST", '{"dfs":1}', 400, "bad_json"),
        (STATION, "POST", '{"dfs":"false"}', 400, "bad_json"),
        (STATION, "POST", '{"dfs":true,"dfs":true}', 400, "bad_json"),
        (STATION, "POST", "", 400, "bad_json"),
        (STATION, "POST", '{"min":"10"}', 400, "bad_json"),
        (STATION, "POST", '{"auto":1}', 400, "bad_json"),
        (STATION, "POST", '{"motion_wake":1}', 400, "bad_json"),
        (STATION, "POST", '{"motion_wake":"true"}', 400, "bad_json"),
        (STATION, "POST", '{"motion_wake":true,"motion_wake":false}', 400, "bad_json"),
        (STATION, "POST", '{"min":10,"min":11}', 400, "bad_json"),
        (STATION, "POST", '{"min":-1}', 400, "bad_json"),
        (STATION, "POST", '{"min":1000}', 400, "bad_json"),
        (STATION, "POST", '{"min":10.5}', 400, "bad_json"),
        (STATION, "POST", '{"bright":10}', 400, "bad_json"),
        (STATION, "POST", '{"min":10} x', 400, "bad_json"),
        (STATION, "POST", '{"min":0}', 400, "bad_value"),
        (STATION, "POST", '{"min":51}', 400, "bad_value"),
        (STATION, "POST", '{"max":19}', 400, "bad_value"),
        (STATION, "POST", '{"max":101}', 400, "bad_value"),
        (STATION, "POST", '{"max":300}', 400, "bad_value"),  # past a byte: never wraps into range
        (STATION, "POST", '{"min":30,"max":30}', 400, "bad_value"),
        (STATION, "POST", '{"min":50,"max":20}', 400, "bad_value"),
        (STATION, "POST", '{"offset":51}', 400, "bad_value"),
        (STATION, "POST", '{"offset":-51}', 400, "bad_value"),
        (STATION, "POST", '{"offset":-200}', 400, "bad_value"),  # past an int8: never wraps into range
        (STATION, "POST", '{"offset":"5"}', 400, "bad_json"),
        (STATION, "POST", '{"offset":--5}', 400, "bad_json"),
        (STATION, "POST", '{"min":-5}', 400, "bad_json"),  # only the offset is signed
        (STATION + ["kvfail 1"], "POST", '{"min":10}', 500, "store"),
    ],
)
def test_display_settings_errors(web_cli, setup, method, body, status, error):
    r, _ = one(web_cli, method, "/api/display", body, setup=setup)
    assert (r.status, r.json()) == (status, {"ok": False, "error": error})


def test_display_settings_bounds_accepted(web_cli):
    for body in ('{"min":1,"max":20}', '{"min":50,"max":100}', '{"offset":-50}', '{"offset":50}'):
        r, _ = one(web_cli, "POST", "/api/display", body, setup=STATION)
        assert (r.status, r.json()) == (200, {"ok": True})


def test_display_offset_persists_and_is_read_back_after_a_reboot(web_cli):
    """``offset`` lands in kv ``disp_off`` (signed byte); a fresh boot (a new driver process) with the
    stored bytes reads them back, and an out-of-range store falls back to the defaults."""
    resps, other = drive(
        web_cli,
        [*(s.encode() for s in STATION), req("POST", "/api/display", '{"offset":15}'), b"kv disp_off"],
    )
    assert resps[0].status == 200 and "KV \x0f" in other
    resps, _ = drive(
        web_cli, [*(s.encode() for s in STATION), b"kvhex disp_off f1", req("GET", "/api/state")]
    )
    assert resps[0].json()["device"]["display"]["offset"] == -15
    resps, other = drive(
        web_cli, [*(s.encode() for s in STATION), b"kvhex disp_off 64", req("GET", "/api/state")]
    )
    assert resps[0].json()["device"]["display"]["offset"] == 0  # +100: out of range -> defaults
    assert "LOG display: stored settings out of range, using defaults" in other


# ---- GET /api/log (R_FW_LOG_API) ---------------------------------------------------------------


def _log_dir(tmp_path, n):
    log = tmp_path / "log"
    log.mkdir()
    (log / "1.jsonl").write_text(
        "".join(
            '{"seq":%d,"boot":3,"up":%d,"ts":null,"tsrc":null,"ev":"read","char":"1302","fn":"water","hex":"03011d010016"}\n'
            % (s, s * 10)
            for s in range(1, n + 1)
        )
    )
    return log


def test_api_log_pages_through_the_sd_task(web_cli, tmp_path):
    """The route parses since/max, waits (CALI_HTTP_PENDING) for the SD task's page and answers it as
    NDJSON ending in the trailer; no card -> 503, bad query -> 400, other method -> 405.

    .. test:: GET /api/log hands the page read to the SD task and streams it back
       :id: T_FW_LOG_API_ROUTE
       :links: R_FW_LOG_API
    """
    log = _log_dir(tmp_path, 5)
    resps, other = drive(
        web_cli,
        [
            req("GET", "/api/log?since=0"),  # no card yet
            ("sdlog %s 65536" % log).encode(),
            req("GET", "/api/log?since=2&max=2"),
            req("GET", "/api/log?max=10&since=4"),
            req("GET", "/api/log"),
            req("GET", "/api/log?since=x"),
            req("GET", "/api/log?since=1&foo=2"),
            req("POST", "/api/log", ""),
        ],
    )
    assert "SDLOG 0" in other
    assert [r.status for r in resps] == [503, 200, 200, 200, 400, 400, 405]
    assert resps[0].json() == {"ok": False, "error": "no_card"}
    page = [json.loads(ln) for ln in resps[1].body.decode().splitlines()]
    assert resps[1].headers["content-type"] == "application/x-ndjson"
    assert [r["seq"] for r in page[:-1]] == [3, 4] and page[-1] == {
        "next": 4,
        "more": True,
        "boot": 0,
        "lost": 0,
    }
    page = [json.loads(ln) for ln in resps[2].body.decode().splitlines()]
    assert [r["seq"] for r in page[:-1]] == [5] and page[-1]["more"] is False
    page = [json.loads(ln) for ln in resps[3].body.decode().splitlines()]
    assert len(page) == 6 and page[-1]["next"] == 5  # the default: from the start, up to 2000
    assert resps[4].json()["error"] == "bad_value"


def test_api_log_a_failed_sd_read_is_503(web_cli, tmp_path):
    log = _log_dir(tmp_path, 1)
    resps, _ = drive(web_cli, [("sdlog %s 10" % log).encode(), req("GET", "/api/log")])
    # a buffer below CALI_SDLOG_PAGE_MIN cannot hold a page: the SD read fails -> 503 sd_read
    assert resps[0].status == 503 and resps[0].json() == {"ok": False, "error": "sd_read"}
