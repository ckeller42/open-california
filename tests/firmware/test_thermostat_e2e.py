"""Host tier: the cabin thermostat runner end to end — real NimBLE host, the Bumble fake unit and a
fake Govee advert (R_FW_THERMOSTAT_RUN).

cali-host pairs and reads; a FakeAdvertiser plays a Govee H5074 configured as ``cabin``; the
test-only ``thermo`` lines of cali-host (host_main.c) switch the feature, set the reference, the
target and a fast period, and ask for AUTO. The fake unit applies the heater writes and pushes 1702.

* a cold cabin (17.0 C, target 21.0 C) -> the START frames (runtime, level, power on), byte for byte
  calictl's, then a colder reading (16.0 C, a plausible rate) raises the level;
* the reference thermometer removed while AUTO -> REFERENCE_LOST, level 1 (no other sensor picked);
* the heater level changed on the unit -> FOREIGN_CHANGE, AUTO ends, nothing written;
* a power-save hang-up with camping off while AUTO -> a reconnect, not the rest, and 1702 re-read;
* the per-step ``thermo`` records (only while the feature is on) and the ``evt`` records.

.. test:: Thermostat runner on the host tier: start, regulation, lost reference, foreign change, link hold, SD records
   :id: T_FW_THERMOSTAT_E2E
   :links: R_FW_THERMOSTAT_RUN
"""

import json
import time

import pytest

from calictl import control

from .conftest import FakeAdvertiser
from .test_host_e2e import _pair
from .test_sensors_e2e import _first_seen

pytestmark = [pytest.mark.linux_only, pytest.mark.xdist_group("firmware-host-build")]

ARM_S = 3.5  # CODEC_ARM_DELAY_MS + margin
GOVEE_ADDR = "A4:C1:38:00:00:02"


def govee(centi):
    """A Govee H5074 advert: int16 temperature and humidity (centi, LE), battery 90."""
    return bytes.fromhex("88ec00") + (centi & 0xFFFF).to_bytes(2, "little") + bytes.fromhex("d8165a02")


def _heater_writes(hu):
    return [f for fn, f in hu.unit.unit.writes if fn == "airheater"]


def _frame(hu, what, value):
    return control.build(hu.unit.funcs, "airheater", what, value, {})


def _thermo(fw):
    fw.send("thermo")
    return fw.expect("THERMO")["thermostat"]


def _reading(fw, centi, timeout=90):
    """Kick sensor windows until the configured Govee reads ``centi``."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        fw.send("sensors")
        cfg = fw.expect("SENSORS")["configured"]
        if cfg and cfg[0]["value"] and round(cfg[0]["value"]["temp_c"] * 100) == centi:
            return cfg[0]["id"]
        time.sleep(2)
    raise AssertionError("no reading %d" % centi)


async def _set(fu, fn, vals):
    fu.unit.state[fn].update(vals)
    fu.unit.push(fn)


async def _hang_up(fu):
    if fu.conn:
        await fu.conn.disconnect(0x13)  # the unit's power-save hang-up (remote user terminated)


DWELL_MS = 3000  # the test's min_dwell_ms (the shipped default is 300 s)


def _setup(host_fw, hu, centi=1700):
    """Paired and read, the Govee configured as ``cabin`` with a reading, the thermostat set up
    (feature on, reference cabin, target 21.0 C, 1 s period, 3 s dwell), not yet AUTO."""
    fw = host_fw(hu)
    assert _pair(fw, hu)["state"] == "bonded"
    adv = FakeAdvertiser(hu, GOVEE_ADDR, govee(centi), "GVH5074_0002").start()
    fw.send("sensors on")
    fw.expect("LOG exp: sensors", lambda v: v == "on")
    seen = _first_seen(fw)
    assert seen and seen["type"] == "govee"
    fw.send("sensors add %d cabin -" % seen["id"])
    fw.expect("LOG sensors", lambda v: v == "ok")
    _reading(fw, centi)
    time.sleep(ARM_S)
    for line in ("thermo ref cabin", "thermo target 2100", "thermo set period_ms 1000",
                 "thermo set min_dwell_ms %d" % DWELL_MS, "thermo on"):  # fmt: skip
        fw.send(line)
    return fw, adv, seen["id"]


def _auto(fw, hu):
    """AUTO from a heater that is off: the START batch lands and AUTO holds."""
    fw.send("thermo auto")
    fw.expect("LOG", lambda s: s.startswith("thermostat: START"), timeout=15)
    end = time.monotonic() + 15
    while hu.unit.unit.state["airheater"].get("NormalOperation") != 1:
        assert time.monotonic() < end, fw.log[-30:]
        time.sleep(0.2)
    time.sleep(2)  # the readback confirms the start
    assert _thermo(fw)["mode"] == "AUTO"


def test_auto_regulates_cold_cabin(host_fw, hci_unit):
    """17.0 C, target 21.0 C: runtime, level, power on as calictl builds them; 16.0 C later raises the level."""
    fw, adv, _ = _setup(host_fw, hci_unit)
    assert _heater_writes(hci_unit) == []
    _auto(fw, hci_unit)
    w = _heater_writes(hci_unit)
    assert w[:3] == [
        _frame(hci_unit, "runtime", 120),
        _frame(hci_unit, "level", 6),
        _frame(hci_unit, "power", "on"),
    ]
    assert len(w) == 3
    adv.set_mfg(govee(1600))
    # no window for 21 s: 1 C then stays inside the 3 C per minute plausibility rule (R_FW_THERMOSTAT_RUN)
    time.sleep(21)
    _reading(fw, 1600)
    end = time.monotonic() + 20  # fw.log: the sensors polls above may have consumed the line from the queue
    while not any(s.startswith("LOG thermostat: LEVEL_UP") for s in fw.log):
        assert time.monotonic() < end, fw.log[-30:]
        time.sleep(0.2)
    time.sleep(1)
    lv = hci_unit.unit.unit.state["airheater"]["HeatingLevel"]
    assert lv > 6 and _heater_writes(hci_unit)[-1] == _frame(hci_unit, "level", lv)
    assert _thermo(fw)["mode"] == "AUTO"


def test_reference_removed_while_auto(host_fw, hci_unit):
    """The reference removed in the sensor list: REFERENCE_LOST, level 1, mode DEGRADED — no other sensor."""
    fw, _, sid = _setup(host_fw, hci_unit)
    _auto(fw, hci_unit)
    fw.send("sensors remove %d" % sid)
    fw.expect("LOG sensors", lambda v: v == "ok")
    fw.expect("LOG", lambda s: s.startswith("thermostat: REFERENCE_LOST"), timeout=10)
    time.sleep(1.5)
    st = _thermo(fw)
    assert (st["mode"], st["reason"], st["t_ref"]) == ("DEGRADED", "REFERENCE_LOST", None)
    assert _heater_writes(hci_unit)[-1] == _frame(hci_unit, "level", 1)
    assert hci_unit.unit.unit.state["airheater"]["HeatingLevel"] == 1


def test_foreign_level_change_ends_auto(host_fw, hci_unit):
    """The level moved on the unit (panel, app): FOREIGN_CHANGE, AUTO ends, the unit's level stays."""
    fw, _, _ = _setup(host_fw, hci_unit)
    _auto(fw, hci_unit)
    n = len(_heater_writes(hci_unit))
    hci_unit.call(_set, hci_unit.unit, "airheater", {"HeatingLevel": 9})
    fw.expect("LOG", lambda s: s.startswith("thermostat: FOREIGN_CHANGE"), timeout=10)
    time.sleep(3)
    assert len(_heater_writes(hci_unit)) == n
    assert _thermo(fw)["mode"] == "OFF"
    assert hci_unit.unit.unit.state["airheater"]["HeatingLevel"] == 9


def test_link_held_while_auto(host_fw, rec_unit):
    """AUTO with camping off: the unit's power-save hang-up reconnects (no rest), and 1702 is re-read
    on the held link (``thermo heaterms 2000`` scales CALI_SESSION_HEATER_REREAD_MS)."""
    hu, rec = rec_unit
    fw, _, _ = _setup(host_fw, hu)
    fw.send("thermo heaterms 2000")
    _auto(fw, hu)
    hu.call(_set, hu.unit, "campingmode", {"State": 0})
    time.sleep(1)
    mark = len(fw.log)
    hu.call(_hang_up, hu.unit)
    fw.expect("LOG", lambda s: s.startswith("session: reconnect in"), timeout=10)
    fw.expect("SNAP", timeout=40)  # back up and read
    t0 = time.time()
    time.sleep(7)
    assert not any("power-save rest on" in s for s in fw.log[mark:])
    reads = [
        r
        for r in map(json.loads, rec.read_text().splitlines())
        if r.get("ev") == "read" and r.get("char") == "1702" and r["t"] >= t0
    ]
    assert len(reads) >= 2, fw.log[-30:]
    assert _thermo(fw)["mode"] == "AUTO"


def test_sd_log_has_thermo_records(host_fw, hci_unit):
    """Disabled and idle: an AUTO request is refused and nothing is logged. Enabled: one ``thermo``
    record per step (every field, within the SD record size), the refusal's reason in it."""
    fw = host_fw(hci_unit)
    assert _pair(fw, hci_unit)["state"] == "bonded"
    fw.expect("SNAP", timeout=40)
    time.sleep(ARM_S)
    fw.send("thermo set period_ms 1000")
    fw.send("thermo auto")
    fw.expect("LOG", lambda s: s.startswith("thermostat: AUTO_REFUSED_DISABLED"), timeout=10)
    time.sleep(2.5)
    assert not any('"ev":"thermo"' in s or '"kind":"thermo"' in s for s in fw.log)
    assert _heater_writes(hci_unit) == []
    fw.send("thermo on")
    fw.send("thermo auto")  # no reference configured
    fw.expect("LOG", lambda s: s.startswith("thermostat: AUTO_REFUSED_NO_REFERENCE"), timeout=10)
    time.sleep(2.5)
    recs = [json.loads("{" + s[4:] + "}") for s in fw.log if s.startswith('REC "ev":"thermo"')]
    assert len(recs) >= 2
    assert {"r", "m", "h", "t", "o", "g", "u", "i", "uo", "l", "lc", "rem"} <= set(recs[0])
    assert any(r["r"] == "AUTO_REFUSED_NO_REFERENCE" for r in recs)
    assert all(r["m"] == "OFF" and r["h"] == "OFF" for r in recs)
    assert max(len(s) - 4 for s in fw.log if s.startswith('REC "ev":"thermo"')) <= 288
    assert _heater_writes(hci_unit) == []


def test_closed_loop_reaches_target(host_fw, rec_unit):
    """AUTO against the mock's cabin thermal model (9 C outside, time constant 240 s, 3 C per level): the
    cabin starts at 19.0 C, the Govee advert follows the model, and the regulation settles within 1 C of
    the 21.0 C target. The burner starts once (no second ``power`` frame) and the level changes at least
    ``min_dwell_ms`` apart. This tests the control logic in a loop; it does not tune it. The real clock
    runs the model, and the runner rejects a reading that moves faster than 3 C per minute: the cabin is
    slow (time constant 240 s; from 19.0 C it moves 0.5 C per minute at level 4, 1.25 at level 5, 2.0 at level 6)
    so the loop, which stays at level 4, remains inside that rule.

    .. test:: Thermostat closed loop on the host tier: the cabin settles, the dwell holds, the burner starts once
       :id: T_THERMOSTAT_CLOSED_LOOP
       :links: R_FW_THERMOSTAT_RUN
    """
    hu, rec = rec_unit
    cabin = hu.unit.unit.cabin
    cabin.update(t_in=1900, t_out=900, tau_s=240, gain_per_level=300)
    fw, adv, _ = _setup(host_fw, hu, 1900)
    # The fast test cabin (tau 240 s) needs a higher ki; the shipped default ki 50 is not exercised closed-loop
    # here, and tuning is not covered.
    fw.send("thermo set ki 300")
    _auto(fw, hu)
    hu.loop.call_soon_threadsafe(
        lambda: hu.loop.create_task(hu.unit.clock())
    )  # the model runs: 1 tick per second
    end, settled_at, trace, advertised = time.monotonic() + 540, None, [], 1900
    while time.monotonic() < end:
        want = round(cabin["t_in"])
        if want != advertised:
            adv.set_mfg(govee(want))
            advertised = want
        th = _thermo(fw)
        trace.append((want, th["mode"], th["reason"], th["level"], th["t_ref"]))
        if abs(cabin["t_in"] - 2100) <= 100:
            settled_at = settled_at or time.monotonic()
            if time.monotonic() - settled_at > 90:  # and stayed there
                break
        else:
            settled_at = None
        time.sleep(3)
    assert settled_at and abs(cabin["t_in"] - 2100) <= 100, (cabin, trace[-12:])
    assert _thermo(fw)["mode"] == "AUTO"
    assert _heater_writes(hu).count(_frame(hu, "power", "on")) == 1  # the START only
    levels = {_frame(hu, "level", n): n for n in range(1, 11)}
    rows = [json.loads(s) for s in rec.read_text().splitlines()]
    at = [r["t"] for r in rows if r.get("ev") == "write" and r.get("hex") in {f.hex() for f in levels}]
    assert len(at) >= 2, rows[-10:]
    # the dwell, less 0.5 s for the scheduling jitter of the host tier (owner tick, mock writes, recorder)
    assert all(b - a >= DWELL_MS / 1000 - 0.5 for a, b in zip(at, at[1:])), at
