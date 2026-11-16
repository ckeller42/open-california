"""Remote hub core (``hub.c``): sealed frames, replay guard, data key, config and QR text via ``hub_cli``.

One hub_cli process per test (in-memory KV); ctx 0 is the ESP, ctx 2 a peer holding the same key
(``peer 2 0``); ``open i`` opens the last sealed frame.
"""

import json
import subprocess

import pytest

from .conftest import build_host

pytestmark = [pytest.mark.linux_only, pytest.mark.xdist_group("firmware-host-build")]


def _run(*lines):
    """Feed the lines to one hub_cli process; return one split output line per input line."""
    out = subprocess.run(
        [str(build_host("hub_cli"))],
        input="\n".join(lines) + "\n",
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    assert len(out) == len(lines), out
    return [o.split() for o in out]


PT = "7b2274223a313233347d"  # {"t":1234}
N = len(PT) // 2
SEAL = "seal 0 1 " + PT
SETUP = ["load 0", "peer 2 0"]


def test_seal_open_roundtrip():
    """A frame sealed by ctx 0 opens in ctx 2 holding the key; layout is ver|dir|nonce|ct|tag.

    .. test:: hub frame seals and opens, wire layout ver|dir|nonce12|ct|tag16
       :id: T_FW_HUB_ROUNDTRIP
    """
    r = _run(*SETUP, SEAL, "open 2 2")
    frame = r[2][0]
    assert frame[:4] == "0101" and len(frame) == 2 * (30 + N)
    assert frame[4:6] == "01"  # the nonce starts with the dir
    for d in (2, 3):  # dir 1 equals the version byte: check the others too
        assert _run("load 0", f"seal 0 {d} " + PT)[1][0][4:6] == f"0{d}"
    assert frame[20:28] == "00000001"  # counter 1, big-endian, last 4 nonce bytes
    assert r[3] == [str(N), "1", PT]


def test_open_rejects_flipped_tag():
    """hub frame with a flipped tag bit or a bad version is rejected.

    .. test:: hub frame with a flipped tag bit or a bad version is rejected
       :id: T_FW_HUB_TAG
    """
    r = _run(*SETUP, SEAL, "flip", "open 2 2", "flip", "open 2 2", "badver", "open 2 2")
    assert r[4][0] == "-1"
    assert r[6][0] == str(N)  # flipped back: the bad one did not move the replay guard
    assert r[8][0] == "-1"


def test_open_rejects_replay():
    """hub frame replay and an older counter in the same session are rejected.

    .. test:: hub frame replay and an older counter in the same session are rejected
       :id: T_FW_HUB_REPLAY
    """
    r = _run(*SETUP, SEAL, "open 2 2", "open 2 2", SEAL, SEAL, "open 2 2", "open 2 2")
    assert [r[3][0], r[4][0]] == [str(N), "-1"]
    assert r[7][0] == str(N) and r[8][0] == "-1"


def test_new_session_restarts_counter():
    """A frame of a new peer session may restart the counter (the time check lives in the RPC layer).

    .. test:: hub: a new peer session restarts the replay counter
       :id: T_FW_HUB_SESSION
    """
    r = _run(*SETUP, "peer 1 0", SEAL, SEAL, "open 2 2", "seal 1 1 " + PT, "open 2 2", "open 2 2")
    assert [r[5][0], r[7][0], r[8][0]] == [str(N), str(N), "-1"]


def test_old_session_replay_rejected():
    """A, B, then a byte-identical replay of an earlier A frame: -1.

    .. test:: hub: a replayed frame of an older peer session is rejected
       :id: T_FW_HUB_PINGPONG
    """
    r = _run(*SETUP, "peer 1 0", SEAL, "keep", "open 2 2", "seal 1 1 " + PT, "open 2 2", "use", "open 2 2")
    assert [r[5][0], r[7][0], r[9][0]] == [str(N), str(N), "-1"]


def test_interleaved_senders_accepted():
    """Two live senders in one dir (A1, B1, A2, B2) are all accepted.

    .. test:: hub: interleaved senders in one dir are all accepted
       :id: T_FW_HUB_INTERLEAVE
    """
    r = _run(
        *SETUP,
        "peer 1 0",
        SEAL,
        "open 2 2",
        "seal 1 1 " + PT,
        "open 2 2",
        SEAL,
        "open 2 2",
        "seal 1 1 " + PT,
        "open 2 2",
    )
    assert [r[i][0] for i in (4, 6, 8, 10)] == [str(N)] * 4


def test_older_session_higher_counter_accepted():
    """After A then B, a fresh A frame with a higher counter is accepted.

    .. test:: hub: a fresh frame of an earlier session is accepted
       :id: T_FW_HUB_FRESH
    """
    r = _run(
        *SETUP, "peer 1 0", SEAL, "open 2 2", SEAL, "keep", "seal 1 1 " + PT, "open 2 2", "use", "open 2 2"
    )
    assert [r[4][0], r[8][0], r[10][0]] == [str(N)] * 3


def test_wrong_direction_rejected():
    """A dir-1 frame given to an opener that expects dir 2 is rejected; dir 1 and 3 are fine for the app.

    .. test:: hub: the opener rejects a dir it does not expect
       :id: T_FW_HUB_DIR
    """
    r = _run(*SETUP, SEAL, "open 2 4", "open 2 10", "open 2 2")
    assert [r[3][0], r[4][0], r[5][0]] == ["-1", str(N), "-1"]  # 10 = dirs 1,3 ; then the replay


def test_counter_rotates_session():
    """Before the 32-bit counter wraps the sender takes a new random session; the counter restarts at 1.

    .. test:: hub: a new session before the counter wraps
       :id: T_FW_HUB_ROTATE
    """
    r = _run("load 0", SEAL, "setctr 0 4294967295", SEAL)
    a, b = r[1][0], r[3][0]
    assert a[6:20] != b[6:20] and b[20:28] == "00000001"


def test_rekey_store_failure_keeps_key():
    """A failed store leaves the key and the session as they were.

    .. test:: hub: rekey keeps the old key when the store fails
       :id: T_FW_HUB_REKEY_FAIL
    """
    r = _run("load 0", "kvfail 1", "rekey 0", "key 0", "kvfail 0", "rekey 0")
    assert r[2] == ["-1"] and r[3] == r[0] and r[5] != r[0]


def test_cfg_load_terminates_strings():
    assert _run("cfgfull")[0] == ["23", "95", "31", "47", "31", "47"]


def test_key_persists():
    """hub data key is made once and persists in KV.

    .. test:: hub data key is made once and persists in KV
       :id: T_FW_HUB_KEY
    """
    r = _run("load 0", "reload 0", "key 0")
    assert r[0] == r[1] == r[2] and len(r[0][0]) == 32 and r[0][0] != "00" * 16


def test_rekey_changes_key():
    """rekey makes and stores a new key; frames of the old key no longer open.

    .. test:: rekey makes and stores a new key; frames of the old key no longer open
       :id: T_FW_HUB_REKEY
    """
    r = _run(*SETUP, SEAL, "rekey 0", "reload 0", "open 2 2", "peer 2 0", "open 2 2")
    assert r[3][0] != r[0][0] and r[4] == r[3]
    assert r[5][0] == str(N)  # ctx 2 still has the old key: opens its frame
    assert r[7][0] == "-1"  # now ctx 2 has the new key: the old frame fails


def test_big_frame_8kb():
    """an 8 KB WiFi frame seals and opens (aead cap lifted).

    .. test:: an 8 KB WiFi frame seals and opens (aead cap lifted)
       :id: T_FW_HUB_BIG
    """
    assert _run("load 0", "big 0")[1] == ["8192", str(8192 + 30), "1"]


def test_cfg_roundtrip():
    assert _run("cfg")[0] == ["0", "0", "1", "wss://h:8884/mqtt"]


def test_qr_text():
    """hub QR text OCH1*van*url*user*pass*key.

    .. test:: hub QR text OCH1*van*url*user*pass*key
       :id: T_FW_HUB_QR
    """
    key = "00112233445566778899aabbccddeeff"
    assert _run(f"qr van1 wss://h:8884/mqtt u p {key}")[0] == [f"OCH1*van1*wss://h:8884/mqtt*u*p*{key}"]


# ---- cali_hub_rpc: a request plaintext through the web router (web_cli, `rpc <json>`) ----

CMD = '{"function":"cooler","what":"power","value":"on","confirm":true}'
NOW = 1_700_000_000_000
ONLINE = ["wifi online home 192.168.1.20 -60", "joined 1", "ntp %d" % NOW]


@pytest.fixture(scope="module")
def rpc_cli(tmp_path_factory):
    from .test_web_handlers import _build

    return _build(tmp_path_factory, "web_cli_rpc")


def _rpc(cli, req, setup=ONLINE):
    """Run the setup lines, then `rpc <req>`; return the parsed res and the other output lines."""
    lines = [*setup, "rpc " + (req if isinstance(req, str) else json.dumps(req))]
    out = subprocess.run(
        [str(cli)], input="\n".join(lines) + "\n", capture_output=True, text=True, check=True
    ).stdout.splitlines()
    res = [line[4:] for line in out if line.startswith("RPC ")]
    assert len(res) == 1, out
    return json.loads(res[0]), [line for line in out if not line.startswith("RPC ")]


def test_rpc_get_state(rpc_cli):
    """GET /api/state answers 200 with the state document as the body string.

    .. test:: hub rpc runs a GET through cali_web_handle
       :id: T_FW_HUB_RPC_GET
    """
    res, _ = _rpc(rpc_cli, {"id": 7, "m": "GET", "p": "/api/state", "b": None, "t": NOW})
    assert res["id"] == 7 and res["s"] == 200
    assert "device" in json.loads(res["b"])


def test_rpc_unknown_path_404(rpc_cli):
    res, _ = _rpc(rpc_cli, {"id": 1, "m": "GET", "p": "/nope", "b": None, "t": NOW})
    assert (res["id"], res["s"], res["b"]) == (1, 404, "")


def test_rpc_command_resumes(rpc_cli):
    """A /api/command that is PENDING twice ends with the final status, not 202.

    .. test:: hub rpc re-asks a pending handler until it answers
       :id: T_FW_HUB_RPC_PENDING
    """
    body = CMD
    setup = [*ONLINE, "ctl pending", "ctldone ok 2"]
    res, other = _rpc(rpc_cli, {"id": 9, "m": "POST", "p": "/api/command", "b": body, "t": NOW}, setup)
    assert res["id"] == 9 and res["s"] == 200, res
    assert any(line.startswith("CALL submit") for line in other)


def test_rpc_setup_mode_keeps_403(rpc_cli):
    body = CMD
    setup = ["wifi setup_ap", "ntp %d" % NOW]
    res, _ = _rpc(rpc_cli, {"id": 3, "m": "POST", "p": "/api/command", "b": body, "t": NOW}, setup)
    assert res["s"] == 403


@pytest.mark.parametrize(
    "req",
    [
        "not json",
        "{}",
        '{"id":1,"m":"PUT","p":"/api/state","b":null,"t":1}',
        '{"id":1,"m":"GET","p":"x","b":null,"t":1}',
    ],
)
def test_rpc_bad_json(rpc_cli, req):
    res, _ = _rpc(rpc_cli, req)
    assert res["s"] == 400


def test_rpc_missing_t(rpc_cli):
    res, _ = _rpc(rpc_cli, {"id": 4, "m": "GET", "p": "/api/state", "b": None})
    assert (res["id"], res["s"]) == (4, 400)


def test_rpc_stale_time(rpc_cli):
    """With valid time a request 10 min old is 408 stale; 60 s off is accepted.

    .. test:: hub rpc rejects a request more than 120 s off the device clock
       :id: T_FW_HUB_RPC_STALE
    """
    res, _ = _rpc(rpc_cli, {"id": 5, "m": "GET", "p": "/api/state", "b": None, "t": NOW - 600_000})
    assert (res["id"], res["s"], res["b"]) == (5, 408, "stale")
    res, _ = _rpc(rpc_cli, {"id": 6, "m": "GET", "p": "/api/state", "b": None, "t": NOW + 60_000})
    assert res["s"] == 200


def test_rpc_time_not_valid_accepts(rpc_cli):
    res, _ = _rpc(
        rpc_cli,
        {"id": 8, "m": "GET", "p": "/api/state", "b": None, "t": 5},
        ["wifi online home 1.2.3.4 -60", "joined 1"],
    )
    assert res["s"] == 200


def _get(**extra):
    return {"id": 1, "m": "GET", "p": "/api/state", "b": None, "t": NOW, **extra}


def test_rpc_t_extreme_negative_is_stale(rpc_cli):
    """t near -INT64_MAX must not wrap now - t past the 120 s check."""
    res, _ = _rpc(rpc_cli, '{"id":2,"m":"GET","p":"/api/state","b":null,"t":-9223372036854775807}')
    assert (res["s"], res["b"]) == (408, "stale")


def test_rpc_nul_escape_rejected(rpc_cli):
    res, _ = _rpc(rpc_cli, r'{"id":2,"m":"POST","p":"/api/wifi","b":"{\"ssid\":\"a\u0000b\"}","t":%d}' % NOW)
    assert res["s"] == 400


def test_rpc_null_pump_is_500(rpc_cli):
    """cali_hub_rpc without a pump answers 500 instead of spinning on a pending route."""
    res, _ = _rpc(rpc_cli, json.dumps(_get()), ONLINE)
    assert res["s"] == 200
    out = subprocess.run([str(rpc_cli)], input="rpc0 {}\n", capture_output=True, text=True, check=True).stdout
    assert '"s":500' in out


def test_rpc_escapes_roundtrip_into_router(rpc_cli):
    """Escapes in the request body string (\\" \\\\ \\u00e9) reach the router decoded.

    .. test:: hub rpc decodes the body string and escapes the response body byte-exact
       :id: T_FW_HUB_RPC_ESCAPES
    """
    body = '{"ssid":"a\\"b\\\\c\u00e9","psk":"test-psk-1234"}'  # the router sees a"b\cé
    res, other = _rpc(rpc_cli, {"id": 5, "m": "POST", "p": "/api/wifi", "b": body, "t": NOW})
    assert 'CALL set_creds [a"b\\cé] [test-psk-1234]' in other, (res, other)
    res, _ = _rpc(rpc_cli, {"id": 6, "m": "POST", "p": "/api/wifi", "b": '{"ssid":"x\\ny"}', "t": NOW})
    assert res["s"] == 400 and res["b"] == '{"ok":false,"error":"json"}'  # quotes come back byte-exact
    res, _ = _rpc(rpc_cli, _get(), ['wifi online a"b\\c 1.2.3.4 -60', "joined 1", "ntp %d" % NOW])
    assert json.loads(res["b"])["device"]["wifi"]["ssid"] == 'a"b\\c'


def test_rpc_unknown_key_ignored_duplicate_rejected(rpc_cli):
    res, _ = _rpc(
        rpc_cli, '{"x":{"y":[1,"a\\\\"]},"id":3,"m":"GET","p":"/api/state","b":null,"t":%d,"z":1}' % NOW
    )
    assert (res["id"], res["s"]) == (3, 200)
    res, _ = _rpc(rpc_cli, '{"id":3,"id":4,"m":"GET","p":"/api/state","b":null,"t":%d}' % NOW)
    assert res["s"] == 400


def test_rpc_long_unknown_keys_ignored(rpc_cli):
    res, _ = _rpc(
        rpc_cli,
        '{"timestamp":1,"id":3,"version_extra":"x","m":"GET","p":"/api/state","b":null,"t":%d,"k\\"y_long_one":[]}'
        % NOW,
    )
    assert (res["id"], res["s"]) == (3, 200)


def test_rpc_newline_escape_is_decoded(rpc_cli):
    """A real LF in the body string travels as \\n and the router sees the LF: JSON whitespace there is
    accepted (a literal backslash-n outside a string would be a 400)."""
    body = '{"ssid":"minsel",\n"psk":"test-psk-1234"}'
    assert "\n" in body and "\\n" not in body
    res, other = _rpc(rpc_cli, {"id": 7, "m": "POST", "p": "/api/wifi", "b": body, "t": NOW})
    assert "CALL set_creds [minsel] [test-psk-1234]" in other, (res, other)
    assert "\\n" in json.dumps({"b": body})  # on the wire it is the two characters \ n
