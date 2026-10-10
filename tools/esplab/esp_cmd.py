#!/usr/bin/env python3
"""Talk to the CoreS3 console WITHOUT resetting it: stty -hupcl + raw open (no DTR/RTS toggle).
usage: esp_cmd.py PORT WAIT_S "cmd1" ["cmd2" ...]   -- each cmd is followed by WAIT_S of capture.
       esp_cmd.py PORT --tail SECONDS               -- timestamp every console line (the firmware
                                                       console has no timestamps of its own)."""

import os
import subprocess
import sys
import time

port = sys.argv[1]
subprocess.run(["stty", "-F", port, "-hupcl", "115200", "raw", "-echo"], check=True)
fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)


def emit(line: bytes) -> None:
    t = time.time()
    stamp = time.strftime("%H:%M:%S", time.localtime(t)) + ".%03d" % int(t % 1 * 1000)
    print(stamp, line.decode("utf-8", "replace")[:160], flush=True)


def tail(secs):
    buf, end = b"", time.time() + secs
    while time.time() < end:
        try:
            chunk = os.read(fd, 4096)
        except BlockingIOError:
            chunk = b""
        if not chunk:  # no data (or EOF on a re-enumerating USB port): don't spin
            time.sleep(0.02)
            continue
        buf += chunk
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            emit(line)
    if buf:
        emit(buf)


def pump(t):
    end = time.time() + t
    while time.time() < end:
        try:
            sys.stdout.write(os.read(fd, 4096).decode("utf-8", "replace"))
            sys.stdout.flush()
        except BlockingIOError:
            time.sleep(0.05)


if sys.argv[2] == "--tail":
    tail(float(sys.argv[3]))
else:
    pump(0.5)
    for c in sys.argv[3:]:
        print(f"\n>>> {c}")
        os.write(fd, (c + "\n").encode())
        pump(float(sys.argv[2]))
os.close(fd)
