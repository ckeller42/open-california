"""Cabin thermostat: pure decision logic (stdlib-only, integer arithmetic, no BLE/serve deps).

The ESP satellite holds the van cabin at a target temperature by changing the air heater's
level. This module is the Python authority; a C twin is held equal by generated vectors. All
arithmetic is integer (centi-°C, milli-levels, ms) so both languages agree bit for bit; division
rounds half away from zero through :func:`_rdiv`.

.. req:: Heater state from the raw 1702 dict
   :id: R_THERMOSTAT_STATE
   :status: implemented
   :tags: thermostat, airheater

   The thermostat shall reduce the raw decoded air-heater state (char 1702) to one of
   :data:`HEATER_STATES`, a heating level 1..10 or ``None``, and the unit's error code.
   Precedence: stale read, not installed, error code or fault bit, continuous operation, normal
   operation, armed departure timer, off. A level outside 1..10 is unknown.
"""

from __future__ import annotations

import copy

HEATER_STATES = ("OFF", "RUNNING", "CONTINUOUS", "TIMER_ARMED", "FAULT", "LOCKED", "BLOCKED", "UNKNOWN")

#: Settings table (Python ``THERMO_DEFAULTS`` = C ``CALI_THERMO_DEFAULTS``); units in the key's comment.
THERMO_DEFAULTS: dict[str, int] = {
    "period_ms": 60000,  # ms
    "kp": 1500,  # milli-level per °C
    "ki": 50,  # milli-level per °C per minute
    "ka": 100,  # milli-level per °C (outside)
    "u_out_max": 3000,  # milli-level
    "hyst": 500,  # milli-level
    "min_dwell_ms": 300000,  # ms
    "band_hi": 200,  # centi-°C
    "hot_hold_ms": 900000,  # ms
    "overheat": 400,  # centi-°C
    "min_level": 1,  # level
    "stale_after_ms": 600000,  # ms
    "heater_stale_ms": 180000,  # ms
    "batt_min_dv": 119,  # 0.1 V
    "batt_hold_ms": 300000,  # ms
    "verify_ms": 10000,  # ms
    "runtime_min": 120,  # min (AUTO from OFF; owner-settable 10-120)
    "night_wait_ms": 300000,  # ms (night start: wait for Dauerbetrieb at the panel)
    "night_confirm_ms": 30000,  # ms (Dauerbetrieb counts after it held this long)
}

#: Target range in centi-°C.
TARGET_MIN, TARGET_MAX, TARGET_STEP = 500, 3000, 50

#: Every reason code the thermostat reports (exact strings).
REASONS = (
    "AUTO_ON",
    *(
        "AUTO_REFUSED_" + why
        for why in (
            "DISABLED",
            "NOT_INSTALLED",
            "FAULT",
            "NO_LINK",
            "NO_CAMPING",
            "IGNITION",
            "NO_REFERENCE",
            "TIMER_ARMED",
            "STALE_HEATER",
            "WARM",
        )
    ),
    "START",
    "ADOPT_RUN",
    "ADOPT_CONTINUOUS",
    "LEVEL_UP",
    "LEVEL_DOWN",
    "HOLD",
    "TARGET_EXCEEDED_AT_MIN_LEVEL",
    "OVERHEAT",
    "BACKUP_SELECTED",
    "REFERENCE_SELECTED",
    "REFERENCE_LOST",
    "OUTSIDE_INACTIVE",
    *("FAULT_%d" % n for n in range(1, 16)),
    "LOCKED",
    "FOREIGN_CHANGE",
    "NOT_APPLIED",
    "START_NOT_CONFIRMED",
    "RUN_ENDED",
    "CONTINUOUS_ENDED",
    "BATTERY_LOW",
    "HEATER_UNKNOWN",
    "USER_OFF",
    "STOPPING",
    "STOP_DROPPED",
    "WAITING_FOR_CONTINUOUS",
    "NIGHT_WAIT_ENDED",
    "HOLD_AT_MIN_LEVEL",
)


def _rdiv(a: int, b: int) -> int:
    """Integer division rounding half away from zero (the one rounding rule, Python and C).

    :param a: dividend.
    :param b: divisor, non-zero.
    :returns: ``a / b`` rounded to the nearest integer, halves away from zero.
    """
    q, r = divmod(abs(a), abs(b))
    if 2 * r >= abs(b):
        q += 1
    return q if (a < 0) == (b < 0) else -q


def heater_state(d: dict | None, age_ms: int | None, cfg: dict) -> tuple[str, int | None, int]:
    """Reduce the raw decoded 1702 dict to ``(state, level_or_None, error_code)``.

    :param d: raw decoded air-heater dict (``Installed``, ``ErrorCode``, ``HeatingLevel`` ...).
    :param age_ms: age of that read in ms; ``None`` (unknown age) counts as stale.
    :param cfg: settings dict; ``heater_stale_ms`` is the staleness limit.
    :returns: state from :data:`HEATER_STATES`; the level 1..10 or ``None`` (0, 11..15 and a
        missing value are no level); the ``ErrorCode`` (-1 when not installed, 0 when none).
    """
    if not d or age_ms is None or age_ms > cfg["heater_stale_ms"]:
        return "UNKNOWN", None, 0
    if d.get("Installed") == 0:
        return "FAULT", None, -1
    lvl = d.get("HeatingLevel")
    level = lvl if isinstance(lvl, int) and 1 <= lvl <= 10 else None
    err = d.get("ErrorCode") or 0
    if err == 4:
        state = "LOCKED"
    elif err == 5:
        state = "BLOCKED"
    elif err or d.get("FaultTriggerBit"):
        state = "FAULT"
    elif d.get("PermanentOperation"):
        state = "CONTINUOUS"
    elif d.get("NormalOperation"):
        state = "RUNNING"
    elif d.get("OperationModeAirHeater") == 3:
        state = "TIMER_ARMED"
    else:
        state = "OFF"
    return state, level, err


def control(
    t_ref: int,
    t_out: int | None,
    target: int,
    dt_ms: int,
    ctl: dict,
    cfg: dict,
    cur_level: int | None,
    now_ms: int,
) -> tuple[int | None, dict, str]:
    """One step of the integer PI law with outside feed-forward (demand in milli-levels to a level).

    .. req:: Thermostat control law
       :id: R_THERMOSTAT_CONTROL
       :status: implemented
       :tags: thermostat

       The thermostat shall compute the demand ``u = p + i + u_out`` in milli-levels from the
       reference error (centi-°C), an integrator frozen on long step gaps and by anti-windup at
       ``min_level`` and level 10, and an outside feed-forward capped at ``u_out_max``. When the
       outside reading appears or disappears while the level is known, the feed-forward's share
       shall move into the integrator so that the demand does not jump. It shall change the level only when the
       demand differs from the current level by at least ``500 + hyst`` and ``min_dwell_ms`` has
       passed. It shall report ``OVERHEAT`` above ``target + overheat`` and
       ``TARGET_EXCEEDED_AT_MIN_LEVEL`` after ``hot_hold_ms`` above ``target + band_hi`` at the
       minimum level. All arithmetic is integer; division uses :func:`_rdiv`.

    :param t_ref: reference temperature in centi-°C.
    :param t_out: outside temperature in centi-°C, or ``None`` (no feed-forward).
    :param target: target temperature in centi-°C.
    :param dt_ms: time since the previous step in ms.
    :param ctl: controller state ``{i, last_change_ms, hot_since_ms, u, u_out}``; not modified.
        ``u_out`` is ``None`` while no outside reading is used.
    :param cfg: settings dict (:data:`THERMO_DEFAULTS` keys).
    :param cur_level: the heater's current level 1..10, or ``None`` when unknown.
    :param now_ms: current time in ms.
    :returns: ``(new_level_or_None, new_ctl, reason)``. The level is ``None`` for ``HOLD``,
        ``OVERHEAT`` and ``TARGET_EXCEEDED_AT_MIN_LEVEL``. With ``cur_level`` ``None`` the level
        comes from the demand alone, with reason ``LEVEL_UP``.
    """
    e = target - t_ref
    p = _rdiv(cfg["kp"] * e, 100)
    u_out = (
        None
        if t_out is None
        else min(max(_rdiv(cfg["ka"] * max(0, target - t_out), 100), 0), cfg["u_out_max"])
    )
    i = ctl["i"]
    prev = ctl["u_out"]
    if cur_level is not None and (prev is None) != (u_out is None):
        i += (prev or 0) - (u_out or 0)  # bumpless: the outside share moves into the integrator
    ff = u_out or 0
    u_old = p + i + ff
    lo = cfg["min_level"] * 1000
    frozen = dt_ms > 3 * cfg["period_ms"] or (u_old < lo and e < 0) or (u_old > 10000 and e > 0)
    if not frozen:
        i += _rdiv(cfg["ki"] * e * dt_ms, 100 * 60000)
    u = p + i + ff
    hot = cur_level == cfg["min_level"] and t_ref > target + cfg["band_hi"]
    hot_since = (ctl["hot_since_ms"] if ctl["hot_since_ms"] is not None else now_ms) if hot else None
    new = {**ctl, "i": i, "u": u, "u_out": u_out, "hot_since_ms": hot_since}
    if t_ref > target + cfg["overheat"]:
        return None, new, "OVERHEAT"
    if hot_since is not None and now_ms - hot_since >= cfg["hot_hold_ms"]:
        return None, new, "TARGET_EXCEEDED_AT_MIN_LEVEL"
    level = min(max(_rdiv(u, 1000), cfg["min_level"]), 10)
    if cur_level is None:
        return level, new, "LEVEL_UP"
    if (
        level == cur_level
        or abs(u - cur_level * 1000) < 500 + cfg["hyst"]
        or now_ms - ctl["last_change_ms"] < cfg["min_dwell_ms"]
    ):
        return None, new, "HOLD"
    new["last_change_ms"] = now_ms
    return level, new, "LEVEL_UP" if level > cur_level else "LEVEL_DOWN"


def track(ctl: dict, cur_level: int, t_ref: int, target: int, cfg: dict) -> dict:
    """Make the next demand equal the current level (bumpless adopt, backup switch, target change).

    :param ctl: controller state; not modified.
    :param cur_level: the heater's current level.
    :param t_ref: reference temperature in centi-°C.
    :param target: target temperature in centi-°C.
    :param cfg: settings dict.
    :returns: a copy of ``ctl`` whose integrator makes ``p + i + u_out == cur_level * 1000``.
    """
    p = _rdiv(cfg["kp"] * (target - t_ref), 100)
    return {**ctl, "i": cur_level * 1000 - p - (ctl["u_out"] or 0)}


_FAULTS = ("FAULT", "LOCKED", "BLOCKED")
_HEATING = ("RUNNING", "CONTINUOUS")


def initial_state() -> dict:
    """The session state after boot: mode ``OFF`` (AUTO is never persisted).

    :returns: a fresh state dict for :func:`step`.
    """
    return {
        "mode": "OFF",
        "active": "ref",
        "ctl": {"i": 0, "last_change_ms": 0, "hot_since_ms": None, "u": 0, "u_out": None},
        "pending": None,
        "expect": None,
        "batt_low_since_ms": None,
        "stop_snapshot": None,
        "last_ctl_ms": None,
        "night": False,
        "wait_until_ms": None,
        "cont_since_ms": None,
    }


def _valid(r: dict | None, cfg: dict) -> bool:
    if not r:
        return False
    return r["value"] is not None and r["age_ms"] is not None and r["age_ms"] <= cfg["stale_after_ms"]


def _snap(d: dict, level: int | None) -> dict:
    return {
        "normal": 1 if d.get("NormalOperation") else 0,
        "permanent": 1 if d.get("PermanentOperation") else 0,
        "level": level,
    }


def _same(a: dict, b: dict) -> bool:
    """Equal in normal/permanent and in the level; an unknown level (``None``) on either side is no difference."""
    return (
        a["normal"] == b["normal"]
        and a["permanent"] == b["permanent"]
        and (a["level"] is None or b["level"] is None or a["level"] == b["level"])
    )


def _applied(p: dict, snap: dict) -> bool:
    if p["what"] == "level":
        return snap["level"] == p["value"]
    if p["what"] == "permanent":
        return snap["permanent"] == 0
    return snap["normal"] == (1 if p["value"] == "on" else 0)


def _resend(p: dict, st: dict, inp: dict, full: bool) -> list:
    if full and p["what"] == "power" and p["value"] == "on":
        return [("runtime", inp["runtime_min"]), ("level", st["expect"]["level"]), ("power", "on")]
    return [(p["what"], p["value"])]


def _pend(what: str, value, now: int, before: int | None = None, why: str | None = None) -> dict:
    """A pending command; ``first_ms`` = first send, ``before`` = the heater's level before it,
    ``why`` = what caused a stop (``USER_OFF``, ``DISABLED``, ``OVERHEAT``, ``TARGET_EXCEEDED_AT_MIN_LEVEL``,
    ``BATTERY_LOW``), ``None`` for other commands."""
    return {
        "what": what, "value": value, "since_ms": now, "tries": 1, "first_ms": now, "before": before, "why": why,
    }  # fmt: skip


def _start_retry_ok(p: dict, inp: dict, cfg: dict) -> bool:
    """§9.1: re-send ``power on`` only soon after the first send and while entry still holds."""
    return (
        inp["now_ms"] - p["first_ms"] <= cfg["heater_stale_ms"]
        and inp["enabled"]
        and (inp["camping_on"] or inp["shore_on"])
        and inp["ignition_on"] is False
        and _valid(inp["ref"], cfg)
    )


def _stop(st: dict, can_send: bool, now: int, why: str) -> list:
    """End AUTO with a normal stop of what the ESP left running; link down → ``STOPPING``.

    ``why`` (the stop's cause) goes with the pending stop or the ``STOPPING`` snapshot.
    """
    e = st["expect"] or {"normal": 0, "permanent": 0, "level": None}
    what = "permanent" if e["permanent"] else "power" if e["normal"] else None
    st["pending"], st["mode"] = None, "OFF"
    if what is None:
        return []
    if can_send:
        st["pending"] = _pend(what, "off", now, why=why)
        return [(what, "off")]
    st["mode"], st["stop_snapshot"] = "STOPPING", {**e, "why": why}
    return []


def _end(st: dict) -> list:
    st["mode"], st["pending"] = "OFF", None
    return []


def _fault_reason(code: int) -> str:
    return "LOCKED" if code == 4 else "FAULT_%d" % code if 1 <= code <= 15 else "HEATER_UNKNOWN"


def _takeover(
    st: dict, snap: dict | None, level: int | None, t_ref: int | None, t_out: int | None, inp: dict, cfg: dict
) -> list:
    """Start the controller from its demand (integrator 0) and send that level at once if it differs.

    ``t_ref`` ``None`` (no valid reference): take the heater as it is; rule (7) acts next step.
    """
    assert snap is not None  # only called with a fresh OFF, RUNNING or CONTINUOUS heater
    now = inp["now_ms"]
    st["pending"], st["expect"], st["last_ctl_ms"] = None, dict(snap), now
    # integrator 0; the dwell restarts only with a level sent
    ctl0 = {"i": 0, "last_change_ms": st["ctl"]["last_change_ms"], "hot_since_ms": None, "u": 0, "u_out": 0}
    st["ctl"] = ctl0
    if t_ref is None:
        return []
    lvl, st["ctl"], _ = control(t_ref, t_out, inp["target"], 0, ctl0, cfg, None, now)
    lvl = lvl or cfg["min_level"]  # overheat: the lowest level
    if lvl == level or not inp["link_ready"]:
        return []
    st["pending"] = _pend("level", lvl, now, level)
    st["expect"]["level"] = lvl
    st["ctl"]["last_change_ms"] = now
    return [("level", lvl)]


def blocked(inp: dict, cfg: dict) -> str | None:
    """Why an AUTO request could not start right now, for the card before the owner taps.

    .. req:: Thermostat blocked start
       :id: R_THERMOSTAT_BLOCKED
       :status: implemented
       :tags: thermostat

       The thermostat shall report, with every step, the first entry check that blocks a start,
       without the ``AUTO_REFUSED_`` prefix, in this order: ``NOT_INSTALLED``, ``FAULT``,
       ``TIMER_ARMED`` (from the last 1702 frame, whatever its age), ``NO_CAMPING`` and
       ``IGNITION`` (from the last-known frames, not only this link's), ``NO_REFERENCE``. It shall
       never report ``DISABLED``, ``NO_LINK``, ``STALE_HEATER`` or ``WARM``: the confirmation
       enables, the request wakes the link and checks live data again, and warm is no fault.

    :param inp: the :func:`step` inputs (``heater``, ``known``, ``ref``).
    :param cfg: settings dict.
    :returns: the code, or ``None`` when nothing blocks a start.
    """
    if inp["heater"]:
        hs, _, code = heater_state(inp["heater"], 0, cfg)
        if code == -1:
            return "NOT_INSTALLED"
        if hs in _FAULTS:
            return "FAULT"
        if hs == "TIMER_ARMED":
            return "TIMER_ARMED"
    k = inp["known"]
    if not (k["camping_on"] or k["shore_on"]):
        return "NO_CAMPING"
    if k["ignition_on"] is not False:
        return "IGNITION"
    if not _valid(inp["ref"], cfg):
        return "NO_REFERENCE"
    return None


def _refusal(inp: dict, cfg: dict, hs: str, code: int) -> str | None:
    if not inp["enabled"]:
        return "AUTO_REFUSED_DISABLED"
    if not inp["link_ready"]:
        return "AUTO_REFUSED_NO_LINK"
    if hs == "UNKNOWN":
        return "AUTO_REFUSED_STALE_HEATER"
    if code == -1:
        return "AUTO_REFUSED_NOT_INSTALLED"
    if hs in _FAULTS:
        return "AUTO_REFUSED_FAULT"
    if hs == "TIMER_ARMED":
        return "AUTO_REFUSED_TIMER_ARMED"
    if not (inp["camping_on"] or inp["shore_on"]):
        return "AUTO_REFUSED_NO_CAMPING"
    if inp["ignition_on"] is not False:
        return "AUTO_REFUSED_IGNITION"
    if not _valid(inp["ref"], cfg):
        return "AUTO_REFUSED_NO_REFERENCE"
    if not inp["night"] and hs == "OFF" and inp["ref"]["value"] > inp["target"] + cfg["band_hi"]:
        return "AUTO_REFUSED_WARM"  # a start would soon stop again: a burner short cycle
    return None


def step(inp: dict, cfg: dict, st: dict) -> tuple[dict, dict]:
    """One decision of the thermostat session (pure: no clock, no I/O; integer arithmetic only).

    .. req:: Thermostat session state machine
       :id: R_THERMOSTAT_SESSION
       :status: implemented
       :tags: thermostat, airheater

       The thermostat shall enter AUTO only on an owner request with the feature enabled, a link,
       a fresh, installed, fault-free heater with no armed timer, camping mode or shore power,
       ignition off and a valid reference reading, and from an OFF heater a reference not above
       ``target + band_hi``; otherwise it shall refuse with
       ``AUTO_REFUSED_<WHY>`` and send nothing. An AUTO request that passes these checks drops the
       owner's own pending stop (the last owner request wins); a refused one keeps it. While a
       safety stop (overheat, target exceeded, battery low, feature disabled) is pending or waits
       in ``STOPPING``, an AUTO request is ignored with ``STOPPING``. From OFF it shall send ``runtime``, ``level`` and
       one ``power on`` in that order; a running or continuous heater it shall take over from
       the computed demand (integrator 0) and send that level at once when it differs from the
       heater's. It shall end AUTO without a command on a heater fault (ErrorCode 1 excepted: a
       normal stop, ``BATTERY_LOW``), a change it did not make (also during its own pending level
       command), the end of the run (also a heater switched off during its own pending level
       command: ``RUN_ENDED`` or ``CONTINUOUS_ENDED``), and a command not applied after one retry (an
       owner stop in the step of a refused or unapplied level command stops the heater instead).
       A ``power on`` is retried at most once, only when a fresh readback shows the heater off, within
       ``heater_stale_ms`` of the first send and while the entry conditions still hold; an
       unconfirmed command is never re-sent blind. A refused or unapplied stop reports
       ``NOT_APPLIED``. An owner stop, low second battery, overheat or a target exceeded
       at the minimum level stop the heater (``power off`` or ``permanent off``); with the link
       down the stop waits in ``STOPPING`` and is dropped when the heater changed meanwhile.
       A lost reference switches to a valid backup, a lost active backup back to a valid
       reference, else the level goes to ``min_level``.

    .. req:: Thermostat night mode
       :id: R_THERMOSTAT_NIGHT
       :status: implemented
       :tags: thermostat, airheater

       An AUTO request with ``night`` shall skip the ``WARM`` refusal. With the heater off it
       shall send the demand level alone and enter ``WAITING``; with an immediate run it shall
       enter ``WAITING`` without a command; with continuous heating it shall take over at once.
       In ``WAITING`` it shall send nothing more (a failed pre-set is not retried) except a stop: an
       owner OFF or the feature disabled shall stop the heater that runs (``power off`` for an
       immediate run, ``permanent off`` for continuous heating still in its confirm window, nothing
       for an off heater). With the link down or the heater reading stale it shall stop what the
       last fresh reading in the wait showed: the stop waits in ``STOPPING`` and goes out on the
       next fresh reading only if the heater is unchanged. A fault
       shall end the wait without a command; ``BATTERY_LOW`` (ErrorCode 1) too, whatever the heater
       shows. It shall take over continuous heating
       first seen in the wait once a heater reading taken ``night_confirm_ms`` later still shows it
       (a shorter one keeps the wait with its remaining time), and end with ``NIGHT_WAIT_ENDED``
       after ``night_wait_ms`` without continuous heating or with continuous heating first seen
       after that time.
       While it regulates continuous heating (night or not), ``OVERHEAT`` and
       ``TARGET_EXCEEDED_AT_MIN_LEVEL`` shall not stop the heater: it holds ``min_level`` with
       ``HOLD_AT_MIN_LEVEL``. The night setting is latched at the start and cleared at mode OFF.

    Evaluation order per call (the first rule that ends AUTO wins): pending-command bookkeeping,
    ``WAITING`` (owner OFF, fault, confirmed continuous heating, wait end), owner request,
    ``STOPPING``, faults, heater unknown, foreign change, battery, heater ended, reference
    validity, control (at most once per ``period_ms``, never while a command is pending; holds
    ``min_level`` instead of a stop on continuous heating).

    :param inp: inputs ``now_ms``, ``enabled``, ``request`` (``None``/``"AUTO"``/``"OFF"``),
        ``target``, ``runtime_min``, ``ref``/``backup``/``out`` (``{"alias", "value", "age_ms"}``
        or ``None``), ``outside_on``, ``heater`` (raw 1702 dict or ``None``), ``heater_age_ms``,
        ``link_ready``, ``camping_on``, ``shore_on``, ``ignition_on``, ``batt2_dv``, ``cmd``
        (``None`` or ``{"what", "result"}``: the last command of the previous batch the runner
        attempted; the runner stops a batch at the first result that is not ``OK``), ``night``
        (the run-time setting "Nacht") and ``known`` (``{"camping_on", "shore_on",
        "ignition_on"}`` from the last-known frames, for :func:`blocked`).
    :param cfg: settings dict (:data:`THERMO_DEFAULTS` keys).
    :param st: session state (:func:`initial_state`); not modified.
    :returns: ``(out, new_st)``; ``out`` = ``{"cmds": [(what, value)...], "reason", "mode",
        "status"}``. ``reason`` is ``None`` for an idle step in mode ``OFF``. Modes: ``OFF``,
        ``AUTO``, ``DEGRADED``, ``STOPPING``, ``WAITING``.
    """
    st = copy.deepcopy(st)
    now, link, target = inp["now_ms"], inp["link_ready"], inp["target"]
    d = inp["heater"]
    hs, level, code = heater_state(d, inp["heater_age_ms"], cfg)
    snap = _snap(d, level) if hs != "UNKNOWN" else None
    sensors = {"ref": inp["ref"], "backup": inp["backup"]}
    t_out = inp["out"]["value"] if inp["outside_on"] and _valid(inp["out"], cfg) else None
    cmds: list = []
    reason = None
    # an owner OFF (or the feature disabled) while on: a failed level command must not end AUTO first
    owner_off = st["mode"] in ("AUTO", "DEGRADED") and (inp["request"] == "OFF" or not inp["enabled"])

    def out(why):
        if st["mode"] == "OFF":
            st["night"] = False
        if st["mode"] != "WAITING":
            st["wait_until_ms"] = st["cont_since_ms"] = None
        r = sensors[st["active"]]
        status = {
            "heater": hs,
            "t_ref": r["value"] if _valid(r, cfg) else None,
            "t_out": t_out,
            "target": target,
            "level": level,
            "active_alias": r["alias"] if r else None,
            "outside_active": t_out is not None,
            "end": "run" if hs == "RUNNING" else "none",
            "blocked": blocked(inp, cfg),
            "wait_left_s": None
            if st["wait_until_ms"] is None
            else max(0, -((now - st["wait_until_ms"]) // 1000)),
            "night": st["night"] if st["mode"] != "OFF" else inp["night"],
        }
        return {"cmds": cmds, "reason": why, "mode": st["mode"], "status": status}, st

    # (1) pending command: readback, result, verify window
    p = st["pending"]
    if p and st["mode"] == "WAITING":  # the night pre-set: never retried, never ends the wait
        res = (inp["cmd"] or {}).get("result")
        post = snap is not None and now - inp["heater_age_ms"] >= p["since_ms"]
        if (
            (snap is not None and post and _applied(p, snap))
            or res not in (None, "OK")
            or now - p["since_ms"] >= cfg["verify_ms"]
        ):
            st["pending"] = None
        p = None
    if p:
        res = (inp["cmd"] or {}).get("result")
        start = p["what"] == "power" and p["value"] == "on"
        post = snap is not None and now - inp["heater_age_ms"] >= p["since_ms"]  # read after the send
        fail = "START_NOT_CONFIRMED" if start else "NOT_APPLIED"
        e = st["expect"]
        moved = (  # someone else moved the heater during our level command
            p["what"] == "level"
            and snap is not None
            and post
            and hs not in _FAULTS
            and (
                (level is not None and level not in (p["value"], p["before"]))
                or snap["normal"] != e["normal"]
                or snap["permanent"] != e["permanent"]
            )
        )
        if moved:
            _end(st)
            if snap and not (snap["normal"] or snap["permanent"]):  # switched off at the panel: the run ended
                return out("CONTINUOUS_ENDED" if e["permanent"] else "RUN_ENDED")
            return out("FOREIGN_CHANGE")
        if snap is not None and post and _applied(p, snap):
            st["pending"], st["expect"] = None, snap
        elif hs in _FAULTS:
            pass  # rule (3) ends AUTO with the fault's reason
        elif p["what"] == "level" and res == "REFUSED" and owner_off:
            pass  # rule (2) stops the heater: a refused level must not end AUTO and drop the owner's OFF
        elif res == "REFUSED":
            st["pending"] = None
            if p["value"] != "off":
                _end(st)
            return out(fail)  # a refused stop: the heater still runs (status.heater)
        elif res in ("BUSY", "NOT_READY") and link:
            if start and not _start_retry_ok(p, inp, cfg):
                _end(st)
                return out(fail)
            cmds = _resend(p, st, inp, full=inp["cmd"]["what"] != "power")
            p["since_ms"] = now
        elif snap is not None and post and link and now - p["since_ms"] >= cfg["verify_ms"]:
            if p["value"] == "off" and not _same(snap, st["expect"]):
                st["pending"] = None
                return out("STOP_DROPPED")
            if p["tries"] >= 2 or (start and not _start_retry_ok(p, inp, cfg)):
                if not (p["what"] == "level" and owner_off):  # an owner OFF: rule (2) stops the heater
                    if p["value"] != "off":
                        _end(st)
                    st["pending"] = None
                    return out(fail)
            else:
                cmds = _resend(p, st, inp, full=False)
                p["since_ms"], p["tries"] = now, 2
        if cmds:
            reason = "STOPPING" if p["value"] == "off" else "AUTO_ON"

    # (W) night wait: send nothing but a stop; take over Dauerbetrieb once it held night_confirm_ms
    if st["mode"] == "WAITING":
        if snap is not None:
            st["expect"] = dict(snap)  # the last fresh heater: what an owner OFF stops
        if inp["request"] == "OFF" or not inp["enabled"]:
            off_why = "USER_OFF" if inp["enabled"] else "DISABLED"
            # stop what runs: an immediate run or an unconfirmed Dauerbetrieb; with the heater stale
            # or the link down the stop of the last fresh reading waits in STOPPING
            cmds = _stop(st, link and snap is not None, now, off_why)
            return out("STOPPING" if st["mode"] == "STOPPING" else off_why)
        if hs in _FAULTS:
            _end(st)
            return out("BATTERY_LOW" if code == 1 else _fault_reason(code))
        if hs != "CONTINUOUS":
            st["cont_since_ms"] = None
            if now >= st["wait_until_ms"]:
                _end(st)
                return out("NIGHT_WAIT_ENDED")
            return out("WAITING_FOR_CONTINUOUS")
        if st["cont_since_ms"] is None:  # a Dauerbetrieb first seen after the wait is not taken over
            if now >= st["wait_until_ms"]:
                _end(st)
                return out("NIGHT_WAIT_ENDED")
            st["cont_since_ms"] = now
        # confirmed by a heater frame read night_confirm_ms after the first one, not by a cached frame
        if now - inp["heater_age_ms"] - st["cont_since_ms"] < cfg["night_confirm_ms"]:
            return out("WAITING_FOR_CONTINUOUS")
        st["mode"] = "AUTO"
        t_ref = inp["ref"]["value"] if _valid(inp["ref"], cfg) else None
        cmds = _takeover(st, snap, level, t_ref, t_out, inp, cfg)
        return out("ADOPT_CONTINUOUS")

    # (2) owner request (feature disabled while on = owner OFF)
    on = st["mode"] in ("AUTO", "DEGRADED")
    req = "OFF" if on and not inp["enabled"] else inp["request"]
    if req == "OFF" and on:
        cmds = _stop(st, link and snap is not None, now, "USER_OFF" if inp["enabled"] else "DISABLED")
        return out("STOPPING" if st["mode"] == "STOPPING" else "USER_OFF")
    sp = st["pending"]  # in mode OFF only a stop can be pending
    held = sp["why"] if sp else st["stop_snapshot"]["why"] if st["mode"] == "STOPPING" else None
    if req == "AUTO" and not on and held not in (None, "USER_OFF"):
        req, reason = None, "STOPPING"  # a safety stop runs on: the AUTO request is ignored
    if req == "AUTO" and not on:
        why = _refusal(inp, cfg, hs, code)
        if why:
            return out(why)  # the owner's own stop (if any) runs on
        if sp is not None:
            st["pending"], cmds = None, []  # the last owner request wins: drop the owner's stop
        t_ref = inp["ref"]["value"]
        ctl0 = {"i": 0, "last_change_ms": now, "hot_since_ms": None, "u": 0, "u_out": 0}
        st.update(
            mode="AUTO",
            active="ref",
            pending=None,
            batt_low_since_ms=None,
            stop_snapshot=None,
            last_ctl_ms=now,
        )
        st["night"] = inp["night"]
        if inp["night"] and hs != "CONTINUOUS":
            st.update(mode="WAITING", wait_until_ms=now + cfg["night_wait_ms"], cont_since_ms=None)
            assert snap is not None  # _refusal passed: the heater reading is fresh
            st["expect"] = dict(snap)
            if hs == "OFF":  # pre-set the demand: the panel starts Dauerbetrieb at the stored level
                cmds = _takeover(st, snap, level, t_ref, t_out, inp, cfg)
            return out("WAITING_FOR_CONTINUOUS")
        if hs == "OFF":
            lvl, st["ctl"], _ = control(t_ref, t_out, target, 0, ctl0, cfg, None, now)
            lvl = lvl or cfg["min_level"]  # overheat at entry: start low, the next step stops
            st["pending"] = _pend("power", "on", now)
            st["expect"] = {"normal": 1, "permanent": 0, "level": lvl}
            cmds = [("runtime", inp["runtime_min"]), ("level", lvl), ("power", "on")]
            return out("START")
        cmds = _takeover(st, snap, level, t_ref, t_out, inp, cfg)
        return out("ADOPT_CONTINUOUS" if hs == "CONTINUOUS" else "ADOPT_RUN")
    if req == "AUTO" and on and st["active"] == "backup" and _valid(inp["ref"], cfg):
        st["active"] = "ref"  # back to the reference only by the owner
        if level is not None:
            st["ctl"] = track(st["ctl"], level, inp["ref"]["value"], target, cfg)

    # STOPPING: wait for the link, then stop only what the ESP left
    if st["mode"] == "STOPPING":
        snap0 = st["stop_snapshot"]
        if not link or snap is None:
            return out("STOPPING")
        st["mode"], st["stop_snapshot"] = "OFF", None
        if hs not in _HEATING:
            return out("CONTINUOUS_ENDED" if snap0["permanent"] else "RUN_ENDED")
        if not _same(snap, snap0):
            return out("STOP_DROPPED")
        st["expect"] = {k: snap0[k] for k in ("normal", "permanent", "level")}
        cmds = _stop(st, True, now, snap0["why"])
        return out("STOPPING")
    if not on:
        return out(reason)

    # (3) faults: AUTO ends, never retried; only ErrorCode 1 (low battery) stops the heater
    if hs in _FAULTS and code == 1:
        cmds = _stop(st, link, now, "BATTERY_LOW")
        return out("BATTERY_LOW")
    if hs in _FAULTS:
        cmds = _end(st)
        return out(_fault_reason(code))
    if snap is None:
        cmds = []  # no writes until fresh; AUTO stays on
        return out("HEATER_UNKNOWN")

    # (4) foreign change (only while no own command is pending)
    if st["pending"] is None and hs in _HEATING:
        if not _same(snap, st["expect"]):
            cmds = _end(st)
            return out("FOREIGN_CHANGE")
        if st["expect"]["level"] is None:
            st["expect"]["level"] = level

    # (5) second battery low for batt_hold_ms
    dv = inp["batt2_dv"]
    if dv is not None and dv < cfg["batt_min_dv"]:
        if st["batt_low_since_ms"] is None:
            st["batt_low_since_ms"] = now
        if now - st["batt_low_since_ms"] >= cfg["batt_hold_ms"]:
            cmds = _stop(st, link, now, "BATTERY_LOW")
            return out("BATTERY_LOW")
    else:
        st["batt_low_since_ms"] = None

    # (6) the heater ended by itself
    if st["pending"] is None and hs not in _HEATING:
        cmds = _end(st)
        return out("CONTINUOUS_ENDED" if st["expect"]["permanent"] else "RUN_ENDED")

    # (7) reference validity: the other valid thermometer, else min_level (DEGRADED)
    act = st["active"]
    if not _valid(sensors[act], cfg):
        if act == "ref" and _valid(inp["backup"], cfg):
            st["active"], st["mode"], st["last_ctl_ms"] = "backup", "AUTO", now
            if level is not None:
                st["ctl"] = track(st["ctl"], level, inp["backup"]["value"], target, cfg)
            return out("BACKUP_SELECTED")
        if act == "backup" and _valid(inp["ref"], cfg):
            st["active"], st["mode"], st["last_ctl_ms"] = "ref", "AUTO", now
            if level is not None:
                st["ctl"] = track(st["ctl"], level, inp["ref"]["value"], target, cfg)
            return out("REFERENCE_SELECTED")
        st["mode"] = "DEGRADED"
        if level != cfg["min_level"] and st["pending"] is None and link:
            cmds = [("level", cfg["min_level"])]
            st["pending"] = _pend("level", cfg["min_level"], now, level)
            st["expect"]["level"] = cfg["min_level"]
            st["ctl"]["last_change_ms"] = now
        return out("REFERENCE_LOST")
    t_ref = sensors[act]["value"]
    if st["mode"] == "DEGRADED":
        st["mode"], st["last_ctl_ms"] = "AUTO", now
        if level is not None:
            st["ctl"] = track(st["ctl"], level, t_ref, target, cfg)
        return out("AUTO_ON")

    # (8) control: once per period, frozen while a command is pending
    if st["pending"] is not None or now - st["last_ctl_ms"] < cfg["period_ms"]:
        return out(reason or "AUTO_ON")
    old_change = st["ctl"]["last_change_ms"]
    lvl, ctl, why = control(t_ref, t_out, target, now - st["last_ctl_ms"], st["ctl"], cfg, level, now)
    st["ctl"], st["last_ctl_ms"] = ctl, now
    if why in ("OVERHEAT", "TARGET_EXCEEDED_AT_MIN_LEVEL") and hs == "CONTINUOUS":
        lo = cfg["min_level"]  # the ESP cannot restart Dauerbetrieb: hold, never stop
        if level != lo and link:
            cmds = [("level", lo)]
            st["pending"] = _pend("level", lo, now, level)
            st["expect"]["level"] = lo
            ctl["last_change_ms"] = now
        return out("HOLD_AT_MIN_LEVEL")
    if why in ("OVERHEAT", "TARGET_EXCEEDED_AT_MIN_LEVEL"):
        cmds = _stop(st, link, now, why)
        return out(why)
    if lvl is not None and lvl != level and link:
        cmds = [("level", lvl)]
        st["pending"] = _pend("level", lvl, now, level)
        st["expect"]["level"] = lvl
        ctl["last_change_ms"] = now
        return out(why)
    ctl["last_change_ms"] = old_change  # nothing sent: the dwell does not restart
    return out("OUTSIDE_INACTIVE" if inp["outside_on"] and t_out is None else "HOLD")
