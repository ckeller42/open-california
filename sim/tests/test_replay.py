"""Conformance: the fake RAK3172 replays real AT transcripts (sim/tests/data/rak3172-*.log).

Every ``>>`` line of a capture goes to the fake; its answers must be the capture's ``<<`` lines, in the same
order, each within a timing tolerance. A new capture file is a new test case."""

import re
from datetime import datetime
from pathlib import Path

import pytest
from coresim.devices import Rak3172

DATA = Path(__file__).parent / "data"
LINE = re.compile(r"^(\d\d:\d\d:\d\d\.\d{3}) LOG lora: (>>|<<) (.*)$")
TOLERANCE_MS = 700  # TX_DONE varies 8.19-8.86 s in the capture; the fake uses one value


def parse(path):
    out = []
    for raw in path.read_text().splitlines():
        m = LINE.match(raw)
        if m:
            t = datetime.strptime(m.group(1), "%H:%M:%S.%f")
            out.append(
                (
                    (t.hour * 3600 + t.minute * 60 + t.second) * 1000 + t.microsecond // 1000,
                    m.group(2),
                    m.group(3),
                )
            )
    return out


def blocks(lines):
    """[(t_sent, command, [(t, answer), ...])] — the answers up to the next command."""
    out = []
    for t, d, text in lines:
        if d == ">>":
            out.append((t, text, []))
        elif out:
            out[-1][2].append((t, text))
    return out


@pytest.mark.parametrize("capture", sorted(DATA.glob("rak3172-*.log")), ids=lambda p: p.stem)
def test_fake_answers_like_the_capture(capture):
    joined = "joined" in capture.stem
    rak = Rak3172(joined=joined)
    for t, cmd, real in blocks(parse(capture)):
        fake = [(t + int(x.split()[1]), x.split(" ", 3)[3]) for x in rak.uart(t, cmd)]
        assert [a for _, a in fake] == [a for _, a in real], cmd
        for (tf, a), (tr, _) in zip(fake, real):
            assert abs(tf - tr) <= TOLERANCE_MS, (cmd, a, tf - t, tr - t)
