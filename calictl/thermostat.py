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
    :param age_ms: age of that read in ms; ``None`` skips the staleness check.
    :param cfg: settings dict; ``heater_stale_ms`` is the staleness limit.
    :returns: state from :data:`HEATER_STATES`; the level 1..10 or ``None`` (0, 11..15 and a
        missing value are no level); the ``ErrorCode`` (-1 when not installed, 0 when none).
    """
    if not d or (age_ms is not None and age_ms > cfg["heater_stale_ms"]):
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
