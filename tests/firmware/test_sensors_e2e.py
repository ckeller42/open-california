"""External BLE sensors end to end (R_FW_SENSORS): cali-host's NimBLE opens the passive window on
the Bumble link, a FakeAdvertiser (conftest.py) plays a Govee, the console drives the setup."""

import time

import pytest

from .conftest import FakeAdvertiser
from .test_host_e2e import _pair
from .test_sensors import GOVEE_H5074, KEY, SOLAR

pytestmark = [pytest.mark.linux_only, pytest.mark.xdist_group("firmware-host-build")]


def _first_seen(fw, tries=90):
    """Poll ``sensors`` once a second until the seen table has a row (the first window opens on
    the next tick after the flag; the fake advertises every 100 ms)."""
    for _ in range(tries):
        fw.send("sensors")
        api = fw.expect("SENSORS")
        if api["seen"]:
            return api["seen"][0]
        time.sleep(1)
    return None


def test_window_sees_govee_configures_and_logs_ext(host_fw, hci_unit):
    """The window finds a Govee, the console configures it, its ext record lands.

    .. test:: Sensor window end to end (host NimBLE + Bumble)
       :id: T_FW_SENSORS_E2E
       :links: R_FW_SENSORS
    """
    fw = host_fw(hci_unit)
    assert _pair(fw, hci_unit)["state"] == "bonded"
    FakeAdvertiser(hci_unit, "A4:C1:38:00:00:02", bytes.fromhex(GOVEE_H5074), "GVH5074_0002").start()
    fw.send("sensors on")
    fw.expect("LOG exp: sensors", lambda v: v == "on")
    seen = _first_seen(fw)
    assert seen and seen["type"] == "govee" and seen["model"] == "GVH5074"
    fw.send("sensors add %d kitchen -" % seen["id"])
    fw.expect("LOG sensors", lambda v: v == "ok")
    rec = fw.expect(
        "REC", lambda v: v.startswith('"ev":"ext","dev":"kitchen"'), timeout=90
    )  # the next window
    assert '"type":"govee","temp_c":23.96,"humidity":58.48,"battery":90' in rec
    fw.send("sensors")
    assert fw.expect("SENSORS")["configured"][0]["value"] == {
        "temp_c": 23.96,
        "humidity": 58.48,
        "battery": 90,
    }


def _victron_aes(model, mode, plain, iv=0xBEEF):
    """A Victron Instant Readout advert encrypted with REAL AES-128-CTR (counter = iv as a 128-bit
    little-endian int): the only test of ble_nimble.c's aes128_ecb keystream short of the board."""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    enc = Cipher(algorithms.AES(KEY), modes.CTR(iv.to_bytes(2, "little") + bytes(14))).encryptor()
    cipher = enc.update(plain) + enc.finalize()
    return (
        bytes([0xE1, 0x02, 0x10, 0x00])
        + model.to_bytes(2, "little")
        + bytes([mode])
        + iv.to_bytes(2, "little")
        + KEY[:1]
        + cipher
    )


def test_window_decodes_victron_with_real_aes(host_fw, hci_unit):
    """A SmartSolar advert encrypted in Python with the test key decodes on host NimBLE (PSA AES)."""
    fw = host_fw(hci_unit)
    # name <= 5 chars: name AD (2 + 5) + mfg AD (2 + 22) = the 31-byte advert
    FakeAdvertiser(hci_unit, "DA:4B:25:00:00:01", _victron_aes(0xA075, 0x01, SOLAR), "SS_01").start()
    fw.send("sensors on")
    fw.expect("LOG exp: sensors", lambda v: v == "on")
    seen = _first_seen(fw)
    assert seen and seen["type"] == "victron_mppt"
    fw.send("sensors add %d - %s" % (seen["id"], KEY.hex()))
    fw.expect("LOG sensors", lambda v: v == "ok")
    rec = fw.expect("REC", lambda v: v.startswith('"ev":"ext","dev":"victron_mppt"'), timeout=90)
    assert (
        '"type":"victron_mppt","battery_current":2.3,"battery_voltage":13.52,"charge_state":5,'
        '"error_code":0,"pv_power":87,"yield_today_kwh":0.42' in rec
    )  # load_current n/a: left out
    assert "load_current" not in rec


def test_sensors_window_yields_to_pairing(host_fw, hci_unit):
    """A pairing flow started while windows run still bonds; the runner's scan is never cancelled by a window."""
    fw = host_fw(hci_unit)
    FakeAdvertiser(hci_unit, "A4:C1:38:00:00:02", bytes.fromhex(GOVEE_H5074), "GVH5074_0002").start()
    fw.send("sensors on")
    fw.expect("LOG exp: sensors", lambda v: v == "on")
    time.sleep(3)  # a window is open (the first one opens on the next tick, for 5 s)
    assert _pair(fw, hci_unit)["state"] == "bonded"  # pair + passkey, as test_host_e2e does
    time.sleep(0.5)  # scan_state follows the runner on the sensor tick (100 ms)
    fw.send("sensors")
    assert fw.expect("SENSORS")["scan"] == "ok"


def test_flag_off_opens_no_window(host_fw, hci_unit):
    """Off (the default): no window, so nothing is seen even with a Govee advertising next door."""
    fw = host_fw(hci_unit)
    FakeAdvertiser(hci_unit, "A4:C1:38:00:00:02", bytes.fromhex(GOVEE_H5074), "GVH5074_0002").start()
    fw.expect("LOG exp: sensors", lambda v: v == "off")
    time.sleep(3)
    fw.send("sensors")
    assert fw.expect("SENSORS")["seen"] == []
