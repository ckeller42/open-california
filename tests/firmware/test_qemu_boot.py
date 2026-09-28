"""QEMU boot tier: the esp32s3 image boots in Espressif's QEMU (#154, Task 9).

The "chip simulator" tier between the host build (NimBLE on Linux, ``test_host_e2e.py``) and
hardware: the real ESP-IDF image (QEMU-probe variant, ``firmware/qemu/sdkconfig.qemu``: UART0
console + ``CONFIG_CALI_QEMU_PROBE``) boots on an emulated esp32s3 via ``firmware/qemu/run_qemu.sh``.
QEMU has no Bluetooth, so the firmware takes its no-controller path (``LOG ble: controller
unavailable``) and must still print the idle ``STATE`` and answer the console. ``kvprobe`` (compiled
only into the probe variant) writes/reads a record through ``cali_kv_*`` = NVS, the bond store's
path, and the second boot on the same flash file proves it survives a reboot. The probe build also
loads the bond store (``ble_store_kv.c``) from NVS at boot, so a record damaged in real NVS
(``kvprobe corrupt``) is met by the real ``platform_esp.c`` CRC check.

Skipped unless ``CALI_QEMU=1`` (CI job ``firmware-qemu``; locally see firmware/README.md).
"""

import time

BOOT_BANNER = "ESP-ROM:"  # the ROM's first line on every (re)boot of the chip


def test_boots_unpaired_and_nvs_survives_reboot(qemu):
    s = qemu.boot()
    s.expect("LOG ble: controller unavailable", timeout=90)
    s.expect("STATE", lambda v: v["state"] == "idle", timeout=90)
    s.send("status")
    assert s.expect("STATE")["state"] == "idle"
    s.send("kvprobe set 0a0b0c")
    s.expect("LOG kvprobe ok")
    s.stop()
    s2 = qemu.boot()  # same flash.bin
    s2.expect("STATE", lambda v: v["state"] == "idle", timeout=90)
    s2.send("kvprobe get")
    assert s2.expect("LOG kvprobe").endswith("0a0b0c")


def test_corrupt_bond_record_boots_unpaired(qemu):
    """A CRC-broken first peer-bond record (``sec_peer_0``, ble_store_kv.c's slot key) in real NVS:
    the next boot logs it ignored, comes up idle, keeps the intact records, and does not crash."""
    s = qemu.boot()
    s.expect("STATE", lambda v: v["state"] == "idle", timeout=90)
    s.send("kvprobe set 0a0b0c")
    s.expect("LOG kvprobe ok")
    s.send("kvprobe corrupt sec_peer_0")
    s.expect("LOG kvprobe corrupt sec_peer_0 ok")
    s.send("kvprobe get sec_peer_0")
    assert s.expect("LOG kvprobe get") == "corrupt"
    s.stop()
    s2 = qemu.boot()  # same flash.bin, now with the bad record
    s2.expect("LOG store: corrupt record sec_peer_0 ignored", timeout=90)
    s2.expect("STATE", lambda v: v["state"] == "idle", timeout=90)
    s2.send("kvprobe get sec_peer_0")
    assert s2.expect("LOG kvprobe get") in ("corrupt", "missing")
    s2.send("kvprobe get")
    assert s2.expect("LOG kvprobe get") == "0a0b0c"
    s2.send("status")
    assert s2.expect("STATE")["state"] == "idle"
    time.sleep(3)  # a crash would reboot within ~0.4 s (QEMU)
    s2.send("status")
    assert s2.expect("STATE")["state"] == "idle"
    assert sum(line.startswith(BOOT_BANNER) for line in s2.log) == 1, "rebooted:\n" + "\n".join(s2.log)


def test_no_wifi_driver_boots_and_serves_nothing(qemu):
    """QEMU's esp32s3 has no WiFi radio, and the QEMU image is built without the WiFi driver
    (``qemu/sdkconfig.qemu``: ``CONFIG_CALI_WIFI=n``), so ``cali_net_esp_init`` returns -1: the
    firmware logs ``LOG wifi: driver unavailable`` once, never starts the WiFi runner or the HTTP
    server, and keeps answering the console. The WiFi SM stays ``WIFI_UNPROVISIONED`` = mode "off",
    which ``status`` shows by leaving the ``wifi`` member out (console.c: it appears only once the
    runner booted) and ``wifi status`` by ``LOG wifi: not enabled``. No crash loop.

    .. test:: A board without a working WiFi driver boots with WiFi off (QEMU tier)
       :id: T_FW_QEMU_NO_WIFI_DRIVER
       :links: R_FW_WIFI_PROVISION
    """
    s = qemu.boot()
    s.expect("LOG wifi: driver unavailable", timeout=90)
    s.expect("STATE", lambda v: v["state"] == "idle", timeout=90)
    s.send("status")
    v = s.expect("STATE")
    assert v["state"] == "idle"
    assert "wifi" not in v, v  # WiFi off: the runner never booted
    s.send("wifi status")
    s.expect("LOG wifi: not enabled")
    time.sleep(3)  # a crash would reboot within ~0.4 s (QEMU)
    s.send("status")
    assert s.expect("STATE")["state"] == "idle"
    assert sum(line.startswith(BOOT_BANNER) for line in s.log) == 1, "rebooted:\n" + "\n".join(s.log)
    assert sum(line.strip() == "LOG wifi: driver unavailable" for line in s.log) == 1, "\n".join(s.log)
