"""Host tier: the verdict on a freshly installed image over real NimBLE and the Bumble fake unit (R_FW_OTA).

The firmware pairs, stops (the OTA's restart) and starts again on the same bond store, then acts as a
freshly installed image (the test-only ``otaverify`` line: cali_ota_verify_begin with short times). The
unit advertises; whether it lets the link come up decides the verdict. The sighting comes from the
sensor scan windows (one a minute), so ``sensors on``.

The decision table itself (every input, the shipped 1 / 15 / 40 / 5 min): ``test_ota.py``. The
bootloader side (an unconfirmed image rolls back on the next reset) is ESP-only: the bench run.
"""

import pytest

from .test_host_e2e import _pair

pytestmark = [pytest.mark.xdist_group("firmware-host-build"), pytest.mark.linux_only]

# min 0, T 10 min (never reached here), W 30 s, gone 90 s (> the 60 s between two scan windows)
VERIFY = "otaverify 0 600000 30000 90000"


async def _set_refuse(unit, on):  # hu.call runs a coroutine on the unit loop
    unit.refuse_connections = on


def _fresh_image(host_fw, hu, tmp_path, refuse):
    store = tmp_path / "store"
    fw = host_fw(hu, store_dir=store)
    assert _pair(fw, hu)["state"] == "bonded"
    fw.expect("SNAP", timeout=40)
    fw.stop()
    hu.call(_set_refuse, hu.unit, refuse)
    fw2 = host_fw(hu, store_dir=store)  # the restart into the new image: the bond is kept
    fw2.send("sensors on")
    fw2.send(VERIFY)
    fw2.expect("LOG otaverify: pending")
    return fw2


def _verdict(fw, timeout):
    line = fw.expect("LOG", lambda v: v.startswith("ota: image "), timeout=timeout)
    return line.split()[2:4]  # ["failed", "(no_link)"]


def test_unit_advertises_but_refuses_the_link_fails_the_image(host_fw, hci_unit, tmp_path):
    """The failure signature: the bonded unit keeps advertising, the firmware keeps dialling, and no
    link comes up within W. The image is failed (never confirmed), and a link later changes nothing.

    .. test:: OTA verdict on the host tier: the unit advertises but refuses the link -> failed
       :id: T_FW_OTA_VERIFY_E2E_NO_LINK
       :links: R_FW_OTA
    """
    fw = _fresh_image(host_fw, hci_unit, tmp_path, refuse=True)
    assert _verdict(fw, timeout=120) == ["failed", "(no_link)"]
    assert not any(line.startswith("SNAP") for line in fw.log)  # no session ever came up


def test_unit_lets_the_link_up_confirms_the_image(host_fw, hci_unit, tmp_path):
    """The same restart with a unit that accepts the link: valid at once (the link latched).

    .. test:: OTA verdict on the host tier: the link comes up -> valid
       :id: T_FW_OTA_VERIFY_E2E_LINK
       :links: R_FW_OTA
    """
    fw = _fresh_image(host_fw, hci_unit, tmp_path, refuse=False)
    assert _verdict(fw, timeout=60) == ["valid", "(link)"]
