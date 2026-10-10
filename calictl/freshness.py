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
"""

from __future__ import annotations

# The parked unit's stale fresh-water latch value, in liters: observed as FreshWaterLevel=1 on this
# van (2026-07-14 and since). Only a drop to <= this is a latch candidate.
WATER_LATCH_MAX_L = 1


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
