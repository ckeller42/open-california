"""Autonomous control rules for the daemon (pure decision logic, stdlib-only, no BLE/serve deps).

Currently one rule: **auto camper mode** — re-enable camper mode + rear USB after the ENGINE
disables it (the unit drops camper mode when ignition turns on), while (a) respecting a manual off
and (b) NEVER fighting the unit's battery protection. Kept pure so every branch is unit-tested
without a live vehicle; :class:`serve.Server` owns the persistent state and does the actuation.
"""
from __future__ import annotations

# Defaults (all env-tunable in serve). window_s: how long after an engine-start to keep trying;
# max_fails: how many re-asserts before we conclude the unit keeps dropping it and stand down;
# min_soc: below this leisure-battery %, we assume the unit shed camper mode to protect the battery
# and we DO NOT fight it.
AUTO_CAMPER_WINDOW_S = 120.0
AUTO_CAMPER_MAX_FAILS = 3
AUTO_CAMPER_MIN_SOC = 20


def auto_camper_decide(*, ignition_on, prev_ignition, camping_on, soc, warning, now,
                       armed_until, fails, min_soc=AUTO_CAMPER_MIN_SOC,
                       max_fails=AUTO_CAMPER_MAX_FAILS, window_s=AUTO_CAMPER_WINDOW_S):
    """Decide what the auto-camper rule does this poll. PURE — returns the new state + any action.

    The rule arms only on an ignition rising edge (engine just started = the thing that disables
    camper mode). While armed and camper mode reads off, it re-asserts — UNLESS the battery is low /
    warning (defer to the unit's load-shed) or it has already failed ``max_fails`` times (the unit
    keeps dropping it = a low-power state we can't cleanly see -> stand down). It disarms the instant
    camper mode is confirmed on. A camper-off with no recent ignition edge (a manual off) is never
    touched, because we're not armed.

    :param ignition_on: this poll's ignition (terminal-15) state (bool/None).
    :param prev_ignition: last poll's ignition state (bool/None).
    :param camping_on: this poll's camper-mode master state (bool/None).
    :param soc: leisure-battery percent (``soc2_pct``), or None if unknown.
    :param warning: True if any battery warning is set (SleepWarning/WarningLevelActive/…).
    :param now: monotonic seconds.
    :param armed_until: current arm deadline (monotonic) or None.
    :param fails: consecutive re-assert attempts this arm cycle.
    :returns: dict ``{actuate, armed_until, fails, notice, prev_ignition, stood_down}`` —
        ``actuate`` True means the caller should turn camper master + USB on; ``notice`` is a
        one-line message to log + toast when the rule gives up/stands down (else None).
    """
    rising = bool(ignition_on) and not bool(prev_ignition)
    if rising:                                  # engine just started -> arm the re-assert
        armed_until = now + window_s
        fails = 0

    actuate = False
    notice = None
    stood_down = False

    if armed_until is not None and now < armed_until:
        if camping_on:                          # success — it's on, stop trying
            armed_until, fails = None, 0
        elif (soc is not None and soc < min_soc) or warning:
            # GUARD 1: the unit shed camper mode to protect the battery — honor it, don't fight it.
            armed_until, stood_down = None, True
            notice = ("Auto camper stood down: battery low / warning"
                      + (" (SoC %d%%)" % soc if soc is not None else "") + " — not re-enabling.")
        elif fails >= max_fails:
            # GUARD 2: it keeps dropping despite a healthy-looking battery — a low-power state we
            # can't see cleanly. Give up for this ignition cycle instead of looping.
            armed_until, stood_down = None, True
            notice = "Auto camper gave up: camper mode keeps dropping (likely low-power) — standing down."
        else:
            actuate = True                      # healthy + still off -> re-assert
            fails += 1
    elif armed_until is not None:               # window elapsed without ever confirming on
        armed_until, stood_down = None, True
        notice = "Auto camper gave up: camper mode did not stay on within the retry window."

    return {"actuate": actuate, "armed_until": armed_until, "fails": fails,
            "notice": notice, "prev_ignition": bool(ignition_on), "stood_down": stood_down}
