"""Firmware/protocol version tracking + drift capture.

The camper unit's protocol drifts across firmware revisions (the `0409 → 0410` bump changed the
DC-DC current scaling — see :func:`semantics.apply_sw_corrections`). The one piece of evidence you
can never recover is the **raw BLE frames from the old firmware at the moment it changes**: the van
deep-sleeps and, once it updates, the old wire data is gone. So when the version changes we dump a
full raw-frame snapshot — that is what makes a *future* correction derivable at all.

.. req:: Capture raw frames when the firmware/protocol version changes
   :id: R_FIRMWARE_DRIFT_CAPTURE
   :status: implemented
   :tags: firmware, diagnostics

   When ``general``'s ``amb_sw_version`` or ``comm_version`` changes between polls, the daemon shall
   log the change and persist a raw-frame + decoded snapshot, so a correction for the new firmware
   can be derived from the wire evidence.

Runtime is stdlib-only at import (``json``/``time`` are stdlib); no bleak/yaml here.
"""
from __future__ import annotations

import json
import os
import time


def current(states: dict) -> tuple:
    """The live firmware/protocol identity as ``(amb_sw_version, comm_version)`` from the
    interpreted ``general`` state, or ``(None, None)`` if general wasn't read this poll."""
    g = states.get("general") or {}
    return (g.get("amb_sw_version"), g.get("comm_version"))


def changed(prev, cur) -> bool:
    """True only when we have a KNOWN previous identity that differs from a KNOWN current one — a
    real firmware/protocol change, not the first read (prev None) or a partial read (cur has None)."""
    if prev is None or cur is None:
        return False
    if cur[0] is None and cur[1] is None:
        return False
    # compare only the components we actually know now (a partial read shouldn't fake a change)
    for i in (0, 1):
        if cur[i] is not None and prev[i] is not None and cur[i] != prev[i]:
            return True
    return False


def write_snapshot(states: dict, raw: dict, out_dir: str, *, reason: str = "drift",
                   now: float | None = None) -> str:
    """Persist the irreplaceable evidence for the CURRENT firmware: the raw per-char bytes (hex) +
    the decoded/interpreted state + the version. Returns the file path.

    :param states: interpreted per-function state (for quick eyeballing).
    :param raw: ``{function: bytes}`` — the raw BLE frames this poll (the evidence that lets a later
        diff spot a moved offset / changed enum / flipped scale).
    :param out_dir: directory to write into (created if missing).
    :param reason: ``"baseline"`` (first firmware ever seen — the OLD-side of a future diff) or
        ``"drift"`` (the version just changed). A diff needs BOTH, hence the baseline matters.
    """
    t = time.time() if now is None else now
    g = states.get("general") or {}
    amb = g.get("amb_sw_version") or "unknown"
    blob = {
        "ts": round(t, 1),
        "reason": reason,
        "amb_sw_version": g.get("amb_sw_version"),
        "cm_sw_version": g.get("cm_sw_version"),
        "comm_version": g.get("comm_version"),
        "raw_frames_hex": {fn: bytes(data).hex() for fn, data in raw.items()},
        "interpreted": states,
    }
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "fw-%s-%s-%d.json" % (reason, amb, int(t)))
    with open(path, "w") as f:
        json.dump(blob, f, indent=1, default=str)
    return path
