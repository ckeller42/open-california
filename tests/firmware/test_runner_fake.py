"""The firmware's pairing runner, driven by a scripted fake transport (#154).

.. req:: Firmware pairing runner
   :id: R_FW_PAIRING_RUNNER

   ``firmware/components/cali_core/runner.c`` connects the C pairing SM (``R_FW_PAIRING_SM``) to a
   BLE stack only through ``cali_transport_t`` (``cali_transport.h``): transport events map to SM
   events per ``docs/business-logic/guided-pairing.md`` "ESP mapping", SM actions become transport
   calls, ``PAIR_TIMEOUT_S`` timeouts come from ``cali_runner_tick``, and every state change is
   reported (bonded carries the transport's identity address). A link drop during pairing counts
   as a pairing failure (NimBLE refusing SMP itself emits no encryption change), and a pairing
   that never completes ends ``error timeout`` instead of hanging. (C comments are not autodoc'd:
   this module docstring is the sphinx-needs shim, like ``test_pairing_sm_parity``.)

.. test:: Pairing runner call/state sequences against a fake transport
   :id: T_FW_RUNNER_FAKE
   :links: R_FW_PAIRING_RUNNER, R_FW_PAIRING_SM

``runner_fake.c`` reads transport events and console commands from stdin and prints each transport
call (``CALL <name> [arg]``) and each state (``STATE <json>``); compiled with the host ``cc``
(runs on macOS too, no NimBLE).
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CORE = ROOT / "firmware" / "components" / "cali_core"
IDENTITY = "C0:FF:EE:CA:11:F0"


@pytest.fixture(scope="module")
def fake(tmp_path_factory):
    cc = shutil.which("cc") or pytest.skip("no C compiler")
    out = tmp_path_factory.mktemp("runner") / "runner_fake"
    subprocess.run(
        [
            cc,
            "-std=c99",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-I",
            str(CORE / "include"),
            "-I",
            str(ROOT / "csrc"),
            str(CORE / "runner.c"),
            str(CORE / "pairing_sm.c"),
            str(CORE / "test" / "runner_fake.c"),
            "-o",
            str(out),
        ],
        check=True,
    )
    return out


def run(fake, *script):
    r = subprocess.run(
        [str(fake)], input="\n".join(script) + "\n", capture_output=True, text=True, timeout=30, check=True
    )
    return r.stdout.splitlines()


def st(state, attempts=0, error=None, address=None):
    return "STATE " + json.dumps(
        {"state": state, "attempts": attempts, "error": error, "address": address}, separators=(",", ":")
    )


HAPPY = ["pair", "FOUND", "CONNECTED", "PASSKEY_REQ", "passkey 123456", "ENC_OK", "READ 1004 0"]


def test_happy_path_call_and_state_sequence(fake):
    assert run(fake, *HAPPY) == [
        "CALL start_scan VWCAMPER",
        st("scanning"),
        "CALL stop_scan",
        "CALL connect_found",
        st("connecting"),
        "CALL pair",
        st("pairing"),
        st("waiting_passkey"),
        "CALL inject_passkey 123456",
        st("pairing"),
        "CALL read 4100",
        st("verifying"),  # 4100 = 0x1004 (CODEC_CHAR_AUTH)
        st("bonded", address=IDENTITY),
    ]


def test_passkey_outside_waiting_passkey_is_ignored(fake):
    out = run(fake, "passkey 111111", "pair", "FOUND", "CONNECTED", "passkey 222222")
    assert not any(l.startswith("CALL inject_passkey") for l in out)
    assert out[-1] == st("pairing")


def test_three_connect_failures_end_connect_failed(fake):
    out = run(fake, "pair", *["FOUND", "CONNECT_FAIL"] * 3)
    assert out[5:8] == ["CALL disconnect", "CALL start_scan VWCAMPER", st("scanning", 1)]
    assert out[-2:] == ["CALL disconnect", st("error", 3, "connect_failed")]


def test_scanning_timeout(fake):
    assert run(fake, "pair", "tick 29999") == ["CALL start_scan VWCAMPER", st("scanning")]
    out = run(fake, "pair", "tick 29999", "tick 30000")
    assert out[2:] == ["CALL stop_scan", st("error", 0, "timeout")]


def test_the_timeout_restarts_on_every_state_change(fake):
    # connecting is entered at the latest tick (20000); its 20 s timeout ends at 40000, not 30000
    out = run(fake, "tick 20000", "pair", "tick 25000", "FOUND", "tick 39999")
    assert out[-1] == st("connecting")
    out = run(fake, "tick 20000", "pair", "tick 25000", "FOUND", "tick 45000")
    assert out[-2:] == ["CALL disconnect", st("error", 0, "timeout")]


def test_a_refused_pairing_without_enc_change_times_out(fake):
    # NimBLE refusing SMP itself (e.g. SC-only vs a legacy peer) emits no ENC_CHANGE: no hang
    out = run(fake, "tick 0", "pair", "FOUND", "CONNECTED", "tick 14999")
    assert out[-1] == st("pairing")
    out = run(fake, "tick 0", "pair", "FOUND", "CONNECTED", "tick 15000")
    assert out[-2:] == ["CALL disconnect", st("error", 0, "timeout")]


def test_a_disconnect_during_pairing_is_a_pairing_failure(fake):
    out = run(fake, "pair", "FOUND", "CONNECTED", "DISCONNECTED")
    assert out[-3:] == ["CALL disconnect", "CALL start_scan VWCAMPER", st("scanning", 1)]


def test_a_disconnect_while_waiting_for_the_passkey_ends_at_the_timeout(fake):
    # the SM (Python parity) has no waiting_passkey + EV_PAIR_FAIL transition: the runner does not
    # invent one; the 60 s passkey timeout ends it instead of a hang
    out = run(fake, "tick 0", "pair", "FOUND", "CONNECTED", "PASSKEY_REQ", "DISCONNECTED", "tick 59999")
    assert out[-1] == st("waiting_passkey")
    out = run(fake, "tick 0", "pair", "FOUND", "CONNECTED", "PASSKEY_REQ", "DISCONNECTED", "tick 60000")
    assert out[-2:] == ["CALL disconnect", st("error", 0, "timeout")]


def test_three_pairing_failures_end_pairing_failed(fake):
    out = run(fake, "pair", *["FOUND", "CONNECTED", "ENC_FAIL"] * 2, "FOUND", "CONNECTED", "DISCONNECTED")
    assert out[-2:] == ["CALL disconnect", st("error", 3, "pairing_failed")]


def test_a_disconnect_while_connecting_is_a_connect_failure(fake):
    out = run(fake, "pair", "FOUND", "DISCONNECTED")
    assert out[-3:] == ["CALL disconnect", "CALL start_scan VWCAMPER", st("scanning", 1)]


@pytest.mark.parametrize("ev", ["READ 1004 5", "DISCONNECTED"])
def test_verify_failures(fake, ev):
    out = run(fake, *HAPPY[:-1], ev)
    assert out[-2:] == ["CALL disconnect", st("error", 0, "verify_failed")]


def test_a_call_that_cannot_start_is_fed_back_as_its_failure(fake):
    out = run(fake, "fail connect_found", "pair", "FOUND")
    assert out[2:] == [
        "CALL stop_scan",
        "CALL connect_found",
        st("connecting"),
        "CALL disconnect",
        "CALL start_scan VWCAMPER",
        st("scanning", 1),
    ]
    out = run(fake, "fail read", *HAPPY[:-1])
    assert out[-4:] == ["CALL read 4100", st("verifying"), "CALL disconnect", st("error", 0, "verify_failed")]


def test_events_the_runner_does_not_consume_are_forwarded(fake):
    out = run(
        fake,
        *HAPPY[:-1],
        "READ 1102 0",
        "READ 1004 0",
        "NOTIFY 1102",
        "DISCOVERED",
        "DISCONNECTED 8",
        "FOUND",
    )
    assert "OTHER READ 4354 0" in out  # a non-auth read while verifying
    assert out[-5:] == [
        st("bonded", address=IDENTITY),
        "OTHER NOTIFY 4354 0",
        "OTHER DISCOVERED 0 0",
        "OTHER DISCONNECTED 0 8",
        "OTHER FOUND 0 0",
    ]


def test_forget_removes_the_bond_then_idles(fake):
    out = run(fake, *HAPPY, "forget")
    assert out[-3:] == ["CALL remove_bond", st("resetting"), st("idle")]


def test_a_failed_bond_removal_stays_resetting_until_the_timeout(fake):
    out = run(fake, "tick 0", *HAPPY, "fail remove_bond", "forget", "tick 10000")
    assert out[-3:] == ["CALL remove_bond", st("resetting"), st("error", 0, "timeout")]
    assert st("idle") not in out
