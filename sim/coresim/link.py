"""The lockstep link between a firmware binary built over ``sim/shims`` and a board (``sim_hw.c``).

The firmware writes ``HW <now_ms> <request>`` and waits; the board answers with zero or more lines and
``END``: ``ACK`` / ``NACK`` (I2C), ``DATA <hex>`` (an I2C read), ``EV <delay_ms> RX <text>`` (UART bytes
after a delay), ``EV <delay_ms> <event>`` (handed to the firmware's ``sim_event()``), ``QUIT``. A line
``T <ms>`` is the virtual clock moving on; every other line is the firmware's log.
"""

from __future__ import annotations

import subprocess
import threading


def run(argv, board, wall_s=60):
    """Run ``argv`` against ``board`` (anything with ``handle(t, req) -> list[str]``, ``t``, ``log``,
    ``rc``) until the firmware exits; ``wall_s`` kills a hung run. Returns the board."""
    p = subprocess.Popen([str(a) for a in argv], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    timer = threading.Timer(wall_s, p.kill)
    timer.start()
    try:
        for line in p.stdout:
            line = line.rstrip("\n")
            if line.startswith("HW "):
                _, t, req = line.split(" ", 2)
                reply = board.handle(int(t), req) + ["END"]
                p.stdin.write("\n".join(reply) + "\n")
                p.stdin.flush()
            elif line.startswith("T "):
                board.t = int(line[2:])
            else:
                board.log.append((board.t, line))
        board.rc = p.wait()
    finally:
        timer.cancel()
        if p.poll() is None:
            p.kill()
    return board
