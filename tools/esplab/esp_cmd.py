#!/usr/bin/env python3
"""Send console commands to the CoreS3 WITHOUT resetting it: stty -hupcl + raw open (no DTR/RTS toggle).
usage: esp_cmd.py PORT WAIT_S "cmd1" ["cmd2" ...]   -- each cmd is followed by WAIT_S of capture."""

import os
import subprocess
import sys
import time

port, wait, cmds = sys.argv[1], float(sys.argv[2]), sys.argv[3:]
subprocess.run(["stty", "-F", port, "-hupcl", "115200", "raw", "-echo"], check=True)
fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)


def pump(t):
    end = time.time() + t
    while time.time() < end:
        try:
            sys.stdout.write(os.read(fd, 4096).decode("utf-8", "replace"))
            sys.stdout.flush()
        except BlockingIOError:
            time.sleep(0.05)


pump(0.5)
for c in cmds:
    print(f"\n>>> {c}")
    os.write(fd, (c + "\n").encode())
    pump(wait)
os.close(fd)
