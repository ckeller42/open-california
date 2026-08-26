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

import os
import time

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

    .. req:: Restore camping mode after parking, bounded and battery-safe
       :id: R_AUTO_CAMPER_RESTORE
       :status: implemented
       :tags: automation, camping, control

       The unit refuses camping-on while driving, so the daemon shall remember the pre-drive
       camping config when the engine sheds it and re-assert it on the ignition falling edge
       (parked), within a bounded retry window, standing down on low battery / warning and never
       looping — so a manual off is respected and the unit's power-saving is never fought.
    """
    # A read gap (vehicle or campingmode missing this poll) carries NO information — FREEZE the
    # machine rather than bool()-coercing None to False, which fabricates edges. Live 2026-08-26:
    # `ignition 1->-` fired a phantom park-restore (mid-drive it would burn the give-up budget),
    # and the `None->1` re-arm can capture a shed pre_drive / silently drop the debt; a None
    # master similarly faked a "manual cancel". Nothing can be actuated during a gap anyway;
    # prevs stay at the last KNOWN values and the window doesn't tick.
    if ignition_on is None or master_on is None:
        return {"actuate": False, "restore_config": None, "owe_restore": owe_restore,
                "pre_drive": pre_drive, "restore_until": restore_until, "fails": fails,
                "notice": None, "restored": False, "prev_ignition": prev_ignition,
                "prev_master": prev_master, "prev_usb": prev_usb, "prev_lights": prev_lights}

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


class AutoCamper:
    """Stateful restore-after-park CONTROLLER — owns the auto-camper toggle + the per-cycle restore
    state, drives the pure :func:`auto_camper_restore_decide`, does the journal logging, and actuates
    via an INJECTED coroutine (so it never touches BLE itself). Extracted from :class:`serve.Server`,
    which previously held ~13 ``_ac_*`` fields + the step + persistence inline.

    :param min_soc / max_fails / window_s: guard params; default from the ``CALICTL_AUTO_CAMPER_*``
        env vars (falling back to the module constants).

    .. req:: Restore-after-park is a self-contained, injectable controller
       :id: R_AUTO_CAMPER_CONTROLLER
       :status: implemented
       :tags: automation, camping

       The restore-after-park state + step + persistence shall live in one unit that actuates through
       an injected coroutine (never opening BLE), so it is unit-testable without the daemon.
    """

    def __init__(self, *, min_soc=None, max_fails=None, window_s=None):
        self.enabled = False
        self.prev_ignition = None
        self.prev_master = None
        self.prev_usb = None
        self.prev_lights = None
        self.owe_restore = False
        self.pre_drive = None            # remembered {master,usb,lights} to restore after park
        self.restore_until = None        # WALL-CLOCK deadline while restoring (persisted), else None
        self.fails = 0
        self.notice = None               # {ts, msg} — one-time log + web toast
        self.min_soc = int(min_soc if min_soc is not None
                           else os.environ.get("CALICTL_AUTO_CAMPER_MIN_SOC", AUTO_CAMPER_MIN_SOC))
        self.max_fails = int(max_fails if max_fails is not None
                             else os.environ.get("CALICTL_AUTO_CAMPER_MAX_FAILS", AUTO_CAMPER_MAX_FAILS))
        self.window_s = float(window_s if window_s is not None
                              else os.environ.get("CALICTL_AUTO_CAMPER_WINDOW_S", AUTO_CAMPER_WINDOW_S))

    def set_enabled(self, on):
        """Toggle the feature (returns the new state). Disabling clears any owed restore. Persistence
        + the ENABLED/disabled log line are the caller's job (serve)."""
        self.enabled = bool(on)
        if not self.enabled:
            self.owe_restore, self.restore_until, self.fails = False, None, 0
        return self.enabled

    def _track_prev(self, ign, master, usb, lights):
        """Roll the previous-state snapshot (edge detection for the next poll). A ``None`` read
        (missed function this poll) keeps the LAST-KNOWN value — otherwise enabling the feature
        right after a read gap would hand ``decide()`` a None prev and fabricate an edge."""
        if ign is not None:
            self.prev_ignition = ign
        if master is not None:
            self.prev_master = master
        if usb is not None:
            self.prev_usb = usb
        if lights is not None:
            self.prev_lights = lights

    async def step(self, states, *, actuate, read_only, now=None):
        """One poll's restore-after-park decision + logging + actuation. Runs AFTER the read (the BLE
        lock is free). ``actuate(function, what, value)`` is an injected coroutine (serve passes
        ``on_command``) — this NEVER touches BLE directly. ``now`` defaults to wall-clock
        ``time.time()`` (the deadline must survive a restart). Never raises out of the poll."""
        veh = states.get("vehicle") or {}
        camp = states.get("campingmode") or {}
        e = states.get("energy") or {}
        ign = veh.get("ignition_on")
        master = camp.get("master_on")
        usb = camp.get("usb_charger")
        lights = camp.get("lights_on")
        if not self.enabled:
            self._track_prev(ign, master, usb, lights)   # keep edges tracked so a mid-cycle enable won't misfire
            return
        soc = e.get("soc2_pct")
        warning = bool(e.get("sleep_warning") or e.get("warning_active")
                       or (e.get("warning_level") or 0) >= 1)
        d = auto_camper_restore_decide(
            ignition_on=ign, prev_ignition=self.prev_ignition,
            master_on=master, prev_master=self.prev_master,
            usb_on=usb, lights_on=lights, prev_usb=self.prev_usb, prev_lights=self.prev_lights,
            soc=soc, warning=warning, now=now if now is not None else time.time(),
            owe_restore=self.owe_restore, pre_drive=self.pre_drive,
            restore_until=self.restore_until, fails=self.fails,
            min_soc=self.min_soc, max_fails=self.max_fails, window_s=self.window_s)
        armed_before = self.owe_restore
        self.owe_restore = d["owe_restore"]
        self.pre_drive = d["pre_drive"]
        self.restore_until = d["restore_until"]
        self.fails = d["fails"]
        self._track_prev(d["prev_ignition"], d["prev_master"], d["prev_usb"], d["prev_lights"])

        # Classify the event for the journal line. "idle" = nothing notable -> silent.
        if d["owe_restore"] and not armed_before:
            event = "armed_engine_shed"
        elif d["restored"]:
            event = "restored"
        elif d["actuate"]:
            event = "restoring"
        elif d["notice"]:
            event = "stood_down" if "battery" in d["notice"] else "gave_up"
        else:
            event = "idle"
        if event != "idle":
            print("auto-camper[%s]: %s (ignition=%s camping=%s soc=%s warn=%s fails=%d owe=%s)"
                  % (event, d["notice"] or event.replace("_", " "), ign, master, soc, warning,
                     self.fails, d["owe_restore"]), flush=True)
        if d["notice"]:
            self.notice = {"ts": round(time.time(), 1), "msg": d["notice"]}

        if d["actuate"]:
            cfg = d["restore_config"] or {}
            if read_only:
                print("auto-camper: parked with a restore owed but writes are read-only; not restoring",
                      flush=True)
                self.owe_restore, self.restore_until = False, None   # can't act -> don't spin
                return
            try:
                await actuate("campingmode", "master", "on")        # master first (gates USB+lights)
                if cfg.get("usb"):
                    await actuate("campingmode", "usb", "on")
                if cfg.get("lights"):
                    await actuate("campingmode", "lights", "on")
            except Exception as ex:           # BLE hiccup etc. -> counts as a failed attempt via fails
                print("auto-camper: restore actuation failed: %r" % ex, flush=True)

    def snapshot(self):
        """The ``_meta.auto_camper`` block for the web UI: toggle, whether a restore is owed, notice."""
        return {"enabled": self.enabled, "armed": bool(self.owe_restore), "notice": self.notice}

    def to_state_dict(self):
        """The persisted per-cycle restore debt (survives a restart mid-cycle)."""
        return {"owe_restore": self.owe_restore, "pre_drive": self.pre_drive,
                "restore_until": self.restore_until, "fails": self.fails,
                "prev_ignition": self.prev_ignition, "prev_master": self.prev_master,
                "prev_usb": self.prev_usb, "prev_lights": self.prev_lights}

    def load(self, enabled, state):
        """Restore the toggle + per-cycle debt from a persisted blob (best-effort; missing/null ->
        defaults, so a corrupt cache never crashes). ``fails`` uses ``or 0`` to survive a null value."""
        self.enabled = bool(enabled)
        ac = state or {}
        self.owe_restore = bool(ac.get("owe_restore", False))
        self.pre_drive = ac.get("pre_drive")
        self.restore_until = ac.get("restore_until")
        self.fails = int(ac.get("fails") or 0)
        self.prev_ignition = ac.get("prev_ignition")
        self.prev_master = ac.get("prev_master")
        self.prev_usb = ac.get("prev_usb")
        self.prev_lights = ac.get("prev_lights")
