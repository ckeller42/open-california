"""Autonomous control rules for the daemon (pure decision logic, stdlib-only, no BLE/serve deps).

Currently one rule: **auto camper mode — restore camping after you park.**

The unit drops camping mode the moment the engine starts (ignition/terminal-15 on) and **refuses to
let it back on while the vehicle is driven** — a firmware *stationary gate*, live-verified
2026-08-19 (`docs/business-logic/control-and-actuation.md` §4). So we cannot keep camping on through
a drive; the only moment the unit accepts camping-on is once you are **stationary again**. This rule
therefore:

1. **Remembers** the camping config that was on *before* a drive (armed when the engine sheds it);
2. on the ignition **falling** edge (you parked) **restores** it — master + rear USB + lights;
3. while (a) respecting a manual off, (b) never fighting the unit's battery protection, and
   (c) staying bounded (a short retry window in case the unit lingers in the driving-lock just after
   parking, then it gives up).

Kept pure so every branch is unit-tested without a live vehicle; :class:`serve.Server` owns the
persistent state and does the actuation. NB the ``enable`` bit on the camping state char mirrors
ignition (it reads 1 while driving = blocked), so ignition-off IS the "stationary again" signal.
"""
from __future__ import annotations

# Defaults (all env-tunable in serve). window_s: how long AFTER PARKING to keep trying the restore
# (covers a brief post-park driving-lock); max_fails: attempts before concluding the unit won't take
# it and giving up; min_soc: below this leisure-battery %, assume the unit is protecting the battery
# and DO NOT restore (would fight its power-saving).
AUTO_CAMPER_WINDOW_S = 120.0
AUTO_CAMPER_MAX_FAILS = 3
AUTO_CAMPER_MIN_SOC = 20


def auto_camper_restore_decide(*, ignition_on, prev_ignition, master_on, prev_master,
                               usb_on, lights_on, prev_usb, prev_lights, soc, warning, now,
                               owe_restore, pre_drive, restore_until, fails,
                               min_soc=AUTO_CAMPER_MIN_SOC, max_fails=AUTO_CAMPER_MAX_FAILS,
                               window_s=AUTO_CAMPER_WINDOW_S):
    """Decide what the restore-after-park rule does this poll. PURE — returns new state + any action.

    State machine (see module docstring):

    * **arm** — on an ignition **rising** edge, if camping was on just before (``prev_master``),
      remember the pre-drive config (``pre_drive``) and set ``owe_restore`` — the engine is about to
      shed it and we owe a restore.
    * **manual cancel** — if camping goes on→off while the ignition is steady off (a manual off,
      not an engine shed), clear the debt: the user wants it off.
    * **restore** — on the ignition **falling** edge (parked) with a debt owed, open a retry window
      and start re-asserting the remembered config each poll.
    * **succeed / stand down / give up** — disarm the instant camping reads on again; stand down on
      low battery / warning (never fight power-saving); give up after ``max_fails`` or when the
      window elapses.

    :param ignition_on / prev_ignition: this/last poll's ignition (terminal-15), bool/None.
    :param master_on / prev_master: this/last poll's camping master, bool/None.
    :param usb_on / lights_on / prev_usb / prev_lights: camping USB + lights this/last poll (the
        ``prev_*`` are captured as the pre-drive config when arming).
    :param soc: leisure-battery percent (``soc2_pct``) or None.
    :param warning: True if any battery warning is set.
    :param now: seconds on a consistent clock. serve passes **wall-clock** (``time.time()``) so the
        ``restore_until`` deadline survives a daemon restart / buspi reboot mid-cycle (the restore
        debt is persisted). The function only ever compares ``now`` against ``restore_until``, so any
        consistent clock works — but persistence requires wall-clock.
    :param owe_restore: do we currently owe a restore (armed)?
    :param pre_drive: remembered ``{"master","usb","lights"}`` to restore, or None.
    :param restore_until: retry-window deadline (monotonic) while restoring, or None.
    :param fails: restore attempts this cycle.
    :returns: dict ``{actuate, restore_config, owe_restore, pre_drive, restore_until, fails,
        notice, restored, prev_ignition, prev_master, prev_usb, prev_lights}`` — ``actuate`` True
        means write ``restore_config`` (master + optional usb/lights); ``restored`` True on a
        confirmed success; ``notice`` is a one-line message to log + toast (else None).
    """
    ign, pign = bool(ignition_on), bool(prev_ignition)
    m, pm = bool(master_on), bool(prev_master)
    actuate = False
    restore_config = None
    notice = None
    restored = False

    # ARM: engine just started (ignition rising) and camping was on before -> we owe a restore of the
    # pre-drive config. The shed itself may land a poll or two later (BLE can drop at the crank); the
    # prev_* snapshot from before the edge is the pre-drive truth. `not owe_restore` guards an
    # ignition bounce at the crank (0->1->0->1) from re-capturing pre_drive off an already-shed read.
    if ign and not pign and pm and not owe_restore:
        owe_restore = True
        pre_drive = {"master": True, "usb": bool(prev_usb), "lights": bool(prev_lights)}
        restore_until = None
        fails = 0

    # MANUAL CANCEL: camping turned off while stationary (ignition steady off) = a manual off, not an
    # engine shed. Respect it — drop any debt so we don't re-enable what the user just switched off.
    if not ign and not pign and pm and not m:
        owe_restore, restore_until, fails = False, None, 0

    # PARK: ignition falling edge with a debt owed -> open the retry window and start restoring.
    if not ign and pign and owe_restore:
        restore_until = now + window_s
        fails = 0

    # RESTORING: parked, within the window, still owe it.
    if owe_restore and restore_until is not None and not ign:
        if now > restore_until:                     # window elapsed without success
            owe_restore, restore_until = False, None
            notice = "Auto camper gave up: camping did not come back within the window after park."
        elif m:                                     # camping is back on -> success
            owe_restore, restore_until, fails, restored = False, None, 0, True
            notice = "Auto camper: camping restored after park."
        elif (soc is not None and soc < min_soc) or warning:
            # GUARD 1: low battery / warning -> the unit is protecting itself; don't restore.
            owe_restore, restore_until = False, None
            notice = ("Auto camper stood down: battery low / warning"
                      + (" (SoC %d%%)" % soc if soc is not None else "") + " — not restoring.")
        elif fails >= max_fails:
            # GUARD 2: the unit keeps refusing after park (lingering lock / low-power) -> give up.
            owe_restore, restore_until = False, None
            notice = "Auto camper gave up: camping would not re-enable after park."
        else:
            actuate = True                          # healthy + still off -> (re)assert the restore
            restore_config = pre_drive
            fails += 1

    return {"actuate": actuate, "restore_config": restore_config, "owe_restore": owe_restore,
            "pre_drive": pre_drive, "restore_until": restore_until, "fails": fails,
            "notice": notice, "restored": restored, "prev_ignition": ign, "prev_master": m,
            "prev_usb": bool(usb_on), "prev_lights": bool(lights_on)}
