"""Tests for the cabin thermostat (heater state, control law, session machine)."""

import json

import pytest

from calictl import thermostat as th

CFG = th.THERMO_DEFAULTS


def _hs(d, age=0):
    return th.heater_state(d, age, CFG)


def test_heater_state_table():
    """The heater-state table maps a raw 1702 dict to one state, level and error code.

    .. test:: Heater state from the raw 1702 dict
       :id: T_THERMOSTAT_STATE
       :links: R_THERMOSTAT_STATE
    """
    ok = {"Installed": 1}
    assert _hs({})[0] == "UNKNOWN"
    assert _hs(None)[0] == "UNKNOWN"
    assert _hs(ok, CFG["heater_stale_ms"] + 1)[0] == "UNKNOWN"
    assert _hs(ok, None)[0] == "UNKNOWN"
    assert _hs(ok, CFG["heater_stale_ms"])[0] == "OFF"
    assert _hs({**ok, "ErrorCode": 4}) == ("LOCKED", None, 4)
    assert _hs({**ok, "ErrorCode": 5}) == ("BLOCKED", None, 5)
    for code in (1, 2, 3, 6, 7, 8, 15):
        assert _hs({**ok, "ErrorCode": code}) == ("FAULT", None, code)
    assert _hs({**ok, "FaultTriggerBit": 1})[0] == "FAULT"
    assert _hs({"Installed": 0}) == ("FAULT", None, -1)
    assert _hs({**ok, "PermanentOperation": 1, "NormalOperation": 1})[0] == "CONTINUOUS"
    assert _hs({**ok, "NormalOperation": 1})[0] == "RUNNING"
    assert _hs({**ok, "OperationModeAirHeater": 3})[0] == "TIMER_ARMED"
    assert _hs({**ok, "OperationModeAirHeater": 0}) == ("OFF", None, 0)
    # precedence: a fault beats a running bit; running beats the armed timer
    assert _hs({**ok, "ErrorCode": 1, "NormalOperation": 1})[0] == "FAULT"
    assert _hs({**ok, "NormalOperation": 1, "OperationModeAirHeater": 3})[0] == "RUNNING"


def test_out_of_range_level_is_unknown():
    """HeatingLevel outside 1..10 (idle seed 0, sentinels 11..15) is no level."""
    for raw in (0, 11, 15, None):
        assert _hs({"Installed": 1, "NormalOperation": 1, "HeatingLevel": raw})[1] is None
    for lvl in range(1, 11):
        assert _hs({"Installed": 1, "NormalOperation": 1, "HeatingLevel": lvl})[1] == lvl


def test_rdiv_rounds_half_away():
    assert th._rdiv(5, 2) == 3
    assert th._rdiv(-5, 2) == -3
    assert th._rdiv(4, 2) == 2
    assert th._rdiv(-4, 2) == -2
    assert th._rdiv(5, -2) == -3
    assert th._rdiv(1, 3) == 0


def test_settings_and_reasons():
    assert th.THERMO_DEFAULTS == {
        "period_ms": 60000, "kp": 1500, "ki": 50, "ka": 100, "u_out_max": 3000, "hyst": 500,
        "min_dwell_ms": 300000, "band_hi": 200, "hot_hold_ms": 900000, "overheat": 400,
        "min_level": 1, "stale_after_ms": 600000, "heater_stale_ms": 180000, "batt_min_dv": 119,
        "batt_hold_ms": 300000, "verify_ms": 10000, "runtime_min": 120,
    }  # fmt: skip
    assert (th.TARGET_MIN, th.TARGET_MAX, th.TARGET_STEP) == (500, 3000, 50)
    assert len(set(th.REASONS)) == len(th.REASONS)
    for r in (
        "AUTO_ON",
        "AUTO_REFUSED_DISABLED",
        "AUTO_REFUSED_STALE_HEATER",
        "FAULT_1",
        "FAULT_15",
        "STOP_DROPPED",
    ):
        assert r in th.REASONS
    assert "FAULT_0" not in th.REASONS and "FAULT_16" not in th.REASONS
    with pytest.raises(AttributeError):
        th.HEATER_STATES.append("X")


def _ctl(**kw):
    return {"i": 0, "last_change_ms": -(10**9), "hot_since_ms": None, "u": 0, "u_out": 0, **kw}


def test_cold_cabin_raises_level():
    """A cold cabin raises the heating level.

    .. test:: Thermostat control law
       :id: T_THERMOSTAT_CONTROL
       :links: R_THERMOSTAT_CONTROL
    """
    lvl, ctl, why = th.control(1700, None, 2100, 60000, _ctl(), CFG, 3, 0)
    assert lvl > 3 and why == "LEVEL_UP"
    assert ctl["u"] > 3000 and ctl["last_change_ms"] == 0


def test_hysteresis_and_dwell():
    lvl, ctl, why = th.control(1700, None, 2100, 60000, _ctl(), CFG, 3, 1_000_000)
    assert (lvl, why) == (6, "LEVEL_UP") and ctl["last_change_ms"] == 1_000_000
    lvl, _, why = th.control(1700, None, 2100, 60000, ctl, CFG, 3, 1_060_000)
    assert (lvl, why) == (None, "HOLD")  # still wants more, but within min_dwell_ms
    # inside the hysteresis band (u 2500 vs level 2000): no change even after the dwell
    lvl, _, why = th.control(1900, None, 2100, 0, _ctl(i=-500), CFG, 2, 2_000_000)
    assert (lvl, why) == (None, "HOLD")


def test_outside_off_contributes_zero():
    _, ctl, _ = th.control(2000, -1000, 2100, 60000, _ctl(), CFG, 3, 0)
    assert ctl["u_out"] == 3000
    _, ctl, _ = th.control(2000, None, 2100, 60000, ctl, CFG, 3, 60000)
    assert ctl["u_out"] == 0


def test_outside_cold_adds_up_to_cap():
    _, ctl, _ = th.control(2100, -1000, 2100, 0, _ctl(), CFG, 3, 0)
    assert ctl["u_out"] == 3000
    _, ctl, _ = th.control(2100, 1600, 2100, 0, _ctl(), CFG, 3, 0)
    assert ctl["u_out"] == 500


def test_overheat():
    lvl, _, why = th.control(2100 + 401, None, 2100, 60000, _ctl(), CFG, 5, 0)
    assert (lvl, why) == (None, "OVERHEAT")


def test_target_exceeded_at_min_level():
    ctl, now = _ctl(), 0
    for _ in range(15):
        lvl, ctl, why = th.control(2100 + 201, None, 2100, 60000, ctl, CFG, 1, now)
        assert why != "TARGET_EXCEEDED_AT_MIN_LEVEL"
        now += 60000
    lvl, ctl, why = th.control(2100 + 201, None, 2100, 60000, ctl, CFG, 1, now)
    assert (lvl, why) == (None, "TARGET_EXCEEDED_AT_MIN_LEVEL")
    # cooling below the band resets the timer
    _, ctl, _ = th.control(2100, None, 2100, 60000, ctl, CFG, 1, now + 60000)
    assert ctl["hot_since_ms"] is None


def test_target_change_keeps_integrator_bumpless():
    ctl = th.track(_ctl(), 4, 1900, 2100, CFG)
    _, c1, _ = th.control(1900, None, 2100, 0, ctl, CFG, 4, 0)
    assert c1["u"] == 4000
    _, c2, _ = th.control(1900, None, 2150, 0, c1, CFG, 4, 0)
    assert c2["u"] - c1["u"] == th._rdiv(CFG["kp"] * 50, 100)
    assert c2["i"] == c1["i"]


def test_step_gap_freezes_integrator():
    _, ctl, _ = th.control(1900, None, 2100, 4 * CFG["period_ms"], _ctl(i=123), CFG, 3, 0)
    assert ctl["i"] == 123
    _, ctl, _ = th.control(1900, None, 2100, CFG["period_ms"], _ctl(i=123), CFG, 3, 0)
    assert ctl["i"] == 123 + th._rdiv(CFG["ki"] * 200 * CFG["period_ms"], 100 * 60000)


# --- session machine (step) ---------------------------------------------------------------

OFF_H = {"Installed": 1}
RUN = {"Installed": 1, "NormalOperation": 1, "HeatingLevel": 5}
CONT = {"Installed": 1, "NormalOperation": 1, "PermanentOperation": 1, "HeatingLevel": 5}
P = CFG["period_ms"]
V = CFG["verify_ms"]


def _inp(**kw):
    base = {
        "now_ms": 0, "enabled": True, "request": None, "target": 2100, "runtime_min": 120,
        "ref": {"alias": "cabin", "value": 1700, "age_ms": 0}, "backup": None, "out": None,
        "outside_on": False, "heater": OFF_H, "heater_age_ms": 0, "link_ready": True,
        "camping_on": True, "shore_on": False, "ignition_on": False, "batt2_dv": 128, "cmd": None,
    }  # fmt: skip
    base.update(kw)
    return base


def _step(st, **kw):
    return th.step(_inp(**kw), CFG, st)


def _auto(heater=RUN, **kw):
    return _step(th.initial_state(), request="AUTO", heater=heater, **kw)


def test_auto_refused_without_camping_or_shore():
    out, st = _auto(OFF_H, camping_on=False, shore_on=False)
    assert (out["reason"], out["mode"], out["cmds"]) == ("AUTO_REFUSED_NO_CAMPING", "OFF", [])
    assert _auto(OFF_H, camping_on=False, shore_on=True)[0]["reason"] == "START"
    assert _auto(OFF_H, enabled=False)[0]["reason"] == "AUTO_REFUSED_DISABLED"
    assert _auto(OFF_H, link_ready=False)[0]["reason"] == "AUTO_REFUSED_NO_LINK"
    assert _auto(None)[0]["reason"] == "AUTO_REFUSED_STALE_HEATER"
    assert _auto({"Installed": 0})[0]["reason"] == "AUTO_REFUSED_NOT_INSTALLED"
    assert _auto({"Installed": 1, "ErrorCode": 3})[0]["reason"] == "AUTO_REFUSED_FAULT"
    assert _auto({"Installed": 1, "ErrorCode": 4})[0]["reason"] == "AUTO_REFUSED_FAULT"
    assert _auto(OFF_H, ignition_on=True)[0]["reason"] == "AUTO_REFUSED_IGNITION"
    assert _auto(OFF_H, ignition_on=None)[0]["reason"] == "AUTO_REFUSED_IGNITION"
    stale = {"alias": "cabin", "value": 1700, "age_ms": CFG["stale_after_ms"] + 1}
    assert _auto(OFF_H, ref=stale)[0]["reason"] == "AUTO_REFUSED_NO_REFERENCE"


def test_auto_refused_while_timer_armed():
    out, _ = _auto({"Installed": 1, "OperationModeAirHeater": 3})
    assert (out["reason"], out["mode"], out["cmds"]) == ("AUTO_REFUSED_TIMER_ARMED", "OFF", [])


def test_auto_from_off_sends_runtime_level_power_in_order():
    """AUTO from OFF sends run time, level and one power on, in that order.

    .. test:: Thermostat session state machine
       :id: T_THERMOSTAT_SESSION
       :links: R_THERMOSTAT_SESSION
    """
    out, st = _auto(OFF_H, runtime_min=45)
    assert out["reason"] == "START" and out["mode"] == "AUTO"
    (w1, v1), (w2, lvl), (w3, v3) = out["cmds"]
    assert (w1, v1, w2, w3, v3) == ("runtime", 45, "level", "power", "on")
    assert 1 <= lvl <= 10
    assert st["pending"]["what"] == "power" and st["pending"]["value"] == "on"
    json.dumps(st)  # plain data only
    # readback shows the run: the pending start clears, nothing is re-sent
    out, st = _step(
        st, now_ms=2000, heater={**RUN, "HeatingLevel": lvl}, cmd={"what": "power", "result": "OK"}
    )
    assert out["cmds"] == [] and st["pending"] is None and out["mode"] == "AUTO"


def test_adopt_running_sends_nothing():
    out, st = _auto(RUN)
    assert (out["reason"], out["cmds"], out["mode"]) == ("ADOPT_RUN", [], "AUTO")
    assert out["status"]["end"] == "run"
    out, st = _step(st, now_ms=P, heater=RUN)
    assert out["cmds"] == [] and out["reason"] == "HOLD"


def test_adopt_continuous_never_sends_power():
    out, st = _auto(CONT, ref={"alias": "cabin", "value": 1000, "age_ms": 0})
    assert (out["reason"], out["cmds"]) == ("ADOPT_CONTINUOUS", [])
    assert out["status"]["end"] == "none"
    heater, sent = dict(CONT), []
    for k in range(1, 40):
        out, st = _step(
            st, now_ms=k * P, heater=heater, ref={"alias": "cabin", "value": 1000 + 30 * k, "age_ms": 0}
        )
        sent += out["cmds"]
        for what, val in out["cmds"]:
            if what == "level":
                heater = {**heater, "HeatingLevel": val}
    assert sent and all(w == "level" for w, _ in sent)
    assert out["mode"] == "AUTO"


def test_web_level_change_ends_auto():
    _, st = _auto(RUN)
    out, st = _step(st, now_ms=1000, heater={**RUN, "HeatingLevel": 7})
    assert (out["mode"], out["reason"], out["cmds"]) == ("OFF", "FOREIGN_CHANGE", [])
    # the idle seed (level 0) is no level: not a foreign change
    _, st = _auto(RUN)
    out, _ = _step(st, now_ms=1000, heater={**RUN, "HeatingLevel": 0})
    assert out["mode"] == "AUTO"
    # the slider moved during the verify window of our own level command
    _, st = _auto(RUN)
    cold = {"alias": "cabin", "value": 1000, "age_ms": 0}
    t = CFG["min_dwell_ms"]
    out, st = _step(st, now_ms=t, heater=RUN, ref=cold)
    assert out["cmds"] == [("level", 10)]
    out, _ = _step(st, now_ms=t + 1000, heater={**RUN, "HeatingLevel": 7}, ref=cold)
    assert (out["mode"], out["reason"], out["cmds"]) == ("OFF", "FOREIGN_CHANGE", [])
    # ... or switched continuous heating on meanwhile
    out, _ = _step(st, now_ms=t + 1000, heater={**CONT, "HeatingLevel": 5}, ref=cold)
    assert (out["mode"], out["reason"], out["cmds"]) == ("OFF", "FOREIGN_CHANGE", [])


def test_power_on_retried_once_only_when_readback_off():
    _, st = _auto(OFF_H)
    # no fresh readback: never retried
    out, st = _step(st, now_ms=V, heater=None, cmd={"what": "power", "result": "FAILED"})
    assert out["cmds"] == [] and out["reason"] == "HEATER_UNKNOWN"
    # fresh readback shows the heater still off: one retry of power on alone
    out, st = _step(st, now_ms=V + 1000, heater=OFF_H)
    assert out["cmds"] == [("power", "on")]
    out, st = _step(st, now_ms=2 * V + 2000, heater=OFF_H)
    assert (out["cmds"], out["mode"], out["reason"]) == ([], "OFF", "START_NOT_CONFIRMED")
    # a refused start ends AUTO at once
    _, st = _auto(OFF_H)
    out, _ = _step(st, now_ms=500, cmd={"what": "power", "result": "REFUSED"})
    assert (out["cmds"], out["mode"], out["reason"]) == ([], "OFF", "START_NOT_CONFIRMED")


def test_unconfirmed_never_resent_blind():
    out, st = _auto(OFF_H)
    started = {**RUN, "HeatingLevel": out["cmds"][1][1]}
    out, st = _step(st, now_ms=100, heater=None, cmd={"what": "power", "result": "UNCONFIRMED"})
    assert out["cmds"] == []
    for t in (V, 10 * V, 100 * V):
        out, st = _step(st, now_ms=t, heater=None)
        assert out["cmds"] == [] and out["mode"] == "AUTO"
    # the readback after the reconnect shows the run: confirmed, nothing sent
    out, st = _step(st, now_ms=100 * V + 1, heater=started)
    assert out["cmds"] == [] and st["pending"] is None and out["mode"] == "AUTO"


def test_not_applied_after_one_retry():
    _, st = _auto(RUN)
    cold = {"alias": "cabin", "value": 1000, "age_ms": 0}
    t = CFG["min_dwell_ms"]
    out, st = _step(st, now_ms=t, heater=RUN, ref=cold)
    assert out["cmds"] == [("level", 10)] and out["reason"] == "LEVEL_UP"
    out, st = _step(st, now_ms=t + 1, heater=RUN, ref=cold, cmd={"what": "level", "result": "OK"})
    assert out["cmds"] == []
    out, st = _step(st, now_ms=t + V, heater=RUN, ref=cold)
    assert out["cmds"] == [("level", 10)]
    out, st = _step(st, now_ms=t + 2 * V, heater=RUN, ref=cold)
    assert (out["cmds"], out["mode"], out["reason"]) == ([], "OFF", "NOT_APPLIED")


def test_user_off_while_link_down_goes_stopping_then_drops_on_change():
    _, st = _auto(RUN)
    out, st1 = _step(st, now_ms=1000, request="OFF")
    assert (out["cmds"], out["mode"], out["reason"]) == ([("power", "off")], "OFF", "USER_OFF")
    out, _ = _step(_auto(CONT)[1], now_ms=1000, request="OFF", heater=CONT)
    assert out["cmds"] == [("permanent", "off")]
    # link down: STOPPING, then the stop once the heater still shows what the ESP left
    out, st2 = _step(st, now_ms=1000, request="OFF", link_ready=False)
    assert (out["cmds"], out["mode"]) == ([], "STOPPING") and st2["stop_snapshot"]
    out, _ = _step(st2, now_ms=2000, link_ready=False)
    assert (out["cmds"], out["mode"], out["reason"]) == ([], "STOPPING", "STOPPING")
    out, _ = _step(st2, now_ms=3000, heater=RUN)
    assert (out["cmds"], out["mode"]) == ([("power", "off")], "OFF")
    # something changed meanwhile (panel, app): the stop is dropped
    out, st3 = _step(st2, now_ms=3000, heater={**RUN, "HeatingLevel": 8})
    assert (out["cmds"], out["mode"], out["reason"]) == ([], "OFF", "STOP_DROPPED")
    # the run ended by itself meanwhile: nothing to stop
    out, _ = _step(st2, now_ms=3000, heater=OFF_H)
    assert (out["cmds"], out["mode"], out["reason"]) == ([], "OFF", "RUN_ENDED")


def test_reference_lost_uses_backup_then_min_level():
    good_b = {"alias": "bed", "value": 1800, "age_ms": 0}
    _, st = _auto(RUN, backup=good_b)
    lost = {"alias": "cabin", "value": 1700, "age_ms": CFG["stale_after_ms"] + 1}
    out, st = _step(st, now_ms=P, heater=RUN, ref=lost, backup=good_b)
    assert (out["reason"], out["cmds"], out["mode"]) == ("BACKUP_SELECTED", [], "AUTO")
    assert out["status"]["active_alias"] == "bed" and st["active"] == "backup"
    # the reference comes back: no automatic switch back
    out, st = _step(st, now_ms=2 * P, heater=RUN, backup=good_b)
    assert st["active"] == "backup"
    out, st = _step(st, now_ms=3 * P, heater=RUN, ref=lost, backup=None)
    assert (out["reason"], out["cmds"], out["mode"]) == ("REFERENCE_LOST", [("level", 1)], "DEGRADED")
    # the owner switches back with an AUTO request
    _, st = _auto(RUN, backup=good_b)
    _, st = _step(st, now_ms=P, heater=RUN, ref=lost, backup=good_b)
    _, st = _step(st, now_ms=2 * P, heater=RUN, request="AUTO", backup=good_b)
    assert st["active"] == "ref"


@pytest.mark.parametrize("code", range(1, 16))
def test_fault_codes_end_auto(code):
    _, st = _auto(RUN)
    bad = {**RUN, "ErrorCode": code}
    out, st = _step(st, now_ms=1000, heater=bad)
    if code == 1:  # low battery: a normal stop (spec §4.4, §9.3)
        assert (out["mode"], out["reason"], out["cmds"]) == ("OFF", "BATTERY_LOW", [("power", "off")])
        return
    want = "LOCKED" if code == 4 else "FAULT_%d" % code
    assert (out["mode"], out["reason"], out["cmds"]) == ("OFF", want, [])
    out, st = _step(st, now_ms=P, heater=bad)
    assert (out["mode"], out["cmds"]) == ("OFF", [])


def test_fault_bit_without_code_ends_auto_unknown():
    _, st = _auto(RUN)
    out, _ = _step(st, now_ms=1000, heater={**RUN, "FaultTriggerBit": 1})
    assert (out["mode"], out["reason"], out["cmds"]) == ("OFF", "HEATER_UNKNOWN", [])


def test_battery_low_after_hold():
    _, st = _auto(RUN)
    out, st = _step(st, now_ms=P, heater=RUN, batt2_dv=118)
    assert out["cmds"] == []
    out, st = _step(st, now_ms=P + CFG["batt_hold_ms"] - 1, heater=RUN, batt2_dv=118)
    assert out["cmds"] == [] and out["mode"] == "AUTO"
    out, st = _step(st, now_ms=P + CFG["batt_hold_ms"], heater=RUN, batt2_dv=118)
    assert (out["cmds"], out["mode"], out["reason"]) == ([("power", "off")], "OFF", "BATTERY_LOW")
    # a recovery inside the hold resets it (the glow dip)
    _, st = _auto(RUN)
    _, st = _step(st, now_ms=P, heater=RUN, batt2_dv=118)
    _, st = _step(st, now_ms=2 * P, heater=RUN, batt2_dv=125)
    out, _ = _step(st, now_ms=P + CFG["batt_hold_ms"], heater=RUN, batt2_dv=118)
    assert out["mode"] == "AUTO"
    # ErrorCode 1 stops at once; continuous heating with permanent off
    _, st = _auto(CONT)
    out, _ = _step(st, now_ms=1000, heater={**CONT, "ErrorCode": 1})
    assert (out["cmds"], out["mode"], out["reason"]) == ([("permanent", "off")], "OFF", "BATTERY_LOW")


def test_start_retry_not_after_long_link_gap():
    _, st = _auto(OFF_H)
    out, st = _step(
        st, now_ms=100, heater=None, link_ready=False, cmd={"what": "power", "result": "UNCONFIRMED"}
    )
    for t in (V, 3600_000, 3 * 3600_000):
        out, st = _step(st, now_ms=t, heater=None, link_ready=False)
        assert out["cmds"] == []
    out, _ = _step(st, now_ms=3 * 3600_000 + 1, heater=OFF_H)
    assert (out["cmds"], out["mode"], out["reason"]) == ([], "OFF", "START_NOT_CONFIRMED")
    # ignition went on in the verify window: no retry either
    _, st = _auto(OFF_H)
    out, _ = _step(
        st, now_ms=V + 1000, heater=OFF_H, ignition_on=True, cmd={"what": "power", "result": "FAILED"}
    )
    assert (out["cmds"], out["mode"], out["reason"]) == ([], "OFF", "START_NOT_CONFIRMED")


def test_refused_stop_reports_not_applied():
    _, st = _auto(RUN)
    _, st = _step(st, now_ms=1000, request="OFF")
    out, _ = _step(st, now_ms=2000, heater=RUN, cmd={"what": "power", "result": "REFUSED"})
    assert (out["cmds"], out["mode"], out["reason"]) == ([], "OFF", "NOT_APPLIED")
    assert out["status"]["heater"] == "RUNNING"
    # acknowledged, but the readback still runs: one retry, then NOT_APPLIED
    out, st = _step(st, now_ms=1000 + V, heater=RUN, cmd={"what": "power", "result": "OK"})
    assert out["cmds"] == [("power", "off")]
    out, _ = _step(st, now_ms=1000 + 2 * V, heater=RUN)
    assert (out["cmds"], out["mode"], out["reason"]) == ([], "OFF", "NOT_APPLIED")
    assert out["status"]["heater"] == "RUNNING"


def test_run_ended_by_unit():
    _, st = _auto(RUN)
    out, _ = _step(st, now_ms=P, heater=OFF_H)
    assert (out["mode"], out["reason"], out["cmds"]) == ("OFF", "RUN_ENDED", [])


def test_continuous_ended_by_engine():
    _, st = _auto(CONT)
    out, _ = _step(st, now_ms=P, heater=OFF_H)
    assert (out["mode"], out["reason"], out["cmds"]) == ("OFF", "CONTINUOUS_ENDED", [])
