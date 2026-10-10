"""Physical-plausibility guards for latched/stale sensor reads (stdlib-only, pure).

The camper unit only *measures* fresh water while its water system is powered (unlocking the
van turns it on). Parked and locked, it stops measuring and hands out a **stale latched value**
(observed: a true 17-19 L tank read back as 1 L). buspi faithfully mirrors that, so the UI would
show a confident-but-wrong low level and a nonsense "0 days left" forecast.

We can't read a "water system powered" bit (none exists in the decoded protocol), but the unit
freezes BOTH tanks when it stops measuring, so the GREY tank is the tell: while parked the unit
measures neither, so grey is frozen and fresh decays alone toward the ~1 L latch; while powered it
measures both, so grey moves whenever fresh does. Ground truth 2026-07-14: powered => fresh 19->17
AND grey 0->1 together; parked => both frozen, fresh latched at 1.

So the guard fires on the latch SIGNATURE — a fresh drop TO the latch value (<= 1 L) while grey is
frozen — and keeps showing the last plausible reading (flagged stale). Any other reading is a live
measurement: a drop that stops above the latch value, or any grey movement. Grey alone is not
enough: on this van grey reads 0 on EVERY frame, so "any drop with grey frozen" held every real
drop (2026-10-10: buspi served 22 L for weeks while the unit and the app reported 20 L). See
``docs/business-logic/value-freshness.md``.

2026-10-10 (calictl BLE trace of the real unit): FreshWaterLevel 1 is what the unit reports whenever
it is NOT measuring, and when a measurement starts it RAMPS the level 1 -> 2 -> ... -> real value in
~4 s (a step every 0.1-0.5 s), holds the real value ~1-2 min, then drops back to 1. A read landing
mid-ramp (buspi read 16 L on the way to 20 L) is not a measurement either, so ``settle_water``
adopts a NEW level only once it has stayed unchanged for ``WATER_SETTLE_S``.
"""

from __future__ import annotations

# The parked unit's stale fresh-water latch value, in liters: observed as FreshWaterLevel=1 on this
# van (2026-07-14 and since). Only a drop to <= this is a latch candidate.
WATER_LATCH_MAX_L = 1

# A new level must stay unchanged this long before it is adopted (seconds). The measurement ramp
# steps every <= 0.5 s; the real value then holds ~1-2 min (BLE trace 2026-10-09/10).
WATER_SETTLE_S = 5.0


def _liters(water: dict, tank: str):
    """Fresh/waste tank level in liters, or None if absent."""
    t = (water or {}).get(tank)
    return t.get("liters") if isinstance(t, dict) else None


def implausible_water_drop(new: dict, prev: dict) -> bool:
    """True when ``new``'s fresh-water drop looks like the parked latch, not real usage.

    The latch is a fresh DROP to <= ``WATER_LATCH_MAX_L`` (the observed 1 L latch) while the GREY
    tank is EXACTLY frozen (the unit freezes both tanks when unpowered). A drop that stays above
    the latch value is a live measurement, as is any grey movement — rise (usage) or fall (a
    dump-station drain). A non-drop (refill / same / re-measure) is always plausible. Missing fresh
    returns False (can't judge); a drop to the latch value we can't corroborate with grey is
    treated as the latch (conservative).

    :param new: freshly-interpreted water dict (``{"fresh":{"liters":..}, "waste":{"liters":..}}``).
    :param prev: the last plausible water dict to compare against.
    :returns: True if ``new`` is the stale latch vs ``prev``.

    .. req:: Reject the parked stale-latch fresh-water reading
       :id: R_WATER_STALE_GUARD
       :status: implemented
       :tags: water, freshness, ui

       The daemon shall treat a fresh-water drop to at most ``WATER_LATCH_MAX_L`` (1 L, the
       observed latch value) while the grey tank is EXACTLY frozen (or unknown) as the stale latch
       and keep displaying the last plausible reading; any other reading — a drop that stays above
       the latch value, or any grey movement (rise or fall) — is a live measurement, is shown, and
       becomes the new baseline.
    """
    nf, pf = _liters(new, "fresh"), _liters(prev, "fresh")
    if nf is None or pf is None:
        return False
    if nf >= pf or nf > WATER_LATCH_MAX_L:  # not a drop, or not down to the latch -> live
        return False
    ng, pg = _liters(new, "waste"), _liters(prev, "waste")
    if ng is None or pg is None:
        return True  # fresh dropped, grey unknown -> can't corroborate -> latch
    return ng == pg  # grey EXACTLY frozen -> latch; ANY grey movement (rise OR
    # fall, e.g. a dump-station drain) -> live measurement.
    # `<=` here wedged the hold for a month after a real grey
    # dump: every genuine post-dump reading re-latched because
    # grey sat below the pre-dump baseline (fixed 2026-08-16).


def _key(water: dict):
    return (_liters(water, "fresh"), _liters(water, "waste"))


def settle_water(new: dict, good, pending, now: float, settle: float = WATER_SETTLE_S):
    """Decide whether ``new`` becomes the shown/baseline water reading (the ramp debounce).

    Order: no fresh level -> pass through (can't judge). The not-measuring 1 L latch is held:
    vs a baseline, ``implausible_water_drop``; with NO baseline (cold start) any level <=
    ``WATER_LATCH_MAX_L`` — nothing is shown rather than a fake 1 L. A reading equal to the baseline
    is adopted at once. Any other level is a candidate: adopted once the SAME (fresh, waste) has been
    seen for ``settle`` (``now`` and ``settle`` in one unit, the caller's monotonic clock). So a
    single read of a new value — e.g. one 30 s poll landing mid-ramp — is never adopted; a second
    read of the same value ``settle`` later is. ``settle=0`` adopts at once (debounce off).

    :param new: freshly-interpreted water dict.
    :param good: the current baseline (last adopted water dict), or None (cold start).
    :param pending: the candidate ``((fresh, waste), first_seen)`` from the previous call, or None.
    :param now: the caller's clock.
    :param settle: how long a new level must stay unchanged before it is adopted.
    :returns: ``(adopt, pending)`` — adopt True: ``new`` is the new baseline; False: keep showing
        ``good`` (or nothing), flagged stale. Pass ``pending`` back on the next call.

    .. req:: Adopt a new fresh-water level only once it has settled
       :id: R_WATER_RAMP_DEBOUNCE
       :status: implemented
       :tags: water, freshness, ui

       The daemon shall adopt a fresh/grey water reading that differs from the baseline only after
       the same reading has been seen for ``WATER_SETTLE_S`` (the unit ramps 1 -> real value in
       ~4 s when it starts measuring); until then it keeps the baseline, flagged stale. With no
       baseline, a level <= ``WATER_LATCH_MAX_L`` is not a measurement and nothing is shown.
    """
    nf = _liters(new, "fresh")
    if nf is None:
        return True, None
    if good is None or _liters(good, "fresh") is None:
        if nf <= WATER_LATCH_MAX_L:
            return False, None  # cold start on the not-measuring 1 L: show nothing
    elif implausible_water_drop(new, good):
        return False, None
    elif _key(new) == _key(good):
        return True, None
    key = _key(new)
    if pending is None or pending[0] != key:
        pending = (key, now)
    if now - pending[1] >= settle:
        return True, None
    return False, pending
