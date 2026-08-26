"""Passive camping/ignition OBSERVER — evidence-gathering that NEVER actuates.

Extracted from :class:`serve.Server` (which held ~7 fields + 3 methods for it) so the observer is a
self-contained, unit-testable unit. It logs every change in ignition + camping substates to the
journal, starts a fast-poll BURST after an engine start so the shed is captured at fine resolution
(the 30 s cadence otherwise aliases engine-on and the shed into one sample), and — when the
persistent BLE session is up — logs the unit's 1202/1004 PUSH notifications at full resolution.

The daemon owns the poll loop + BLE; it calls :meth:`observe` each poll, passes :meth:`on_push` to
the persistent session, and asks :meth:`poll_interval` how long to nap (the burst logic).

.. req:: Passive camping/ignition observer with an engine-start fast-poll burst
   :id: R_CAMPING_OBSERVER
   :status: implemented
   :tags: camping, observability

   The daemon shall passively record ignition + camping-substate transitions (never actuating) and,
   on an engine-start edge of EITHER Terminal-15 or DC-DC charging, fast-poll for a bounded window so
   the camping shed is captured finely enough to tell a clean shed from a flip-flop.
"""
from __future__ import annotations

import os
import time

from . import protocol, semantics

# camping/ignition fields watched on a push, per function (mirrors what `observe` tracks).
# cooler: probes whether the unit BROADCASTS night-timer/quiet-time changes on 1102 (issue #99)
# — a push here the moment the app writes a quiet time is the wire proof.
_PUSH_FIELDS = {"campingmode": ("master_on", "usb_charger", "lights_on", "enable"),
                "vehicle": ("ignition_on",),
                "cooler": ("on", "level", "quiet_scheduled", "quiet_from", "quiet_to",
                           "timer_active")}


def _fmt(v):
    """0/1 for a bool, '-' for missing, else the raw value — compact for one journal line."""
    if v is None:
        return "-"
    if isinstance(v, bool):
        return "1" if v else "0"
    return str(v)


class CampingObserver:
    """Holds the observer's own state (previous samples + the burst deadline) and does the logging.

    :param funcs: the loaded function table (name -> Function); used to reverse-map a pushed char
        UUID back to its function and to decode pushed frames.
    """

    def __init__(self, funcs, *, burst_interval=None, burst_window_s=None):
        self._funcs = funcs
        self._push_char_to_fn = {str(f.state_char).lower(): name
                                 for name, f in funcs.items() if f.state_char}
        self._obs_prev = None            # last observed {ignition, dcdc_charging, master_on, ...}
        self._obs_ign_edge = None        # wall time of the last engine-start edge (for Δ-since)
        self._push_prev = {}             # last pushed value per tracked field (change detection)
        self._burst_until = None         # monotonic deadline while fast-polling, else None
        self._burst_interval = (burst_interval if burst_interval is not None
                                else float(os.environ.get("CALICTL_OBSERVE_BURST_INTERVAL_S", "3")))
        self._burst_window_s = (burst_window_s if burst_window_s is not None
                                else float(os.environ.get("CALICTL_OBSERVE_BURST_S", "180")))

    def observe(self, states):
        """Log every change in ignition + camping substates, and arm the fast-poll burst on an
        engine-start edge. Runs every poll regardless of the auto-camper toggle. NEVER actuates.

        Two "engine on" signals are tracked, because they differ: ``ignition`` is Terminal-15 (can be
        on with the engine NOT running) while ``dcdc_charging`` is the DC-DC converter active =
        alternator = engine RUNNING. The burst triggers on a rising edge of EITHER.
        """
        veh = states.get("vehicle") or {}
        camp = states.get("campingmode") or {}
        e = states.get("energy") or {}
        cur = {"ignition": veh.get("ignition_on"),          # Terminal-15 (ignition switch)
               "dcdc_charging": e.get("dcdc_charging"),      # DC-DC active = alternator = engine RUNNING
               "master_on": camp.get("master_on"),
               "usb_charger": camp.get("usb_charger"),
               "lights_on": camp.get("lights_on"),
               "enable": camp.get("enable")}                 # camp.enable = terminal-15 as camping sees it
        now = time.time()
        prev, self._obs_prev = self._obs_prev, cur
        if prev is None:
            return                                # first poll: baseline only, nothing to compare
        engine_rise = ((bool(cur["ignition"]) and not bool(prev["ignition"]))
                       or (bool(cur["dcdc_charging"]) and not bool(prev["dcdc_charging"])))
        if engine_rise:                            # engine start on EITHER signal -> stamp + burst
            self._obs_ign_edge = now
            self._burst_until = time.monotonic() + self._burst_window_s
        changed = [k for k in cur if cur[k] != prev[k]]
        if not changed:
            return
        dt = "%.0f" % (now - self._obs_ign_edge) if self._obs_ign_edge else "-"
        parts = ", ".join("%s %s->%s" % (k, _fmt(prev[k]), _fmt(cur[k])) for k in changed)
        print("camping-watch: %s (ign=%s dcdc=%s dcdc_A=%s batt2_A=%s dt_eng=%ss soc=%s)"
              % (parts, _fmt(cur["ignition"]), _fmt(cur["dcdc_charging"]),
                 _fmt(e.get("dcdc_current")), _fmt(e.get("batt2_current")),
                 dt, _fmt(e.get("soc2_pct"))), flush=True)

    def on_push(self, uuid, data):
        """Fired IN THE BLE LOOP the instant a status char pushes a notification (persistent session
        only). Logs camping/ignition changes at full resolution — confirms the unit's push channel
        and catches toggles between polls. Passive, sync, best-effort: never actuates, never raises
        into bleak (the caller swallows exceptions). Tagged ``camping-push`` to distinguish it from
        the poll observer's ``camping-watch``."""
        fn = self._push_char_to_fn.get(uuid)
        fields = _PUSH_FIELDS.get(fn)
        if not fields:
            return                            # not a char we watch (only campingmode / vehicle)
        interp = semantics.interpret(fn, protocol.decode(self._funcs[fn], data))
        cur = {k: interp.get(k) for k in fields}
        changed = {k: (self._push_prev[k], v) for k, v in cur.items()
                   if k in self._push_prev and self._push_prev[k] != v}
        self._push_prev.update(cur)
        if changed:
            parts = ", ".join("%s %s->%s" % (k, _fmt(o), _fmt(n)) for k, (o, n) in changed.items())
            print("camping-push[%s]: %s" % (fn, parts), flush=True)

    def poll_interval(self, default):
        """The nap the poll loop should take: the short burst interval while a burst is active (set
        by an engine-start edge in :meth:`observe`), else ``default``. Clears the burst when its
        window elapses. Encapsulates the fast-poll-burst cadence so the poll loop just asks."""
        if self._burst_until is None:
            return default
        if time.monotonic() < self._burst_until:
            return min(default, self._burst_interval)
        self._burst_until = None              # window elapsed -> back to the normal cadence
        return default

    @property
    def bursting(self):
        """True while a fast-poll burst window is active (for tests / introspection)."""
        return self._burst_until is not None
