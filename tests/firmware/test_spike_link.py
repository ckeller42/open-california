"""Spike (deleted in Task 6): the NimBLE Linux host pairs with the Bumble fake unit over TCP HCI."""
import subprocess

import pytest

from .conftest import HOST_DIR, Firmware

pytestmark = pytest.mark.linux_only


def _build(target):
    subprocess.run(["bash", str(HOST_DIR / "fetch_nimble.sh")], check=True)
    subprocess.run(["make", "-C", str(HOST_DIR), target], check=True)
    return HOST_DIR / target


def test_spike_pairs_and_reads_1004(hci_unit):
    fw = Firmware(_build("cali-spike"), hci_unit.port)
    try:
        fw.expect("PASSKEY?", timeout=30)
        fw.send("%06d" % hci_unit.call(hci_unit.unit.next_passkey))
        fw.expect("ENCRYPTED", timeout=30)
        fw.expect("READ1004", pred=lambda v: len(v) > 0, timeout=15)
    finally:
        fw.stop()
