"""Tests for the cabin thermostat's heater-state model."""

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
