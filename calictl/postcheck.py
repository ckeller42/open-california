"""Post-write applied-check: did a control write land in the resulting state?

Extracted from ``cli`` so the daemon (``serve``) no longer imports up into the CLI for it — both
now depend on this lower-level module. ``set_check`` reads the field a given ``set`` targeted out of
the interpreted (or, for cooler numerics, raw-decoded) state and reports ``(label, got, want)``;
``got == want`` means the write applied. Stdlib-only at import (``control``/``semantics`` are lazy).

.. req:: One post-write applied-check shared by CLI and daemon
   :id: R_POST_WRITE_CHECK
   :status: implemented
   :tags: control, verification

   The applied-check that maps ``(function, what, value)`` to the resulting-state field shall live in
   one module both the CLI and the daemon depend on — no daemon→CLI import.
"""
from __future__ import annotations


def set_check(function, what, value, interp, decoded):
    """Post-write success check: ``(label, got, want)``. ``got == want`` -> OK. Reads the targeted
    field from the interpreted state (or the raw decode for cooler numerics)."""
    from . import control  # lazy (stdlib-only import rule); needed for the lighting rows
    on = str(value).strip().lower() in ("on", "true", "1")
    if function == "lighting" and what != "power":
        return _lighting_check(what, value, interp)
    table = {
        ("cooler", "power"):        lambda: ("State", decoded.get("State"), 1 if on else 0),
        ("cooler", "level"):        lambda: ("Level", decoded.get("Level"), int(value)),
        ("campingmode", "master"):  lambda: ("master_on", interp.get("master_on"), on),
        ("campingmode", "usb"):     lambda: ("usb_charger", interp.get("usb_charger"), on),
        ("campingmode", "lights"):  lambda: ("lights_on", interp.get("lights_on"), on),
        ("lighting", "power"):      lambda: ("profile", interp.get("profile"),
                                             control.LIGHT_PROFILE_ALL_ON if on else control.LIGHT_PROFILE_ALL_OFF),
        ("airheater", "power"):     lambda: ("running", interp.get("running"), on),
        ("airheater", "level"):     lambda: ("level", interp.get("level"), int(value)),
        # UNVERIFIED targets (not installed on this van) — interp keys per semantics.py.
        ("roofaircondition", "power"):       lambda: ("on", interp.get("on"), on),
        ("roofaircondition", "fanspeed"):    lambda: ("fan_speed", interp.get("fan_speed"), int(value)),
        ("roofaircondition", "mode"):        lambda: ("mode", interp.get("mode"), int(value)),
        ("roofaircondition", "temperature"): lambda: ("target_temp", interp.get("target_temp"), int(value)),
        ("stairs", "move"):     lambda: ("extended", interp.get("extended"),
                                         {"extend": True, "retract": False}.get(str(value).strip().lower())),
        ("stairs", "mode"):     lambda: ("mode", interp.get("mode"), int(value)),
        ("livingroomheater", "air"):         lambda: ("air_on", interp.get("air_on"), on),
        ("livingroomheater", "water"):       lambda: ("water_on", interp.get("water_on"), on),
        ("livingroomheater", "temperature"): lambda: ("air_temp", interp.get("air_temp"), int(value)),
    }
    fn = table.get((function, what))
    return fn() if fn else (what, None, None)


def _max_zone(interp):
    # only the REAL lamps (L1-L8) — L9-L16 carry the unchanged sentinel 14 that _all_real_zones
    # leaves in place, which would otherwise masquerade as the peak brightness
    from .semantics import _REAL_LIGHT_ZONES
    return max((interp.get("brightness_zone_%d" % z) or 0 for z in _REAL_LIGHT_ZONES), default=0)


# friendly lighting-zone key -> the interpreted brightness_zone_<N> it maps to (see
# control.LIGHT_ZONES / semantics._LZONES). Used only for the applied-check.
_LIGHT_ZONE_NUM = {"reading-1": 2, "reading-2": 1, "reading-3": 4,
                   "kitchen": 7, "roof-ambient": 8, "outside-rear": 3,
                   "kitchen-ambient": 5, "roof-reading": 6}   # L5/L6 inferred (unverified)


def _lighting_check(what, value, interp):
    """Applied-check for the per-zone lighting grammar (zone / all / profile)."""
    if what == "profile":
        return ("profile", interp.get("profile"), int(value))
    znum = _LIGHT_ZONE_NUM.get(what)
    if znum is not None:
        return ("zone_%d" % znum, interp.get("brightness_zone_%d" % znum), int(value))
    return ("max_zone", _max_zone(interp), int(value))   # "all" or a raw field: check the peak
