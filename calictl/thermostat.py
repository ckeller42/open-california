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
       reference error (centi-°C), an integrator frozen on long step gaps and by anti-windup, and
       an outside feed-forward capped at ``u_out_max``. It shall change the level only when the
       demand differs from the current level by at least ``500 + hyst`` and ``min_dwell_ms`` has
       passed. It shall report ``OVERHEAT`` above ``target + overheat`` and
       ``TARGET_EXCEEDED_AT_MIN_LEVEL`` after ``hot_hold_ms`` above ``target + band_hi`` at the
       minimum level. All arithmetic is integer; division uses :func:`_rdiv`.

    :param t_ref: reference temperature in centi-°C.
    :param t_out: outside temperature in centi-°C, or ``None`` (no feed-forward).
    :param target: target temperature in centi-°C.
    :param dt_ms: time since the previous step in ms.
    :param ctl: controller state ``{i, last_change_ms, hot_since_ms, u, u_out}``; not modified.
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
        0 if t_out is None else min(max(_rdiv(cfg["ka"] * max(0, target - t_out), 100), 0), cfg["u_out_max"])
    )
    i = ctl["i"]
    u_old = p + i + u_out
    frozen = dt_ms > 3 * cfg["period_ms"] or (u_old < 1000 and e < 0) or (u_old > 10000 and e > 0)
    if not frozen:
        i += _rdiv(cfg["ki"] * e * dt_ms, 100 * 60000)
    u = p + i + u_out
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
    return {**ctl, "i": cur_level * 1000 - p - ctl["u_out"]}
