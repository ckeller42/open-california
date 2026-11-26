"""The OTA update end to end on the real image in QEMU (R_FW_OTA): a local fake release server in place of the
GitHub release, reached over QEMU's Ethernet (``CONFIG_CALI_QEMU_ETH``, probe build only).

The server serves ``/esp-stable/manifest.json`` and ``/esp-stable/cali_fw.bin`` like the esp-stable release
(``tools/esplab/promote_stable.sh``): the image is this build's own app, the manifest names another commit,
so the running image sees an update. The OTA task downloads it into ota_1, checks its SHA-256 read back from
flash, switches the boot slot and restarts; the bootloader boots ota_1 as pending verify. QEMU has no BLE,
so the image's own verdict cannot come (it needs the camper link): the test stops at "the new slot boots
pending verify", then rolls back with ``ota invalid`` (the console's rollback) to ota_0. A manifest whose
SHA-256 does not match the image is refused, and the image stays on ota_0.

Skipped unless ``CALI_QEMU=1`` (CI job ``firmware-qemu``).
"""

import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from .conftest import QEMU_BUILD

PORT = 8070  # CONFIG_CALI_OTA_URL_FMT in firmware/qemu/sdkconfig.qemu: http://10.0.2.2:8070/esp-%s/
OTHER_SHA = "f" * 40  # never the running build's commit


@pytest.fixture
def release():
    """A fake esp-stable release: ``release(sha256=None)`` sets the manifest (the image's own SHA-256 when
    None) and returns the list of the requested paths."""
    app = QEMU_BUILD / json.loads((QEMU_BUILD / "flasher_args.json").read_text())["app"]["file"]
    image = app.read_bytes()
    state = {"manifest": b"", "paths": []}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 (http.server's name)
            path = self.path.split("?")[0]
            state["paths"].append(path)
            body = {"/esp-stable/manifest.json": state["manifest"], "/esp-stable/cali_fw.bin": image}.get(
                path
            )
            self.send_response(200 if body is not None else 404)
            self.send_header("Content-Length", str(len(body or b"")))
            self.end_headers()
            self.wfile.write(body or b"")

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    def set_manifest(sha256=None):
        m = {"sha": OTHER_SHA, "size": len(image), "sha256": sha256 or hashlib.sha256(image).hexdigest()}
        state["manifest"] = json.dumps(m).encode()
        return state["paths"]

    yield set_manifest
    srv.shutdown()
    srv.server_close()


def ota_status(s):
    s.send("ota status")
    return s.expect("LOG ota: fw=")


def test_ota_install_boots_the_new_slot_pending_verify_then_rolls_back(qemu, release):
    """Install from the fake release, boot the new slot pending verify, then roll back.

    .. test:: OTA end to end in QEMU: manifest, download, SHA-256 check, reboot into ota_1 pending verify, rollback
       :id: T_FW_OTA_QEMU_E2E
       :links: R_FW_OTA
    """
    paths = release()
    s = qemu.boot()
    s.expect("STATE", lambda v: v["state"] == "idle", timeout=90)
    s.expect("LOG eth: up 10.0.2.", timeout=90)  # the DHCP lease comes after the boot's STATE
    assert " slot=ota_0 " in ota_status(s)
    s.send("ota install")
    s.expect("LOG ota: available fffffff", timeout=60)
    s.expect("LOG ota: installed fffffff, restarting", timeout=300)
    assert paths[:2] == ["/esp-stable/manifest.json", "/esp-stable/cali_fw.bin"], paths
    s.expect("LOG ota: ota_1 awaits its verdict", timeout=120)  # the same QEMU: the chip restarted
    s.expect("STATE", lambda v: v["state"] == "idle", timeout=90)
    st = ota_status(s)
    assert " slot=ota_1 img_state=1 " in st, st  # ESP_OTA_IMG_PENDING_VERIFY
    s.send("ota invalid")  # the rollback a person (or a failed verdict) starts
    s.expect("ESP-ROM:", timeout=60)  # the chip restarts
    s.expect("STATE", lambda v: v["state"] == "idle", timeout=120)
    st = ota_status(s)
    assert " slot=ota_0 " in st, st


def test_ota_with_a_wrong_checksum_is_refused(qemu, release):
    """A manifest whose SHA-256 is not the image's: the install is refused and ota_0 stays.

    .. test:: OTA in QEMU: an image whose SHA-256 is not the manifest's is never booted
       :id: T_FW_OTA_QEMU_CHECKSUM
       :links: R_FW_OTA
    """
    release(sha256="0" * 64)
    s = qemu.boot()
    s.expect("STATE", lambda v: v["state"] == "idle", timeout=90)
    s.expect("LOG eth: up 10.0.2.", timeout=90)  # the DHCP lease comes after the boot's STATE
    s.send("ota install")
    s.expect("LOG ota: error checksum", timeout=300)
    assert " slot=ota_0 " in ota_status(s)
    s.stop()
    s2 = qemu.boot()  # and the next boot is still the old slot
    s2.expect("STATE", lambda v: v["state"] == "idle", timeout=90)
    assert " slot=ota_0 " in ota_status(s2)
    assert not any("awaits its verdict" in line for line in s2.log)
