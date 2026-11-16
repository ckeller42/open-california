"""The iPhone app's BLE trace is the ``CALICTL_BLE_TRACE`` format: ``tools/trace_compare.py`` replays it.

``tests/vectors/ios_trace_sample.jsonl`` is a trace the Swift test ``T_IOS_TRACE_SESSION`` writes
(``IOS_TRACE_SAMPLE=1 swift test --filter TracingTransportTests``, times fixed): a session over the
fake transport connects, reads, runs the cooler command, gets one notification and loses the link.

.. test:: The iPhone app's BLE trace has calictl's keys and replays through trace_compare
   :id: T_IOS_TRACE_FORMAT
   :links: R_BLE_TRACE
"""

import json
import os

from calictl import trace
from tools import trace_compare

SAMPLE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vectors", "ios_trace_sample.jsonl")
IO_KEYS = ["t", "ev", "char", "fn", "hex"]
LINK_EXTRA = {"connect": [], "disconnect": ["reason"]}


def _lines():
    with open(SAMPLE, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def test_io_lines_have_trace_py_keys_and_link_lines_the_plans():
    """I/O lines: exactly the keys of trace.py's I/O lines, ``t,ev,char,fn,hex`` in order, ``fn`` as
    calictl names the char. Link lines: ``t,ev,addr`` with ``addr`` null, plus the plan's ``reason``
    on a disconnect (calictl writes ``attempt`` on a connect and no reason; ``trace_compare`` reads
    no link key). No heartbeat line."""
    events = _lines()
    assert {e["ev"] for e in events} == {"connect", "read", "write", "notify", "disconnect"}
    for e in events:
        assert isinstance(e["t"], float)
        if e["ev"] in ("notify", "read", "write"):
            assert list(e) == IO_KEYS
            assert e["char"] != "1003"
            assert e["fn"] == trace.fn_name(e["char"])
            assert e["hex"] == bytes.fromhex(e["hex"]).hex()  # lowercase, whole bytes
        else:
            assert list(e) == ["t", "ev", "addr"] + LINK_EXTRA[e["ev"]]
            assert e["addr"] is None


def test_trace_py_reads_it_and_trace_compare_replays_it(capsys):
    """``trace.read_events`` yields every line; ``trace_compare`` exits 0, every state frame re-packs
    byte for byte, and the cooler command is the one calictl write it lists."""
    assert list(trace.read_events(SAMPLE)) == _lines()
    assert trace_compare.main([SAMPLE]) == 0
    assert f"{len(_lines())} events over" in capsys.readouterr().out
    r = trace_compare.report(SAMPLE)
    assert r["events"] == len(_lines())
    assert r["round_trip"] and all(not d["mismatch"] for d in r["round_trip"].values())
    assert [(w["fn"], w["hex"]) for w in r["writes"]] == [("cooler", "fd771e3e1f1f")]
