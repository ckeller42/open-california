"""The HIL board backend without a board: the console on a pty with a scripted firmware behind it, the port
guard, the drift report, and tools/esplab/hil.sh refusing to run without a named HIL board.

.. test:: HIL plumbing: console without reset, port guard, drift report, hil.sh refusals
   :id: T_FW_HIL_PLUMBING
   :links: R_FW_LORA
"""

import os
import pty
import subprocess
import threading
from pathlib import Path

import pytest

from . import hil

ROOT = Path(__file__).resolve().parents[2]
ANSWERS = {
    "lora at RX2DR": ["LOG lora: >> AT+RX2DR=?", "LOG lora: << AT+RX2DR=3", "LOG lora: << OK"],
    "lora status": ["LOG lora: on, module present, joined"],
}


def fake_firmware(master, stop):
    """Answer console lines like the satellite would (\\r\\n line ends, as its console writes)."""
    buf = b""
    while not stop.is_set():
        try:
            buf += os.read(master, 1024)
        except OSError:
            return
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            for out in ANSWERS.get(line.decode().strip(), [f"LOG console: unknown {line.decode().strip()}"]):
                os.write(master, (out + "\r\n").encode())


@pytest.fixture
def pty_board():
    master, slave = pty.openpty()
    stop = threading.Event()
    th = threading.Thread(target=fake_firmware, args=(master, stop), daemon=True)
    th.start()
    yield os.ttyname(slave)
    stop.set()
    os.close(slave)
    os.close(master)


def test_board_target_sends_steps_and_parses_the_transcript(pty_board):
    res = hil.BoardTarget(pty_board, check=lambda p: p).run(
        [(0.1, "lora at RX2DR"), (0.4, "lora status")], 1.0
    )
    assert res.answers("AT+RX2DR=?") == [["AT+RX2DR=3", "OK"]]
    assert [line for _, line in res.log][-1] == "LOG lora: on, module present, joined"
    assert all(0 <= t <= 1500 for t, _ in res.log)


@pytest.mark.parametrize(
    "port, ok",
    [
        ("", False),
        ("/dev/ttyACM0", False),
        ("/dev/ttyUSB0", False),
        ("/dev/serial/by-id/usb-Espressif_USB_JTAG_serial_debug_unit_AA:BB:CC:DD:EE:FF-if00", True),
    ],
)
def test_port_guard(port, ok):
    if ok:
        assert hil.check_port(port) == port
    else:
        with pytest.raises(ValueError):
            hil.check_port(port)


def result(target, lines):
    return hil.Result(target, [(i * 10, line) for i, line in enumerate(lines)])


def test_drift_reports_differences_only():
    same = ["LOG lora: >> AT+DR=?", "LOG lora: << AT+DR=0", "LOG lora: << OK"]
    assert hil.drift(result("sim", same), result("board", same)) == []
    other = ["LOG lora: >> AT+DR=?", "LOG lora: << AT_ERROR"]
    assert hil.drift(result("sim", same), result("board", other)) == [
        "AT+DR=?: answers sim ['AT+DR=0', 'OK'] vs board ['AT_ERROR']"
    ]
    other_value = [same[0], "LOG lora: << AT+DR=3", same[2]]
    assert hil.drift(result("sim", same), result("board", other_value))
    regs = ["LOG i2c: 0x58 reg 0x02 = 0x11"]
    assert hil.drift(result("sim", regs), result("board", ["LOG i2c: 0x58 reg 0x02 = 0x13"])) == [
        "i2c 0x58 reg 0x02: sim 0x11, board 0x13"
    ]
    slow = hil.Result("board", [(0, same[0]), (5000, same[1]), (5000, same[2])])
    assert [d for d in hil.drift(result("sim", same), slow) if "after" in d]


@pytest.mark.parametrize(
    "env, why",
    [
        ({}, "HIL_PORT is not set"),
        ({"HIL_PORT": "/dev/ttyUSB0"}, "not a /dev/serial/by-id/usb-Espressif"),
        ({"HIL_PORT": "/dev/serial/by-id/usb-Espressif_x-if00"}, "does not exist"),
    ],
)
def test_hil_sh_refuses_without_the_named_board(env, why):
    r = subprocess.run(
        ["bash", str(ROOT / "tools/esplab/hil.sh"), "identify"],
        env={"PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", "/tmp"), **env},
        capture_output=True,
        text=True,
    )
    assert r.returncode == 2 and why in r.stderr, r.stderr
