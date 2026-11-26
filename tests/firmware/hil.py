"""Dual-target LoRa scenarios: the same console steps on the simulated CoreS3 (``lora-sim``, coresim) or on a
real CoreS3 + RAK3172 on the HIL bench (its USB-Serial/JTAG console, opened without a reset).

A scenario is a list of ``(at_s, console line)`` and a duration. Either target returns a :class:`Result` with
the AT transcript (the ``lora: >>`` / ``lora: <<`` log lines, keys and EUIs redacted by the firmware) and the
whole log. :func:`drift` compares a sim run with a board run of the same scenario ("fake drift").

Target selection is by environment (pytest options would need the tests root's conftest):
``HIL_TARGET=sim`` (default) or ``board``; ``HIL_PORT`` = the board's ``/dev/serial/by-id/usb-Espressif_...``
path (required for ``board``); ``HIL_ACTIONS=1`` allows the scenarios that change the board's state.
"""

from __future__ import annotations

import os
import re
import select
import time
from dataclasses import dataclass, field
from pathlib import Path

LORA = re.compile(r"LOG lora: (>>|<<) (.*)$")
BY_ID = "/dev/serial/by-id/usb-Espressif"
FORBIDDEN = ("/dev/ttyUSB0",)  # odelly's node32: never opened, never flashed


@dataclass
class Result:
    target: str
    log: list = field(default_factory=list)  # (t_ms since the start, line)

    @property
    def transcript(self):
        """[(t_ms, ">>" | "<<", text)]"""
        return [(t, m.group(1), m.group(2)) for t, line in self.log if (m := LORA.search(line))]

    def answers(self, command):
        """The ``<<`` lines up to the next ``>>`` after each ``>> command``."""
        out, take = [], False
        for _, d, text in self.transcript:
            if d == ">>":
                take = text == command
                if take:
                    out.append([])
            elif take:
                out[-1].append(text)
        return out


def check_port(port):
    """The HIL board only: a by-id Espressif path that is not odelly's node32. Raises ValueError otherwise."""
    if not port:
        raise ValueError(
            "HIL_PORT is not set: name the HIL board by its /dev/serial/by-id/usb-Espressif_... path"
        )
    if not port.startswith(BY_ID):
        raise ValueError(f"HIL_PORT {port} is not a /dev/serial/by-id/usb-Espressif_... path")
    real = os.path.realpath(port)
    if real in FORBIDDEN or port in FORBIDDEN:
        raise ValueError(f"HIL_PORT {port} resolves to {real}: never this board")
    return port


class Console:
    """The board's console, opened like tools/esplab/esp_cmd.py: no HUPCL, raw, no DTR/RTS toggle (no reset)."""

    def __init__(self, port):
        import termios  # POSIX only: the sim target and the transcript helpers import without it

        self.fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        attrs = termios.tcgetattr(self.fd)
        attrs[0] = 0  # iflag: raw
        attrs[1] = 0  # oflag
        attrs[2] = (attrs[2] & ~termios.HUPCL) | termios.CS8 | termios.CREAD | termios.CLOCAL
        attrs[3] = 0  # lflag: no echo, no canonical mode
        attrs[4] = attrs[5] = termios.B115200
        termios.tcsetattr(self.fd, termios.TCSANOW, attrs)
        self.buf = b""

    def send(self, line):
        os.write(self.fd, (line + "\n").encode())

    def lines(self, until):
        """Yield the console's lines until the monotonic time ``until``."""
        while (left := until - time.monotonic()) > 0:
            r, _, _ = select.select([self.fd], [], [], min(left, 0.2))
            if r:
                try:
                    self.buf += os.read(self.fd, 4096)
                except (BlockingIOError, OSError):
                    time.sleep(0.02)
            while b"\n" in self.buf:
                line, self.buf = self.buf.split(b"\n", 1)
                yield line.decode("utf-8", "replace").rstrip("\r")

    def close(self):
        os.close(self.fd)


class BoardTarget:
    name = "board"

    def __init__(self, port, check=check_port):
        self.port = check(port)  # the plumbing tests pass a pty with a no-op check

    def run(self, steps, duration_s):
        res, con = Result("board"), Console(self.port)
        t0 = time.monotonic()
        pending = sorted(steps)
        try:
            while True:
                due = t0 + (pending[0][0] if pending else duration_s)
                for line in con.lines(min(due, t0 + duration_s)):
                    res.log.append((int((time.monotonic() - t0) * 1000), line))
                if not pending or time.monotonic() >= t0 + duration_s:
                    break
                con.send(pending.pop(0)[1])
        finally:
            con.close()
        return res


class SimTarget:
    name = "sim"

    def __init__(self, binary, store):
        self.binary, self.store = binary, Path(store)

    def run(self, steps, duration_s, board=None):
        from . import esp_sim

        board = board or esp_sim.Board()
        start = (
            60_000  # a session starts on a running board: the steps begin after the boot and the first round
        )
        for at_s, line in steps:
            board.at(start + int(at_s * 1000), lambda b, line=line: b.emit(f"EV 0 CONSOLE {line}"))
        self.store.mkdir(parents=True, exist_ok=True)
        esp_sim.run(self.binary, board, start + int(duration_s * 1000), self.store)
        return Result("sim", [(t - start, line) for t, line in board.log if t >= start])


def drift(sim: Result, board: Result, slack_ms=1000):
    """Differences between the AT transcripts: the commands in order, each command's answer kinds, and an
    answer's delay beyond ``slack_ms`` plus 50 %. Payload values differ by nature and are not compared."""

    def command(text):
        return re.sub(r"^(AT\+SEND=\d+):.*$", r"\1:…", text)

    def shape(text):
        return re.sub(r"=.*$", "=…", text)

    def blocks(r):
        out = []
        for t, d, text in r.transcript:
            if d == ">>":
                out.append((t, command(text), []))
            elif out:
                out[-1][2].append((t - out[-1][0], shape(text)))
        return out

    diffs, sb, bb = [], blocks(sim), blocks(board)
    if [c for _, c, _ in sb] != [c for _, c, _ in bb]:
        diffs.append(f"commands: sim {[c for _, c, _ in sb]} vs board {[c for _, c, _ in bb]}")
    for (_, cmd, sa), (_, _, ba) in zip(sb, bb):
        if [a for _, a in sa] != [a for _, a in ba]:
            diffs.append(f"{cmd}: answers sim {[a for _, a in sa]} vs board {[a for _, a in ba]}")
            continue
        for (ds, a), (db, _) in zip(sa, ba):
            if abs(ds - db) > slack_ms + 0.5 * max(ds, db):
                diffs.append(f"{cmd} -> {a}: after {ds} ms in the sim, {db} ms on the board")
    return diffs
