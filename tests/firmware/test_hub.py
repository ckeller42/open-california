"""Remote hub core (``hub.c``): sealed frames, replay guard, data key, config and QR text via ``hub_cli``.

One hub_cli process per test (in-memory KV); ctx 0 is the ESP, ctx 2 a peer holding the same key
(``peer 2 0``); ``open i`` opens the last sealed frame.
"""

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
    """Ping-pong A, B, A: the frame of the older session A is a replay, although its counter is higher.

    .. test:: hub: a replay of an older peer session (A, B, A) is rejected
       :id: T_FW_HUB_PINGPONG
    """
    r = _run(*SETUP, "peer 1 0", SEAL, "open 2 2", "seal 1 1 " + PT, "open 2 2", SEAL, "open 2 2")
    assert [r[4][0], r[6][0], r[8][0]] == [str(N), str(N), "-1"]


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
