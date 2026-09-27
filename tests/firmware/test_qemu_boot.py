"""QEMU boot tier: the esp32s3 image boots in Espressif's QEMU (#154, Task 9).

The "chip simulator" tier between the host build (NimBLE on Linux, ``test_host_e2e.py``) and
hardware: the real ESP-IDF image (QEMU-probe variant, ``firmware/qemu/sdkconfig.qemu``: UART0
console + ``CONFIG_CALI_QEMU_PROBE``) boots on an emulated esp32s3 via ``firmware/qemu/run_qemu.sh``.
QEMU has no Bluetooth, so the firmware takes its no-controller path (``LOG ble: controller
unavailable``) and must still print the idle ``STATE`` and answer the console. ``kvprobe`` (compiled
only into the probe variant) writes/reads a record through ``cali_kv_*`` = NVS, the bond store's
path, and the second boot on the same flash file proves it survives a reboot.

Skipped unless ``CALI_QEMU=1`` (CI job ``firmware-qemu``; locally see firmware/README.md).
"""


def test_boots_unpaired_and_nvs_survives_reboot(qemu):
    s = qemu.boot()
    s.expect("LOG ble: controller unavailable", timeout=90)
    s.expect("STATE", lambda v: v["state"] == "idle", timeout=90)
    s.send("status"); assert s.expect("STATE")["state"] == "idle"
    s.send("kvprobe set 0a0b0c"); s.expect("LOG kvprobe ok")
    s.stop()
    s2 = qemu.boot()                                   # same flash.bin
    s2.expect("STATE", lambda v: v["state"] == "idle", timeout=90)
    s2.send("kvprobe get"); assert s2.expect("LOG kvprobe").endswith("0a0b0c")
