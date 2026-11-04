#!/usr/bin/env python3
"""Generate ``tests/vectors/thermostat.json`` — the cabin thermostat's decisions as golden vectors.

Every case is one call of :func:`calictl.thermostat.step` (the authority):
``[group, cfg_index, in, st, out, st2]`` with each part flattened to a list (``None`` = null, booleans
0/1; ``st`` null = the previous case's ``st2``, which keeps the file under the 1 MB commit limit). The C twin
(``firmware/components/cali_core/thermostat.c``) must reproduce every case
(``tests/firmware/test_thermostat_parity.py``). Groups: ``grid`` (each base state x one changed input),
``sweep`` (seeded random walks from :func:`~calictl.thermostat.initial_state`, seed 4711) and
``session:<name>`` (scripted multi-step sessions). ``heater`` holds direct
:func:`~calictl.thermostat.heater_state` cases. Checked in; never hand-edit:

    python3 -m tools.gen_thermostat_vectors            # rewrite the file
    python3 -m tools.gen_thermostat_vectors --check    # exit 1 if the checked-in file is stale
"""

import copy
import itertools
import json
import random
import sys
from pathlib import Path

from calictl import thermostat as th

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "tests" / "vectors" / "thermostat.json"

D = dict(th.THERMO_DEFAULTS)
# A second table with remainders everywhere: odd gains reach the half-away-from-zero rounding of
# negative values, a wide overheat band lets the demand go negative before OVERHEAT, and a
# min_level above 1 moves the low anti-windup limit.
ODD = {
    **D,
    "period_ms": 30000,
    "kp": 1550,
    "ki": 37,
    "ka": 133,
    "u_out_max": 2500,
    "hyst": 250,
    "min_dwell_ms": 120000,
    "band_hi": 150,
    "hot_hold_ms": 300000,
    "overheat": 2000,
    "min_level": 3,
    "verify_ms": 7000,
    "runtime_min": 30,
}
CFGS = [D, ODD]

HKEYS = (
    "Installed",
    "HeatingLevel",
    "ErrorCode",
    "FaultTriggerBit",
    "PermanentOperation",
    "NormalOperation",
    "OperationModeAirHeater",
)
OFF_H = {"Installed": 1, "HeatingLevel": 0}
NO_ID = {"Installed": 1}


def run_h(level, perm=0):
    return {"Installed": 1, "NormalOperation": 1, "PermanentOperation": perm, "HeatingLevel": level}


# ---- flattening (the C driver reads and prints these lists in this order) ----


def _ints(xs):
    """Booleans as 0/1 (smaller file; the C driver reads ints)."""
    return [int(x) if isinstance(x, bool) else x for x in xs]


def _sensor(r):
    return [0, None, None, None] if not r else [1, r["alias"], r["value"], r["age_ms"]]


def _heater(d):
    return [0] + [None] * len(HKEYS) if not d else [1] + [d.get(k) for k in HKEYS]


def _onoff(v):
    return {"on": 1, "off": 0}.get(v, v)


def _snap(s):
    return [0, None, None, None] if s is None else [1, s["normal"], s["permanent"], s["level"]]


def flat_in(i):
    cmd = i["cmd"] or {}
    return [
        i["now_ms"], i["enabled"], i["request"], i["target"], i["runtime_min"],
        *_sensor(i["ref"]), *_sensor(i["backup"]), *_sensor(i["out"]), i["outside_on"],
        *_heater(i["heater"]), i["heater_age_ms"], i["link_ready"], i["camping_on"], i["shore_on"],
        i["ignition_on"], i["batt2_dv"], cmd.get("what"), cmd.get("result"),
        i["night"], i["known"]["camping_on"], i["known"]["shore_on"], i["known"]["ignition_on"],
    ]  # fmt: skip


def flat_st(s):
    c, p = s["ctl"], s["pending"]
    pend = (
        [0] + [None] * 7
        if p is None
        else [
            1,
            p["what"],
            _onoff(p["value"]),
            p["since_ms"],
            p["tries"],
            p["first_ms"],
            p["before"],
            p["why"],
        ]
    )
    return [
        s["mode"], s["active"], c["i"], c["last_change_ms"], c["hot_since_ms"], c["u"], c["u_out"],
        *pend, *_snap(s["expect"]), s["batt_low_since_ms"], *_snap(s["stop_snapshot"]),
        (s["stop_snapshot"] or {}).get("why"), s["last_ctl_ms"],
        s["night"], s["wait_until_ms"], s["cont_since_ms"],
    ]  # fmt: skip


def flat_out(o):
    st = o["status"]
    cmds = [x for w, v in o["cmds"] for x in (w, _onoff(v))]
    return [
        o["reason"], o["mode"], st["heater"], st["t_ref"], st["t_out"], st["target"], st["level"],
        st["active_alias"], st["outside_active"], st["end"], st["blocked"], st["wait_left_s"], st["night"],
        len(o["cmds"]), *cmds,
    ]  # fmt: skip


# ---- inputs ----


def sensor(value, age=0, alias="cabin"):
    return {"alias": alias, "value": value, "age_ms": age}


#: What the last-known frames say (the live values of the van); ``base`` copies it.
KNOWN = {"camping_on": True, "shore_on": False, "ignition_on": False}


def base(**kw):
    i = {
        "now_ms": 0, "enabled": True, "request": None, "target": 2100, "runtime_min": 120,
        "ref": sensor(1700), "backup": None, "out": None, "outside_on": False, "heater": OFF_H,
        "heater_age_ms": 0, "link_ready": True, "camping_on": True, "shore_on": False,
        "ignition_on": False, "batt2_dv": 128, "cmd": None, "night": False,
        "known": dict(KNOWN),
    }  # fmt: skip
    i.update(kw)
    return i


class Rec:
    """Runs step() and records every call as ``[group, cfg_index, in, st, out, st2]``.

    ``st`` is null when it equals the previous case's ``st2``.
    """

    def __init__(self):
        self.cases = []
        self.tags = set()

    def _tag(self, k, inp, st, out, st2):
        """Coverage tags of the night, takeover, hold and blocked paths (read off the structured step)."""
        cfg, why, wait = CFGS[k], out["reason"], st["mode"] == "WAITING"
        sent = [w for w, _ in out["cmds"]]
        if why in ("ADOPT_RUN", "ADOPT_CONTINUOUS") and "level" in sent:
            self.tags.add("adopt_to_demand")
        if why == "WAITING_FOR_CONTINUOUS":
            if not wait and "level" in sent:
                self.tags.add("night_preset")
            if st2["cont_since_ms"] is not None:
                self.tags.add("night_unconfirmed")
            if not wait and inp["ref"]["value"] > inp["target"] + cfg["band_hi"]:
                self.tags.add("night_skips_warm")
        if wait and why == "ADOPT_CONTINUOUS":
            self.tags.add("night_wait_adopt")
            if inp["now_ms"] >= st["wait_until_ms"]:
                self.tags.add("night_confirm_past_wait")
        if why == "NIGHT_WAIT_ENDED":
            self.tags.add("night_wait_timeout")
        if wait and why == "USER_OFF":
            self.tags.add("night_user_off")
        if (
            wait
            and why in ("USER_OFF", "DISABLED", "STOPPING")
            and (out["cmds"] or st2["mode"] == "STOPPING")
        ):
            self.tags.add("night_user_off_stops_run")
        if why == "HOLD_AT_MIN_LEVEL":
            hot = inp["ref"]["value"] > inp["target"] + cfg["overheat"]
            self.tags.add("continuous_hold_overheat" if hot else "continuous_hold_min")
        left = out["status"]["wait_left_s"]
        if left is not None and (st2["wait_until_ms"] - inp["now_ms"]) % 1000:
            self.tags.add("wait_left_ceil")
        if out["status"]["blocked"]:
            self.tags.add("blocked_" + out["status"]["blocked"])
            if inp["known"] != {
                "camping_on": inp["camping_on"],
                "shore_on": inp["shore_on"],
                "ignition_on": inp["ignition_on"],
            }:
                self.tags.add("blocked_known_frames")

    def step(self, group, k, inp, st):
        out, st2 = th.step(inp, CFGS[k], st)
        self._tag(k, inp, st, out, st2)
        before = _ints(flat_st(st))
        if self.cases and self.cases[-1][5] == before:
            before = None
        self.cases.append([group, k, _ints(flat_in(inp)), before, _ints(flat_out(out)), _ints(flat_st(st2))])
        return out, st2


def follow(st, level_default=None):
    """The heater the unit would report after it applied what the thermostat expects."""
    e = st["expect"]
    if e is None or not (e["normal"] or e["permanent"]):
        return OFF_H
    return run_h(e["level"] if e["level"] is not None else level_default or 3, e["permanent"])


def session(rec, name, k, steps, st=None, t0=1_000_000):
    """Scripted session: each step is a dict of input overrides plus optional keys:

    ``dt`` (ms since the previous step, default the period), ``heater="follow"`` (the default: the
    unit applies what the thermostat expects), ``ack`` (result for the previous batch's last command,
    default ``OK``).
    """
    st = st or th.initial_state()
    now, last = t0, None
    for s in steps:
        s = dict(s)
        now += s.pop("dt", CFGS[k]["period_ms"])
        ack = s.pop("ack", "OK")
        cmd = None
        if last and last["cmds"]:
            cmd = {"what": last["cmds"][-1][0], "result": ack}
        h = s.pop("heater", "follow")
        if h == "follow":
            h = follow(st)
        if callable(h):
            h = h(st)
        last, st = rec.step("session:" + name, k, base(now_ms=now, heater=h, cmd=cmd, **s), st)
    return st


def cabin(rec, name, k, t, t_out, n, target=2100, **kw):
    """A cabin that warms with the heater level and cools to the outside (centi-°C per step)."""
    st, now, last = th.initial_state(), 1_000_000, None
    for j in range(n):
        now += CFGS[k]["period_ms"]
        cmd = {"what": last["cmds"][-1][0], "result": "OK"} if last and last["cmds"] else None
        req = "AUTO" if j == 0 else None
        out = sensor(t_out, alias="outside")
        inp = base(
            now_ms=now, request=req, ref=sensor(t), out=out, outside_on=True, heater=follow(st),
            cmd=cmd, target=target, **kw,
        )  # fmt: skip
        last, st = rec.step("session:" + name, k, inp, st)
        lvl = (st["expect"] or {}).get("level") or 0 if st["mode"] != "OFF" else 0
        t += lvl * 12 - th._rdiv(t - t_out, 25)
    return st


# ---- groups ----


def sessions(rec):
    P = D["period_ms"]
    S = D["min_dwell_ms"]
    auto = {"request": "AUTO"}
    cabin(rec, "cooling_cabin", 0, 1200, -500, 60)
    cabin(rec, "cooling_cabin_odd_cfg", 1, 1500, -800, 60)
    cabin(rec, "warm_cabin_windup_low", 0, 2300, 1800, 25, target=2100)
    cabin(rec, "freezing_cabin_windup_high", 0, 200, -2500, 40, target=3000)
    session(
        rec, "adopt_run_then_hold", 0,
        [{**auto, "heater": run_h(5)}] + [{"heater": run_h(5), "ref": sensor(2050)}] * 8,
    )  # fmt: skip
    session(
        rec, "adopt_continuous_then_end", 0,
        [{**auto, "heater": run_h(6, 1)}, {"heater": run_h(6, 1)}, {"heater": OFF_H}],
    )  # fmt: skip
    session(
        rec, "run_ended_by_unit", 0,
        [{**auto, "heater": run_h(4)}, {"heater": run_h(4)}, {"heater": OFF_H}, {}],
    )  # fmt: skip
    session(
        rec, "reference_lost_backup_then_owner_back", 0,
        [
            {**auto, "heater": run_h(4), "backup": sensor(1900, alias="bed")},
            {"ref": sensor(1700, age=D["stale_after_ms"] + 1), "backup": sensor(1900, alias="bed")},
            {"ref": None, "backup": sensor(1950, alias="bed")},
            {**auto, "ref": sensor(1800), "backup": sensor(1950, alias="bed")},
            {"ref": sensor(1800)},
        ],
    )  # fmt: skip
    session(
        rec, "backup_lost_back_to_reference", 0,
        [
            {**auto, "heater": run_h(4), "backup": sensor(1900, alias="bed")},
            {"ref": sensor(None), "backup": sensor(1900, alias="bed")},
            {"ref": sensor(1850), "backup": sensor(None, alias="bed")},
            {"ref": sensor(None), "backup": sensor(None, alias="bed")},
        ],
    )  # fmt: skip
    session(
        rec, "warm_cabin_refused_then_start_at_the_edge", 0,
        [
            {**auto, "ref": sensor(D["band_hi"] + 2101), "target": 2100},
            {**auto, "ref": sensor(D["band_hi"] + 2100), "target": 2100},
        ],
    )  # fmt: skip
    session(
        rec, "reference_lost_no_backup_degraded", 0,
        [
            {**auto, "heater": run_h(6)},
            {"ref": sensor(None)},
            {"ref": sensor(None)},
            {"ref": sensor(None), "link_ready": False},
            {"ref": sensor(1800)},
            {"ref": sensor(1800)},
        ],
    )  # fmt: skip
    session(
        rec, "foreign_level_change", 0,
        [{**auto, "heater": run_h(5)}, {"heater": run_h(5)}, {"heater": run_h(8)}, {"heater": run_h(8)}],
    )  # fmt: skip
    session(
        rec, "foreign_change_during_pending_level", 0,
        [
            {**auto, "ref": sensor(900)},
            {"ref": sensor(900), "dt": S},
            {"ref": sensor(900), "dt": S},
            {"ref": sensor(900), "dt": 2000, "heater": lambda st: run_h(9)},
        ],
    )  # fmt: skip
    session(
        rec, "user_off", 0,
        [{**auto}, {}, {"request": "OFF"}, {"heater": OFF_H}, {"heater": OFF_H}],
    )  # fmt: skip
    session(
        rec, "disabled_while_on", 0,
        [{**auto, "heater": run_h(3, 1)}, {"heater": run_h(3, 1), "enabled": False}, {"heater": OFF_H}],
    )  # fmt: skip
    session(
        rec, "user_off_link_down_then_stop", 0,
        [
            {**auto, "heater": run_h(5)},
            {"request": "OFF", "link_ready": False, "heater": run_h(5)},
            {"link_ready": False, "heater": run_h(5)},
            {"heater": run_h(5)},
            {"heater": OFF_H},
        ],
    )  # fmt: skip
    session(
        rec, "stopping_heater_changed_dropped", 0,
        [
            {**auto, "heater": run_h(5)},
            {"request": "OFF", "link_ready": False, "heater": run_h(5)},
            {"heater": run_h(9)},
        ],
    )  # fmt: skip
    session(
        rec, "stopping_heater_ended", 0,
        [
            {**auto, "heater": run_h(5, 1)},
            {"request": "OFF", "heater": None},
            {"heater": OFF_H},
        ],
    )  # fmt: skip
    session(
        rec, "stop_refused", 0,
        [{**auto, "heater": run_h(5)}, {"request": "OFF", "heater": run_h(5)}, {"heater": run_h(5), "ack": "REFUSED"}],
    )  # fmt: skip
    session(
        rec, "stop_not_applied_after_retry", 0,
        [
            {**auto, "heater": run_h(5)},
            {"request": "OFF", "heater": run_h(5)},
            {"heater": run_h(5), "dt": D["verify_ms"]},
            {"heater": run_h(5), "dt": D["verify_ms"]},
        ],
    )  # fmt: skip
    session(
        rec, "stop_dropped_heater_moved", 0,
        [
            {**auto, "heater": run_h(5)},
            {"request": "OFF", "heater": run_h(5)},
            {"heater": run_h(7), "dt": D["verify_ms"]},
        ],
    )  # fmt: skip
    session(
        rec, "battery_low_held", 0,
        [{**auto, "heater": run_h(4)}]
        + [{"heater": run_h(4), "batt2_dv": 118}] * 3
        + [{"heater": run_h(4), "batt2_dv": 125}]
        + [{"heater": run_h(4), "batt2_dv": 110}] * 7
        + [{"heater": OFF_H}],
    )  # fmt: skip
    for code in range(1, 16):
        session(
            rec, "fault_%d" % code, 0,
            [{**auto, "heater": run_h(4)}, {"heater": {**run_h(4), "ErrorCode": code}}, {"heater": OFF_H}],
        )  # fmt: skip
    session(
        rec, "fault_bit_without_code", 0,
        [{**auto, "heater": run_h(4)}, {"heater": {**run_h(4), "FaultTriggerBit": 1}}],
    )  # fmt: skip
    session(
        rec, "battery_fault_link_down", 0,
        [{**auto, "heater": run_h(4)}, {"heater": {**run_h(4), "ErrorCode": 1}, "link_ready": False}, {"heater": run_h(4)}],
    )  # fmt: skip
    session(
        rec, "heater_stale_then_back", 0,
        [
            {**auto, "heater": run_h(4)},
            {"heater": run_h(4), "heater_age_ms": D["heater_stale_ms"] + 1},
            {"heater": run_h(4), "heater_age_ms": None},
            {"heater": None},
            {"heater": run_h(4)},
        ],
    )  # fmt: skip
    session(
        rec, "overheat", 0,
        [{**auto, "heater": run_h(4)}, {"heater": run_h(4), "ref": sensor(2600)}, {"heater": OFF_H}],
    )  # fmt: skip
    session(
        rec, "overheat_at_entry", 0,
        [{**auto, "ref": sensor(2600)}, {"ref": sensor(2600)}, {"ref": sensor(2600), "heater": OFF_H}],
    )  # fmt: skip
    session(
        rec, "target_exceeded_at_min_level", 0,
        [{**auto, "heater": run_h(1), "ref": sensor(2350)}]
        + [{"heater": run_h(1), "ref": sensor(2350)}] * 17,
    )  # fmt: skip
    session(
        rec, "outside_feed_forward_then_inactive", 0,
        [{**auto, "heater": run_h(3), "outside_on": True, "out": sensor(-300, alias="out")}]
        + [{"heater": "follow", "outside_on": True, "out": sensor(-300, alias="out"), "ref": sensor(2000)}] * 3
        + [{"outside_on": True, "out": sensor(-300, age=D["stale_after_ms"] + 5, alias="out"), "ref": sensor(2000)}] * 2
        + [{"outside_on": True, "out": None, "ref": sensor(2000)}],
    )  # fmt: skip
    session(
        rec, "start_not_confirmed_after_retry", 0,
        [
            {**auto},
            {"heater": OFF_H, "dt": D["verify_ms"]},
            {"heater": OFF_H, "dt": D["verify_ms"]},
            {"heater": OFF_H},
        ],
    )  # fmt: skip
    session(
        rec, "start_busy_resend_then_ok", 0,
        [{**auto}, {"heater": OFF_H, "ack": "BUSY", "dt": 1000}, {"dt": 1000}, {}, {}],
    )  # fmt: skip
    session(
        rec, "start_power_not_ready_short_resend", 0,
        [{**auto}, {"heater": OFF_H, "ack": "NOT_READY", "dt": 1000}, {"heater": OFF_H, "ack": "NOT_READY", "dt": 1000}, {}],
    )  # fmt: skip
    session(
        rec, "start_busy_late_no_retry", 0,
        [{**auto}, {"heater": OFF_H, "ack": "BUSY", "dt": D["heater_stale_ms"] + 1}],
    )  # fmt: skip
    session(
        rec, "start_refused", 0,
        [{**auto}, {"heater": OFF_H, "ack": "REFUSED", "dt": 1000}, {"heater": OFF_H}],
    )  # fmt: skip
    session(
        rec, "start_retry_blocked_by_ignition", 0,
        [{**auto}, {"heater": OFF_H, "dt": D["verify_ms"], "ignition_on": True}],
    )  # fmt: skip
    session(
        rec, "level_not_applied", 0,
        [
            {**auto, "heater": run_h(3), "ref": sensor(800)},
            {"heater": run_h(3), "ref": sensor(800), "dt": S},
            {"heater": run_h(3), "ref": sensor(800), "dt": D["verify_ms"]},
            {"heater": run_h(3), "ref": sensor(800), "dt": D["verify_ms"]},
            {"heater": run_h(3), "ref": sensor(800)},
        ],
    )  # fmt: skip
    session(
        rec, "level_refused", 0,
        [
            {**auto, "heater": run_h(3), "ref": sensor(800)},
            {"heater": run_h(3), "ref": sensor(800), "dt": S},
            {"heater": run_h(3), "ref": sensor(800), "ack": "REFUSED", "dt": 1000},
        ],
    )  # fmt: skip
    session(
        rec, "long_gap_freezes_integrator", 0,
        [{**auto, "heater": run_h(4), "ref": sensor(1900)}, {"heater": run_h(4), "ref": sensor(1900), "dt": 4 * P}]
        + [{"ref": sensor(1900)}] * 3,
    )  # fmt: skip
    session(
        rec, "target_change_bumpless", 0,
        [{**auto, "heater": run_h(5), "ref": sensor(2000)}, {"ref": sensor(2000)}]
        + [{"ref": sensor(2000), "target": 2400}] * 4,
    )  # fmt: skip
    session(
        rec, "level_unknown_while_running", 0,
        [{**auto, "heater": run_h(0)}, {"heater": run_h(0)}, {"heater": run_h(11)}, {"heater": run_h(5)}, {"heater": run_h(5)}],
    )  # fmt: skip
    busy = {"heater": run_h(5), "ack": "BUSY", "dt": 1000}
    session(
        rec, "owner_stop_then_auto_drops_it", 0,
        [{**auto, "heater": run_h(5)}, {"request": "OFF", "heater": run_h(5)}, {**busy, **auto}, {"heater": run_h(5)}],
    )  # fmt: skip
    session(
        rec, "owner_stop_kept_by_refused_auto", 0,
        [{**auto, "heater": run_h(5)}, {"request": "OFF", "heater": run_h(5)}, {**busy, **auto, "enabled": False},
         {"heater": OFF_H}],
    )  # fmt: skip
    session(
        rec, "safety_stop_ignores_auto", 0,
        [{**auto, "heater": run_h(5)}, {"heater": run_h(5), "ref": sensor(2600)}, {**busy, **auto},
         {**auto, "heater": run_h(5), "dt": D["verify_ms"]}, {**auto, "heater": OFF_H}],
    )  # fmt: skip
    session(
        rec, "safety_stopping_ignores_auto", 0,
        [{**auto, "heater": run_h(5)}, {"heater": run_h(5), "ref": sensor(2600), "link_ready": False},
         {**auto, "heater": run_h(5)}, {"heater": OFF_H}],
    )  # fmt: skip
    session(  # odd cfg: u = -13500 reaches the negative half in the level (masked by min_level)
        rec, "negative_half_demand", 1,
        [{**auto, "heater": run_h(2), "ref": sensor(3100)}, {"heater": run_h(2), "ref": sensor(4100)}],
    )  # fmt: skip
    session(
        rec, "shore_only_entry", 1,
        [{**auto, "camping_on": False, "shore_on": True}, {"camping_on": False, "shore_on": True}],
    )  # fmt: skip
    session(  # the integrator saturated low, then the cabin drops below the target: it integrates again
        rec, "windup_low_then_undershoot", 0,
        [{**auto, "heater": run_h(1), "ref": sensor(2300)}]
        + [{"ref": sensor(2300)}] * 4
        + [{"ref": sensor(2050)}] * 2,
    )  # fmt: skip
    _night_sessions(rec)
    _hold_and_blocked_sessions(rec)


def _night_sessions(rec):
    P, W, C = D["period_ms"], D["night_wait_ms"], D["night_confirm_ms"]
    auto, night = {"request": "AUTO"}, {"night": True}
    cont = run_h(6, 1)
    session(  # pre-set, wait; a step of 1500 ms leaves a remaining time that is no multiple of 1000
        rec, "night_preset_then_timeout", 0,
        [{**auto, **night}, {**night, "dt": 1500}, {**night}, {**night, "dt": W - P - 1500}, {**night, "dt": 1}],
    )  # fmt: skip
    session(
        rec, "night_wait_adopt", 0,
        [{**auto, **night}, {**night, "heater": cont}, {**night, "heater": cont, "dt": C}, {}],
    )  # fmt: skip
    session(  # 20 s of Dauerbetrieb, off again, then a confirmed one
        rec, "night_unconfirmed", 0,
        [
            {**auto, **night},
            {**night, "heater": cont, "dt": P},
            {**night, "heater": cont, "dt": 20000},
            {**night, "heater": OFF_H, "dt": 5000},
            {**night, "heater": cont, "dt": P},
            {**night, "heater": cont, "dt": C},
            {},
        ],
    )  # fmt: skip
    session(  # Dauerbetrieb from 4:50 on is adopted at 5:20, after the wait ended at 5:00
        rec, "night_confirm_past_wait", 0,
        [
            {**auto, **night},
            {**night, "heater": cont, "dt": W - 10000},
            {**night, "heater": cont, "dt": 10000},
            {**night, "heater": cont, "dt": C - 10000},
            {},
        ],
    )  # fmt: skip
    session(
        rec, "night_user_off", 0,
        [{**auto, **night}, {**night, "request": "OFF"}, {}],
    )  # fmt: skip
    session(  # an owner OFF in the wait stops an immediate run (power off) and an unconfirmed Dauerbetrieb
        rec, "night_user_off_stops_run", 0,
        [{**auto, **night, "heater": run_h(5)}, {**night, "request": "OFF", "heater": run_h(5)}, {"heater": OFF_H, "dt": 1000}],
    )  # fmt: skip
    session(
        rec, "night_user_off_stops_continuous", 0,
        [{**auto, **night}, {**night, "heater": cont, "dt": 1000}, {**night, "enabled": False, "heater": cont},
         {"heater": OFF_H, "dt": 1000}],
    )  # fmt: skip
    session(
        rec, "night_user_off_link_down", 0,
        [{**auto, **night, "heater": run_h(5)}, {**night, "request": "OFF", "heater": run_h(5), "link_ready": False},
         {"heater": run_h(5)}, {"heater": OFF_H, "dt": 1000}],
    )  # fmt: skip
    session(
        rec, "night_disabled_in_wait", 0,
        [{**auto, **night}, {**night, "enabled": False}, {}],
    )  # fmt: skip
    session(
        rec, "night_fault_in_wait", 0,
        [{**auto, **night}, {**night, "heater": {**run_h(4), "ErrorCode": 3}}, {}],
    )  # fmt: skip
    session(  # the pre-set is refused; it is not retried and the wait goes on
        rec, "night_preset_refused", 0,
        [{**auto, **night}, {**night, "ack": "REFUSED"}, {**night}, {**night, "heater": cont, "dt": C}, {**night, "heater": cont, "dt": C}],
    )  # fmt: skip
    session(
        rec, "night_skips_warm", 0,
        [{**auto, **night, "ref": sensor(D["band_hi"] + 2101)}, {**night, "ref": sensor(D["band_hi"] + 2101)}],
    )  # fmt: skip
    session(  # a day start of the same warm cabin is refused
        rec, "day_warm_refused", 0,
        [{**auto, "ref": sensor(D["band_hi"] + 2101)}],
    )  # fmt: skip
    session(  # an immediate run enters the wait without a command; Dauerbetrieb then takes over
        rec, "night_run_then_continuous", 0,
        [{**auto, **night, "heater": run_h(5)}, {**night, "heater": run_h(5)}, {**night, "heater": cont, "dt": 1000},
         {**night, "heater": cont, "dt": C}, {}],
    )  # fmt: skip
    session(  # Dauerbetrieb already on: taken over at once
        rec, "night_continuous_at_once", 0,
        [{**auto, **night, "heater": cont}, {}],
    )  # fmt: skip
    session(
        rec, "night_odd_cfg", 1,
        [{**auto, **night}, {**night, "heater": cont, "dt": 1000}, {**night, "heater": cont, "dt": ODD["night_confirm_ms"]}, {}],
    )  # fmt: skip
    session(  # the night setting is latched at the start: a later input change does not matter
        rec, "night_latched", 0,
        [{**auto, **night}, {"night": False}, {"night": False, "heater": cont, "dt": C}, {"night": True}, {}],
    )  # fmt: skip


def _hold_and_blocked_sessions(rec):
    auto = {"request": "AUTO"}
    cont = run_h(6, 1)
    session(  # an owner takeover starts from the demand
        rec, "adopt_run_to_demand", 0,
        [{**auto, "heater": run_h(9), "ref": sensor(2000)}, {"ref": sensor(2000)}],
    )  # fmt: skip
    session(
        rec, "adopt_continuous_to_demand", 0,
        [{**auto, "heater": run_h(2, 1), "ref": sensor(1200)}, {"ref": sensor(1200)}],
    )  # fmt: skip
    session(
        rec, "adopt_no_reference", 0,
        [{**auto, "heater": run_h(5), "ref": None}, {"ref": None}],
    )  # fmt: skip
    session(
        rec, "adopt_link_down", 0,
        [{**auto, "heater": run_h(9), "ref": sensor(2000), "link_ready": False}, {"ref": sensor(2000)}],
    )  # fmt: skip
    session(  # continuous heating is held at min_level instead of stopped
        rec, "continuous_overheat_holds", 0,
        [{**auto, "heater": cont, "ref": sensor(2000)}, {"ref": sensor(2600)}, {"ref": sensor(2600)}, {"ref": sensor(2000)}],
    )  # fmt: skip
    session(
        rec, "continuous_target_exceeded_holds", 0,
        [{**auto, "heater": run_h(1, 1), "ref": sensor(2350)}] + [{"ref": sensor(2350)}] * 17,
    )  # fmt: skip
    session(
        rec, "continuous_hold_link_down", 0,
        [{**auto, "heater": cont, "ref": sensor(2000)}, {"ref": sensor(2600), "link_ready": False}],
    )  # fmt: skip
    session(
        rec, "continuous_hold_odd_cfg", 1,
        [{**auto, "heater": run_h(8, 1), "ref": sensor(2000)}, {"ref": sensor(4600)}, {"ref": sensor(4600)}],
    )  # fmt: skip
    # blocked: idle OFF, nothing requested; the first failing check of the list wins
    stale = D["heater_stale_ms"] + 1
    known = lambda **k: {**KNOWN, **k}  # noqa: E731
    session(
        rec, "blocked_codes", 0,
        [
            {},
            {"heater": {"Installed": 0}},
            {"heater": {"Installed": 1, "ErrorCode": 3}},
            {"heater": {"Installed": 1, "ErrorCode": 5}},
            {"heater": {"Installed": 1, "OperationModeAirHeater": 3}},
            {"heater": {"Installed": 1, "OperationModeAirHeater": 3}, "heater_age_ms": stale},
            {"heater": None, "known": known(camping_on=False)},
            {"heater": None, "known": known(camping_on=False, shore_on=True)},
            {"heater": None, "known": known(ignition_on=True)},
            {"heater": None, "known": known(ignition_on=None)},
            {"ref": None},
            {"ref": sensor(1700, age=stale)},
            {"heater": {"Installed": 0}, "ref": None, "known": known(camping_on=False)},
        ],
    )  # fmt: skip
    session(  # the live and the last-known frames differ: the card blocks on the known ones
        rec, "blocked_known_frames", 0,
        [
            {"camping_on": True, "known": known(camping_on=False)},
            {"ignition_on": False, "known": known(ignition_on=True)},
            {"camping_on": False, "known": known()},
            {**auto, "camping_on": False, "known": known()},
        ],
    )  # fmt: skip


REFUSALS = [
    {"enabled": False},
    {"link_ready": False},
    {"heater": None},
    {"heater": {"Installed": 0}},
    {"heater": {"Installed": 1, "ErrorCode": 3}},
    {"heater": {"Installed": 1, "ErrorCode": 4}},
    {"heater": {"Installed": 1, "ErrorCode": 5}},
    {"heater": {"Installed": 1, "OperationModeAirHeater": 3}},
    {"camping_on": False},
    {"ignition_on": True},
    {"ignition_on": None},
    {"ref": None},
    {"ref": sensor(None)},
    {"ref": sensor(1700, age=None)},
    {"ref": sensor(1700, age=D["stale_after_ms"] + 1)},
]


def grid(rec):
    """Each base state x one changed input (one factor at a time)."""
    bases = []
    seen = set()

    def keep(st):
        key = json.dumps(st, sort_keys=True)
        if key not in seen:
            seen.add(key)
            bases.append(copy.deepcopy(st))

    keep(th.initial_state())
    probe = Rec()
    for steps in (
        [{"request": "AUTO"}],  # START: power on pending
        [{"request": "AUTO", "heater": run_h(5)}],  # adopted, running
        [{"request": "AUTO", "heater": run_h(5, 1)}],  # adopted, continuous
        [{"request": "AUTO", "heater": run_h(5), "ref": sensor(900)}, {"heater": run_h(5), "ref": sensor(900), "dt": 400000}],
        [{"request": "AUTO", "heater": run_h(5)}, {"request": "OFF", "heater": run_h(5)}],  # stop pending
        [{"request": "AUTO", "heater": run_h(5)}, {"request": "OFF", "heater": run_h(5), "link_ready": False}],
        [{"request": "AUTO", "heater": run_h(5)}, {"heater": run_h(5), "ref": None}],  # DEGRADED, level 1 pending
        [{"request": "AUTO", "heater": run_h(5), "backup": sensor(1800, alias="bed")}, {"heater": run_h(5), "ref": None, "backup": sensor(1800, alias="bed")}],
        [{"request": "AUTO", "heater": run_h(1), "ref": sensor(2350)}, {"heater": run_h(1), "ref": sensor(2350)}],
        [{"request": "AUTO", "heater": run_h(5)}, {"heater": run_h(5), "batt2_dv": 100}],
        [{"request": "AUTO", "night": True}],  # WAITING, demand pre-set
        [{"request": "AUTO", "night": True}, {"night": True, "heater": run_h(6, 1), "dt": 1000}],  # cont_since set
        [{"request": "AUTO", "heater": run_h(6, 1)}, {"heater": run_h(6, 1), "ref": sensor(2600)}],  # held at min
    ):  # fmt: skip
        keep(session(probe, "probe", 0, steps))
    t = 2_000_000
    for b, (j, change) in itertools.product(bases, enumerate(_variations())):
        for req in ("AUTO",) if b == th.initial_state() else ((None, "AUTO", "OFF")[j % 3],):
            inp = base(now_ms=t + change.get("dt", 0), request=req, heater=follow(b))
            if b["pending"] is not None and j % 2:
                inp["cmd"] = {
                    "what": b["pending"]["what"],
                    "result": ("OK", "BUSY", "REFUSED", "NOT_READY")[j % 4],
                }
            inp.update({k: v for k, v in change.items() if k != "dt"})
            rec.step("grid", 0, inp, b)


def _variations():
    P = D["period_ms"]
    out = [{}, {"dt": P}, {"dt": D["verify_ms"]}, {"dt": D["batt_hold_ms"]}, {"dt": D["heater_stale_ms"] + 1}]
    out += REFUSALS
    out += [
        {"night": True},
        {"night": True, "dt": D["night_wait_ms"]},
        {"night": True, "dt": D["night_confirm_ms"], "heater": run_h(5, 1)},
        {"night": True, "heater": run_h(5, 1)},
        {"night": True, "ref": sensor(3000)},
        {"known": {**KNOWN, "camping_on": False}},
        {"known": {**KNOWN, "ignition_on": True}},
        {"known": {**KNOWN, "ignition_on": None}},
        {"known": {**KNOWN, "camping_on": False, "shore_on": True}},
        {"heater": OFF_H, "dt": P},
        {"heater": run_h(2), "dt": P},
        {"heater": run_h(9), "dt": P},
        {"heater": run_h(5, 1), "dt": P},
        {"heater": {**run_h(5), "ErrorCode": 1}},
        {"heater": {**run_h(5), "ErrorCode": 7}},
        {"heater": {**run_h(5), "FaultTriggerBit": 1}},
        {"heater_age_ms": D["heater_stale_ms"] + 1},
        {"batt2_dv": 100, "dt": P},
        {"batt2_dv": None, "dt": P},
        {"ref": sensor(3000), "dt": P},
        {"ref": sensor(2600), "dt": P},
        {"ref": sensor(500), "dt": P},
        {"ref": None, "backup": sensor(2000, alias="bed"), "dt": P},
        {"outside_on": True, "out": sensor(-1000, alias="out"), "dt": P},
        {"outside_on": True, "out": None, "dt": P},
        {"target": 500, "dt": P},
        {"target": 3000, "dt": P},
        {"shore_on": True, "camping_on": False},
    ]
    return out


def sweep(rec, n=2000):
    """Seeded random walks: the heater mostly follows the thermostat, sometimes does its own thing."""
    rnd = random.Random(4711)
    P = D["period_ms"]
    dts = (0, 1000, D["verify_ms"], P, P, P, 2 * P, 3 * P + 1, D["min_dwell_ms"], D["heater_stale_ms"] + 1,
           D["night_confirm_ms"], D["night_wait_ms"])  # fmt: skip
    heaters = (
        OFF_H, NO_ID, run_h(1), run_h(3), run_h(5), run_h(10), run_h(0), run_h(11), run_h(4, 1),
        {"Installed": 0}, {"Installed": 1, "OperationModeAirHeater": 3},
        {**run_h(4), "ErrorCode": 1}, {**run_h(4), "ErrorCode": 4}, {**run_h(4), "ErrorCode": 6},
        {**run_h(4), "FaultTriggerBit": 1}, None,
    )  # fmt: skip
    while sum(c[0] == "sweep" for c in rec.cases) < n:
        k = rnd.choice((0, 0, 1))
        cfg = CFGS[k]
        st, now, last = th.initial_state(), rnd.randrange(0, 10**7), None
        t, target = rnd.randrange(300, 2800), rnd.choice((1500, 2100, 2350, 3000))
        for _ in range(rnd.randrange(3, 40)):
            now += rnd.choice(dts) if k == 0 else rnd.choice(dts) // 2 + rnd.choice((0, 1))
            t += rnd.randrange(-150, 151)
            r = rnd.random()
            ref = (
                sensor(t)
                if r < 0.85
                else rnd.choice((None, sensor(None), sensor(t, age=cfg["stale_after_ms"] + 1)))
            )
            h = follow(st, rnd.randrange(1, 11)) if rnd.random() < 0.7 else rnd.choice(heaters)
            cmd = None
            if last and last["cmds"]:
                res = rnd.choice(("OK", "OK", "OK", "BUSY", "NOT_READY", "REFUSED", "TIMEOUT"))
                cmd = {"what": last["cmds"][-1][0], "result": res}
            inp = base(
                now_ms=now,
                enabled=rnd.random() < 0.97,
                request=rnd.choice((None,) * 8 + ("AUTO", "AUTO", "OFF")),
                target=target if rnd.random() < 0.95 else rnd.choice((500, 2000, 3000)),
                runtime_min=cfg["runtime_min"],
                ref=ref,
                backup=rnd.choice((None, None, sensor(t + rnd.randrange(-300, 300), alias="bed"))),
                out=rnd.choice((None, sensor(rnd.randrange(-2000, 2500), alias="out"))),
                outside_on=rnd.random() < 0.5,
                heater=h,
                heater_age_ms=rnd.choice((0, 0, 0, 5000, cfg["heater_stale_ms"] + 1, None)),
                link_ready=rnd.random() < 0.9,
                camping_on=rnd.random() < 0.95,
                shore_on=rnd.random() < 0.2,
                ignition_on=rnd.choice((False,) * 12 + (True, None)),
                batt2_dv=rnd.choice((128, 128, 128, 118, 100, None)),
                cmd=cmd,
                night=rnd.random() < 0.25,
                known={
                    "camping_on": rnd.random() < 0.9,
                    "shore_on": rnd.random() < 0.2,
                    "ignition_on": rnd.choice((False,) * 8 + (True, None)),
                },
            )
            last, st = rec.step("sweep", k, inp, st)


def heater_cases():
    vals = {
        "Installed": (None, 0, 1),
        "HeatingLevel": (None, 0, 1, 10, 11),
        "ErrorCode": (None, 0, 1, 4, 5, 9),
        "FaultTriggerBit": (None, 1),
        "PermanentOperation": (0, 1),
        "NormalOperation": (0, 1),
        "OperationModeAirHeater": (None, 3),
    }
    rnd = random.Random(4711)
    cases = [[_heater(None) + [0], ["UNKNOWN", None, 0]]]
    combos = list(itertools.product(*vals.values()))
    for combo in rnd.sample(combos, 120):
        d = {k: v for k, v in zip(HKEYS, combo) if v is not None} or {"Installed": 1}
        for age in (0, D["heater_stale_ms"], D["heater_stale_ms"] + 1, None) if combo[0] == 1 else (0,):
            cases.append([_heater(d) + [age], list(th.heater_state(d, age, D))])
    return cases


def generate():
    rec = Rec()
    sessions(rec)
    grid(rec)
    sweep(rec)
    return {"cfgs": CFGS, "heater": heater_cases(), "cases": rec.cases, "tags": rec.tags}


def coverage():
    """Tags of the control-law branches the vectors reach (wraps th.control while generating)."""
    tags = set()
    orig = th.control

    def spy(t_ref, t_out, target, dt_ms, ctl, cfg, cur_level, now_ms):
        e = target - t_ref
        kpe = cfg["kp"] * e
        u_out = (
            None
            if t_out is None
            else min(max(th._rdiv(cfg["ka"] * max(0, target - t_out), 100), 0), cfg["u_out_max"])
        )
        i = ctl["i"]
        if cur_level is not None and (ctl["u_out"] is None) != (u_out is None):
            tags.add("outside_found" if ctl["u_out"] is None else "outside_lost")
            i += (ctl["u_out"] or 0) - (u_out or 0)
        u_old = th._rdiv(kpe, 100) + i + (u_out or 0)
        lo = cfg["min_level"] * 1000
        gap = dt_ms > 3 * cfg["period_ms"]
        if gap and e:
            tags.add("gap_frozen")
        if not gap and u_old < lo and e < 0:
            tags.add("windup_low" if lo == 1000 else "windup_min_level")
        if not gap and u_old > 10000 and e > 0:
            tags.add("windup_high")
        if not gap and dt_ms and ((u_old < lo and e > 0) or (u_old > 10000 and e < 0)):
            tags.add("opposite_sign_integrates")
        if cur_level is None:
            tags.add("cur_level_none")
        if kpe < 0 and kpe % 100 == 50:
            tags.add("neg_half_p")
        res = orig(t_ref, t_out, target, dt_ms, ctl, cfg, cur_level, now_ms)
        u = res[1]["u"]
        if res[2] not in ("OVERHEAT", "TARGET_EXCEEDED_AT_MIN_LEVEL") and abs(u) % 1000 == 500:
            tags.add("neg_half_level" if u < 0 else "pos_half_level")
        return res

    th.control = spy
    try:
        tags |= generate()["tags"]
    finally:
        th.control = orig
    return tags


def render() -> str:
    """The exact checked-in file content: one case per line."""
    g = generate()
    head = json.dumps({"cfgs": g["cfgs"]})[:-1]
    heater = ",\n".join(json.dumps(c, separators=(",", ":")) for c in g["heater"])
    body = ",\n".join(json.dumps(c, separators=(",", ":")) for c in g["cases"])
    return head + ', "heater": [\n' + heater + '\n], "cases": [\n' + body + "\n]}\n"


def main() -> int:
    text = render()
    if "--check" in sys.argv:
        if not OUT.is_file() or OUT.read_text(encoding="utf-8") != text:
            print("STALE: %s — run python3 -m tools.gen_thermostat_vectors" % OUT, file=sys.stderr)
            return 1
        print("fresh: %s" % OUT)
        return 0
    OUT.write_text(text, encoding="utf-8")
    g = generate()
    print("wrote %s (%d step cases, %d heater cases)" % (OUT, len(g["cases"]), len(g["heater"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
