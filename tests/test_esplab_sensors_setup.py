"""tools/esplab/sensors_setup.sh against a stub of the ESP's /api/sensors (R_FW_SENSORS setup).

The stub shows two Victron chargers (a neighbour's and ours), one Battery Sense and two Govees;
``add`` with the wrong device answers 400 ``key_mismatch`` like the firmware's key-check byte.

.. test:: Sensor setup script: keys from files, key-mismatch skip, strongest Govee, idempotent
   :id: T_ESP_SENSORS_SETUP
   :links: R_FW_SENSORS
"""

import json
import os
import pathlib
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "esplab" / "sensors_setup.sh"
# built, not written: a 32-hex literal next to "KEY" trips the gitleaks working-tree scan in CI
KEY_MPPT = bytes(range(0x00, 0x100, 0x11)).hex()  # 00112233…eeff
KEY_BATT = bytes(reversed(range(0x00, 0x100, 0x11))).hex()  # ffeedd…1100


class Esp:
    """One ESP: a seen table, a configured list, the adds it received."""

    def __init__(self):
        self.seen = [  # id -> (type, model, rssi, the key the device would accept)
            (11, "victron_mppt", "SmartSolar", -80, bytes([0xDE, 0xAD, 0xBE, 0xEF] * 4).hex()),
            (12, "victron_mppt", "SmartSolar", -55, KEY_MPPT),
            (21, "victron_battery", "BatterySense", -60, KEY_BATT),
            (31, "govee", "GVH5075", -70, None),
            (32, "govee", "GVH5075", -50, None),
        ]
        self.configured = []
        self.adds = []
        self.experimental = False

    def api(self):
        return {
            "configured": [
                {"id": i, "type": t, "alias": a, "last_seen_s": 0, "value": None, "has_key": k is not None}
                for i, t, a, k in self.configured
            ],
            "seen": [
                {"id": i, "type": t, "model": m, "rssi": r}
                for i, t, m, r, _ in self.seen
                if i not in {c[0] for c in self.configured}
            ],
            "scan": "ok",
        }

    def add(self, body):
        self.adds.append(body)
        row = next((s for s in self.seen if s[0] == body["add"]), None)
        if row is None:
            return 400, {"ok": False, "error": "unknown_id"}
        want = row[4]
        if want is not None and body.get("key") != want:
            return 400, {"ok": False, "error": "key_mismatch"}
        self.configured.append((row[0], row[1], body["alias"], body.get("key")))
        return 200, self.api()


def serve(esp):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _json(self, code, obj):
            data = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == "/api/sensors":
                return self._json(200, esp.api())
            if self.path == "/api/state":
                return self._json(200, {"ext": {c[2]: {"age_s": 1} for c in esp.configured}})
            self._json(404, {})

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path == "/api/experimental":
                esp.experimental = bool(body["sensors"])
                return self._json(200, {"ok": True, "sensors": esp.experimental})
            if self.path == "/api/sensors":
                return self._json(*esp.add(body))
            self._json(404, {})

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


@pytest.mark.skipif(not shutil.which("curl") or not shutil.which("bash"), reason="needs bash + curl")
def test_setup_adds_our_devices_only(tmp_path):
    esp = Esp()
    srv = serve(esp)
    (tmp_path / "mppt.key").write_text(KEY_MPPT + "\n")
    (tmp_path / "batt.key").write_text(KEY_BATT + "\n")
    env = dict(
        os.environ,
        MPPT_KEY_FILE=str(tmp_path / "mppt.key"),
        BATT_KEY_FILE=str(tmp_path / "batt.key"),
        WAIT_MIN="1",
        SETTLE_S="0",
    )
    p = subprocess.run(
        ["bash", str(SCRIPT), "http://127.0.0.1:%d" % srv.server_port],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    srv.shutdown()
    assert p.returncode == 0, p.stdout + p.stderr
    assert esp.experimental
    assert [(c[0], c[2]) for c in esp.configured] == [
        (12, "mppt-75-15"),
        (21, "battery-sense"),
        (32, "govee"),
    ]
    assert [a["add"] for a in esp.adds] == [12, 21, 32]  # strongest first: the neighbour (-80) never tried
    assert KEY_MPPT not in p.stdout + p.stderr and KEY_BATT not in p.stdout + p.stderr
    assert '"govee"' in p.stdout  # the ext block at the end


@pytest.mark.skipif(not shutil.which("curl") or not shutil.which("bash"), reason="needs bash + curl")
def test_setup_skips_a_wrong_key_device_and_is_idempotent(tmp_path):
    esp = Esp()
    esp.seen[1] = (12, "victron_mppt", "SmartSolar", -90, KEY_MPPT)  # ours is now the weaker one
    srv = serve(esp)
    (tmp_path / "mppt.key").write_text(KEY_MPPT)
    (tmp_path / "batt.key").write_text(KEY_BATT)
    env = dict(
        os.environ,
        MPPT_KEY_FILE=str(tmp_path / "mppt.key"),
        BATT_KEY_FILE=str(tmp_path / "batt.key"),
        WAIT_MIN="1",
        SETTLE_S="0",
    )
    for _ in range(2):
        p = subprocess.run(
            ["bash", str(SCRIPT), "http://127.0.0.1:%d" % srv.server_port],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert p.returncode == 0, p.stdout + p.stderr
    srv.shutdown()
    assert [a["add"] for a in esp.adds] == [
        11,
        12,
        21,
        32,
    ]  # 11 refused (key_mismatch), then ours; 2nd run adds nothing
    assert "not ours (key mismatch)" in p.stdout or "already configured" in p.stdout
    assert len(esp.configured) == 3
