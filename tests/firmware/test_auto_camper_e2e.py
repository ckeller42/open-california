"""Host tier: auto camper mode on the satellite end to end — real NimBLE host, the Bumble fake unit (R_FW_AUTO_CAMPER).

``cali-host --http`` pairs, reads and goes online; ``acstep 1000`` (cali-host only) makes the
auto-camper step every second instead of serve's 30 s. The fake unit's own clock (``MockCamperUnit.tick``)
does what the van does on an engine start: terminal 15 rises, ``campingmode.Enable`` follows and the
camping master is shed (the crank's link drop is off here: this peripheral never models it). Then:

* drive: camping on (+ rear USB + lights) -> ignition on -> shed -> ignition off -> the firmware writes
  campingmode master, usb and lights on and logs ``auto-camper[restored]``;
* manual off before the drive, or the feature switched off mid-drive -> no write at park;
* leisure battery at 10 % -> ``stood_down``, no write;
* the unit ACKs and ignores the master (its driving lock lingering) -> exactly 3 tries, then ``gave_up``;
* a restore owed survives a restart (the kv store = NVS).

.. test:: Auto camper on the host tier: restore after park, manual off, low SoC, bounded retries
   :id: T_FW_AUTO_CAMPER_E2E
   :links: R_FW_AUTO_CAMPER
"""

import time

import pytest

from calictl import control

from .test_control_e2e import ARM_S, WIFI, _online
from .test_web_e2e import _request, _wifi_script, get_json

pytestmark = [pytest.mark.xdist_group("firmware-host-build"), pytest.mark.linux_only]


@pytest.fixture(autouse=True)
def _no_crank_drop(monkeypatch):
    from tools import mock_unit

    monkeypatch.setattr(mock_unit, "CRANK_DROP_S", 0.0)  # the peripheral never models the drop's link loss


async def _set(fu, fn, vals):
    fu.unit.state[fn].update(vals)
    fu.unit.push(fn)


async def _tick(fu, vehicle=None):
    fu.unit.state["vehicle"].update(vehicle or {})
    fu.unit.tick(1.0)


async def _driving(fu, on):
    fu.unit.driving = on


def _camping_writes(hu):
    return [
        control.decode_control(hu.unit.funcs["campingmode"], f)
        for fn, f in hu.unit.unit.writes
        if fn == "campingmode"
    ]


def _ac(fw):
    return get_json(fw, "/api/state")["device"]["auto_camper"]


def _start(host_fw, hu, tmp_path, *, usb=1):
    """Online, camping on (master + usb + lights), the 1 s step, the unit's ignition edge detector primed."""
    fw = _online(host_fw, hu, tmp_path)
    hu.call(_tick, hu.unit)  # the mock's first tick only remembers terminal 15
    hu.call(
        _set, hu.unit, "campingmode", {"State": 1, "UsbCharger": usb, "InteriorLight": 0, "OutsideLight": 0}
    )
    fw.send("acstep 1000")
    end = time.monotonic() + 10
    while get_json(fw, "/api/state")["fn"]["campingmode"]["State"] != 1:
        assert time.monotonic() < end
        time.sleep(0.2)
    time.sleep(1.5)  # one step saw camping on
    assert _ac(fw) == {"enabled": True, "state": "idle"}
    return fw


def _drive(fw, hu):
    """Ignition on (the unit sheds camping), the firmware arms."""
    hu.call(_driving, hu.unit, True)
    hu.call(_tick, hu.unit, {"TerminalOneFive": 1})
    fw.expect("LOG", lambda s: s.startswith("auto-camper[armed_engine_shed]"), timeout=10)
    assert hu.unit.unit.state["campingmode"]["State"] == 0
    assert _ac(fw) == {"enabled": True, "state": "armed"}


def _park(hu, lock=False):
    hu.call(_driving, hu.unit, lock)
    hu.call(_tick, hu.unit, {"TerminalOneFive": 0})


def test_restores_camping_usb_and_lights_after_park(host_fw, rec_unit, tmp_path):
    hu, _ = rec_unit
    fw = _start(host_fw, hu, tmp_path)
    _drive(fw, hu)
    assert _camping_writes(hu) == []  # nothing while driving
    _park(hu)
    fw.expect("LOG", lambda s: s.startswith("auto-camper[restoring]"), timeout=10)
    fw.expect(
        "LOG",
        lambda s: s.startswith("auto-camper[restored]: Auto camper: camping restored after park."),
        timeout=10,
    )
    w = _camping_writes(hu)
    assert [x["State"] for x in w] == [1, 3, 3] and w[1]["UsbCharger"] == 1 and w[2]["InteriorLight"] == 0
    st = hu.unit.unit.state["campingmode"]
    assert (st["State"], st["UsbCharger"], st["InteriorLight"], st["OutsideLight"]) == (1, 1, 0, 0)
    assert _ac(fw) == {"enabled": True, "state": "idle"}


def test_a_restore_owed_survives_a_reboot(host_fw, rec_unit, tmp_path):
    """Armed, then the firmware restarts (same kv store = NVS): it boots owing the restore and
    restores at park."""
    hu, _ = rec_unit
    fw = _start(host_fw, hu, tmp_path)
    _drive(fw, hu)
    fw.stop()
    fw = host_fw(hu, http=True, fake_wifi=_wifi_script(tmp_path, WIFI))
    fw.expect("LOG", lambda s: s == "auto-camper: on (restore owed)")
    fw.expect("SNAP", timeout=40)
    fw.send("acstep 1000")
    time.sleep(ARM_S)
    _park(hu)
    fw.expect("LOG", lambda s: s.startswith("auto-camper[restored]"), timeout=15)
    assert hu.unit.unit.state["campingmode"]["State"] == 1


def test_manual_off_before_the_drive_is_respected(host_fw, rec_unit, tmp_path):
    hu, _ = rec_unit
    fw = _start(host_fw, hu, tmp_path)
    hu.call(_set, hu.unit, "campingmode", {"State": 0})  # switched off by hand while parked
    time.sleep(1.5)
    hu.call(_driving, hu.unit, True)
    hu.call(_tick, hu.unit, {"TerminalOneFive": 1})
    time.sleep(1.5)
    assert _ac(fw)["state"] == "idle"  # nothing to restore
    _park(hu)
    time.sleep(3)
    assert _camping_writes(hu) == [] and not any("auto-camper[" in s for s in fw.log)


def test_switched_off_mid_drive_never_restores(host_fw, rec_unit, tmp_path):
    hu, _ = rec_unit
    fw = _start(host_fw, hu, tmp_path)
    _drive(fw, hu)
    r = _request(fw, "POST", "/api/auto_camper", {"enabled": False})
    assert r.status == 200
    assert _ac(fw) == {"enabled": False, "state": "idle"}
    _park(hu)
    time.sleep(3)
    assert _camping_writes(hu) == [] and "LOG auto-camper: disabled" in fw.log


def test_low_battery_stands_down(host_fw, rec_unit, tmp_path):
    hu, _ = rec_unit
    fw = _start(host_fw, hu, tmp_path)
    _drive(fw, hu)
    hu.call(_set, hu.unit, "energy", {"SocTwoBattAfs": 1})  # 10 %
    _park(hu)
    line = fw.expect("LOG", lambda s: s.startswith("auto-camper[stood_down]"), timeout=10)
    assert "(SoC 10%)" in line and "soc=10" in line
    time.sleep(2)
    assert _camping_writes(hu) == []


def test_gives_up_after_three_refused_tries(host_fw, rec_unit, tmp_path):
    hu, _ = rec_unit
    fw = _start(host_fw, hu, tmp_path, usb=0)
    _drive(fw, hu)
    _park(hu, lock=True)  # the unit keeps ACKing and ignoring camping on
    fw.expect(
        "LOG",
        lambda s: s.startswith(
            "auto-camper[gave_up]: Auto camper gave up: camping would not re-enable after park."
        ),
        timeout=30,
    )
    time.sleep(2)
    assert [x["State"] for x in _camping_writes(hu)].count(1) == 3
    assert hu.unit.unit.state["campingmode"]["State"] == 0
    assert _ac(fw) == {"enabled": True, "state": "idle"}
