"""Host tier: the BLE link modes over ``/api/ble`` end to end — real NimBLE host, the Bumble fake unit
(R_FW_BLE_MODE, R_FW_BLE_EXP).

``POST /api/ble {"mode":"quiet"}``: the link stays up and the unit receives no ``1003`` heartbeat;
``{"mode":"off"}``: the link drops (``link_down`` reason ``ble_off``) and the firmware does not
reconnect; ``/api/state``'s ``device.ble`` reports the mode.

.. test:: BLE modes on the host tier: quiet stops the heartbeat, off drops the link for good
   :id: T_FW_BLE_MODE_E2E
   :links: R_FW_BLE_MODE
"""

import json
import time

import pytest

from .test_control_e2e import _beats, _online
from .test_web_e2e import _request, get_json

pytestmark = [pytest.mark.xdist_group("firmware-host-build"), pytest.mark.linux_only]


def _post(fw, body):
    r = _request(fw, "POST", "/api/ble", body)
    assert r.status == 200, (r.status, r.body)
    return json.loads(r.body)


def test_quiet_stops_the_heartbeat_and_off_drops_the_link(host_fw, rec_unit, tmp_path):
    hu, rec = rec_unit
    fw = _online(host_fw, hu, tmp_path)
    assert get_json(fw, "/api/state")["device"]["ble"] == {"mode": "hold", "probing": False, "exp": None}

    assert _post(fw, {"mode": "quiet"})["mode"] == "quiet"
    time.sleep(1)  # a beat already on the air may still land
    n = len(_beats(rec))
    time.sleep(3)
    assert len(_beats(rec)) == n  # quiet: no 1003 for 3 s ...
    assert get_json(fw, "/api/state")["device"]["link"]["up"] is True  # ... and the link held

    assert _post(fw, {"mode": "off"})["mode"] == "off"
    fw.expect("REC", lambda v: '"kind":"link_down","reason":"ble_off"' in v, timeout=5)
    since = len(fw.log)
    time.sleep(10)
    assert not [s for s in fw.log[since:] if '"kind":"link_up"' in s]  # off: no reconnect
    st = get_json(fw, "/api/state")["device"]
    assert (st["ble"]["mode"], st["link"]["up"]) == ("off", False)
