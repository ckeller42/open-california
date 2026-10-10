#!/usr/bin/env python3
"""Timestamp every console line of the CoreS3 WITHOUT resetting it (stty -hupcl + raw open, no DTR/RTS).
usage: esp_tail.py PORT SECONDS   -- the firmware console has no timestamps of its own."""

import os
import subprocess
import sys
import time

port, secs = sys.argv[1], float(sys.argv[2])
subprocess.run(["stty", "-F", port, "-hupcl", "115200", "raw", "-echo"], check=True)
fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
buf, end = b"", time.time() + secs
while time.time() < end:
    try:
        buf += os.read(fd, 4096)
    except BlockingIOError:
        time.sleep(0.02)
        continue
    while b"\n" in buf:
        line, buf = buf.split(b"\n", 1)
        t = time.time()
        stamp = time.strftime("%H:%M:%S", time.localtime(t)) + ".%03d" % int(t % 1 * 1000)
        print(stamp, line.decode("utf-8", "replace")[:160], flush=True)
