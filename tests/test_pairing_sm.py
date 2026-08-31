"""Sequence vectors through the pure pairing SM — the future ESP golden vectors.

.. test:: Pairing SM sequence vectors
   :id: T_PAIRING_SM_VECTORS
   :links: R_PAIRING_SM
"""
import json
from pathlib import Path

from calictl import pairing

VECTORS = Path(__file__).parent / "vectors" / "pairing.json"


def test_vectors_replay():
    for case in json.loads(VECTORS.read_text())["cases"]:
        ps = pairing.PairingState(*case["start"])
        for i, s in enumerate(case["steps"]):
            ps, acts = pairing.step(ps, s["ev"], s.get("arg", 0))
            assert list(ps) == s["state"], (case["id"], i)
            assert [list(a) for a in acts] == s["actions"], (case["id"], i)


def test_stale_events_are_noops():
    ps = pairing.PairingState(pairing.IDLE, 0, pairing.ERR_NONE)
    for ev in (pairing.EV_PAIR_OK, pairing.EV_PASSKEY_ENTERED, pairing.EV_TIMEOUT):
        ps2, acts = pairing.step(ps, ev)
        assert ps2 == ps and acts == []


def test_timeout_table_covers_every_timed_state():
    assert set(pairing.TIMEOUT_S) == {pairing.SCANNING, pairing.CONNECTING,
                                      pairing.WAITING_PASSKEY, pairing.PAIRING,
                                      pairing.VERIFYING, pairing.RESETTING}
