#!/usr/bin/env python3
"""Recovery: put buspi's own WiFi (NetworkManager profile) back onto the ESP over its USB console.
The PSK is read from the root-only profile and written to the serial port only: never printed, never in
argv. Run as root: sudo python3 esp_wifi_restore.py [PROFILE]. Console echo lines that contain the PSK
are masked before printing."""

import configparser
import os
import subprocess
import sys
import time

prof = sys.argv[1] if len(sys.argv) > 1 else "minsel"
cp = configparser.ConfigParser(interpolation=None)
cp.read("/etc/NetworkManager/system-connections/%s.nmconnection" % prof)
ssid, psk = cp["wifi"]["ssid"], cp["wifi-security"]["psk"]
if " " in ssid or " " in psk:
    sys.exit("ssid/psk with a space: the console's `wifi set` takes single tokens; use the hotspot page")
port = "/dev/ttyACM0"
subprocess.run(["stty", "-F", port, "-hupcl", "115200", "raw", "-echo"], check=True)
fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)


def send(data):
    """Write all of data: the port is non-blocking, so retry a short write or EAGAIN."""
    view = memoryview(data)
    deadline = time.time() + 5
    while view:
        try:
            view = view[os.write(fd, view) :]
        except BlockingIOError:
            if time.time() > deadline:
                sys.exit("console write timed out")
            time.sleep(0.01)


def pump(t):
    end, buf = time.time() + t, b""
    while time.time() < end:
        try:
            buf += os.read(fd, 4096)
        except BlockingIOError:
            time.sleep(0.05)
    print(buf.decode("utf-8", "replace").replace(psk, "***"), flush=True)


pump(0.5)
send(b"wifi status\n")
pump(2)
send(("wifi set %s %s\n" % (ssid, psk)).encode())
pump(20)
send(b"wifi status\n")
pump(2)
os.close(fd)
