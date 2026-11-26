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

import binascii
import json
import struct
import subprocess
import sys
import time

from .conftest import QEMU_BUILD

BOOT_BANNER = "ESP-ROM:"  # the ROM's first line on every (re)boot of the chip


def test_boots_unpaired_and_nvs_survives_reboot(qemu):
    """Also the boot counter (``R_FW_TIME``): 1 on the fresh flash, 2 after the reboot, in real NVS.

    .. test:: The boot counter persists in NVS and counts every boot (QEMU tier)
       :id: T_FW_TIME_QEMU
       :links: R_FW_TIME
    """
    s = qemu.boot()
    assert s.expect("LOG time: boot", timeout=90) == "1"
    assert s.expect("LOG exp: sensors", timeout=90) == "off"
    s.expect("LOG ble: controller unavailable", timeout=90)
    s.expect("STATE", lambda v: v["state"] == "idle", timeout=90)
    s.send("status")
    assert s.expect("STATE")["state"] == "idle"
    s.send("kvprobe set 0a0b0c")
    s.expect("LOG kvprobe ok")
    s.stop()
    s2 = qemu.boot()  # same flash.bin
    assert s2.expect("LOG time: boot", timeout=90) == "2"
    s2.expect("STATE", lambda v: v["state"] == "idle", timeout=90)
    s2.send("kvprobe get")
    assert s2.expect("LOG kvprobe").endswith("0a0b0c")


def test_corrupt_bond_record_boots_unpaired(qemu):
    """A CRC-broken first peer-bond record (``sec_peer_0``, ble_store_kv.c's slot key) in real NVS:
    the next boot logs it ignored, comes up idle, keeps the intact records, and does not crash.

    The probe build has no BLE transport, so ``has_bond()`` is always 0 and "idle" holds whatever the
    store did: it is no evidence here. The evidence is the ``LOG store: corrupt record`` line plus
    the single boot banner (no crash loop), and both are asserted."""
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


def test_boots_with_sensors_flag_on(qemu):
    """R_FW_SENSORS: the flag survives a reboot and a board without a controller (QEMU) boots with it
    on — the window is never opened (scan_window refuses), nothing crashes."""
    s = qemu.boot()
    s.expect("STATE", lambda v: v["state"] == "idle", timeout=90)
    s.send("sensors on")
    assert s.expect("LOG exp: sensors") == "on"
    s.stop()
    s2 = qemu.boot()  # same flash.bin
    assert s2.expect("LOG exp: sensors", timeout=90) == "on"
    s2.expect("STATE", lambda v: v["state"] == "idle", timeout=90)
    time.sleep(3)  # a crash would reboot within ~0.4 s (QEMU)
    s2.send("sensors")
    assert s2.expect("SENSORS")["seen"] == []
    assert sum(line.startswith(BOOT_BANNER) for line in s2.log) == 1, "rebooted:\n" + "\n".join(s2.log)


def _otadata_entry(seq, state):
    """One ``esp_ota_select_entry_t`` (esp_flash_partitions.h): seq, 20 label bytes, state, CRC32 of
    seq as the bootloader computes it (``esp_rom_crc32_le(UINT32_MAX, &seq, 4)``, = otatool.py)."""
    crc = binascii.crc32(struct.pack("<I", seq), 0xFFFFFFFF)
    return struct.pack("<I20sII", seq, b"\xff" * 20, state, crc)


def test_unconfirmed_ota_image_rolls_back_on_reset(qemu):
    """The bootloader side of the rollback, on the real image and bootloader (R_FW_OTA). The flash
    holds what an OTA install leaves: the confirmed image in ota_0 (otadata seq 1, VALID) and the new
    one in ota_1 (seq 2, NEW, what ``esp_ota_set_boot_partition`` writes). The bootloader must boot
    ota_1 as pending verify, the image must see that and not confirm itself (QEMU has no web server,
    so the verdict stays pending), and the next reset must boot ota_0 again. Fails when rollback is
    off in the bootloader, when the image confirms itself at boot, or when it misreads its state.

    .. test:: An OTA image that has not confirmed itself boots the previous slot after a reset (QEMU tier)
       :id: T_FW_OTA_ROLLBACK_QEMU
       :links: R_FW_OTA
    """
    fa = json.loads((QEMU_BUILD / "flasher_args.json").read_text())
    parts = {"otadata": int(fa["otadata"]["offset"], 16), "ota_0": int(fa["app"]["offset"], 16)}
    ota_1 = 0x320000  # partitions.csv (tests/test_fw_flash_safety.py pins the layout)
    subprocess.run(
        [sys.executable, "-m", "esptool", "--chip", "esp32s3", "merge-bin", "--pad-to-size",
         fa["flash_settings"]["flash_size"], "-o", str(qemu.flash), "@flash_args",
         hex(ota_1), fa["app"]["file"]],
        cwd=QEMU_BUILD, check=True, capture_output=True,
    )  # fmt: skip
    img = bytearray(qemu.flash.read_bytes())
    sector = 0x1000
    img[parts["otadata"] : parts["otadata"] + 32] = _otadata_entry(1, 2)  # ota_0, ESP_OTA_IMG_VALID
    img[parts["otadata"] + sector : parts["otadata"] + sector + 32] = _otadata_entry(2, 0)  # ota_1, NEW
    qemu.flash.write_bytes(bytes(img))

    s = qemu.boot()
    s.expect("LOG ota: ota_1 awaits its verdict", timeout=90)
    s.expect("STATE", lambda v: v["state"] == "idle", timeout=90)
    s.send("ota status")
    st = s.expect("LOG ota: fw=")
    assert " slot=ota_1 img_state=1 " in st, st  # ESP_OTA_IMG_PENDING_VERIFY
    time.sleep(3)
    s.stop()  # a reset before any verdict

    s2 = qemu.boot()
    s2.expect("STATE", lambda v: v["state"] == "idle", timeout=90)
    s2.send("ota status")
    st = s2.expect("LOG ota: fw=")
    assert " slot=ota_0 img_state=2 " in st, st  # back on the confirmed image
    assert not any("awaits its verdict" in line for line in s2.log), "\n".join(s2.log)
