"""Physical-plausibility guards for latched/stale sensor reads (stdlib-only, pure).

The camper unit only *measures* fresh water while its water system is powered (unlocking the
van turns it on). Parked and locked, it stops measuring and hands out a **stale latched value**
(observed: a true 17-19 L tank read back as 1 L). buspi faithfully mirrors that, so the UI would
show a confident-but-wrong low level and a nonsense "0 days left" forecast.

We can't force the unit to measure, but we can reject a physically impossible reading: a large
fresh-water DROP with no matching grey-water RISE is impossible — drained water goes to the grey
tank. When we see that, we keep displaying the last plausible reading (flagged stale) instead of
the bogus low. See ``docs/business-logic/value-freshness.md`` (confirmed at the van 2026-07-14).
"""
from __future__ import annotations


def _liters(water: dict, tank: str):
    """Fresh/waste tank level in liters, or None if absent."""
    t = (water or {}).get(tank)
    return t.get("liters") if isinstance(t, dict) else None


def implausible_water_drop(new: dict, prev: dict, *, grey_margin_l: int = 0) -> bool:
    """True when ``new`` fresh-water reading can't physically follow ``prev``.

    The test is conservation of water, NOT the size of the drop: fresh water that leaves the tank
    must appear in the grey tank. So ANY fresh-water drop whose grey-water rise falls short of it
    (by more than ``grey_margin_l``) is water that "vanished" — the parked/asleep stale latch, which
    **decays gradually** (~1 L/poll) and would otherwise slip under a size threshold and ratchet the
    baseline down. Real usage (fresh down, grey up together) and refills / re-measures (fresh up or
    equal) are plausible and return False. Missing levels return False (can't judge).

    :param new: freshly-interpreted water dict (``{"fresh":{"liters":..}, "waste":{"liters":..}}``).
    :param prev: the last plausible water dict to compare against.
    :returns: True if ``new`` is physically impossible vs ``prev`` (a stale latch).

    .. req:: Reject physically impossible fresh-water drops as stale
       :id: R_WATER_STALE_GUARD
       :status: implemented
       :tags: water, freshness, ui

       The daemon shall treat a fresh-water reading that drops by a large amount with no matching
       grey-water rise as a stale latch (the unit stops measuring when the water system is off),
       and keep displaying the last plausible reading rather than the bogus low value.
    """
    nf, pf = _liters(new, "fresh"), _liters(prev, "fresh")
    if nf is None or pf is None:
        return False
    drop = pf - nf
    if drop <= 0:                       # not a drop (refill / same / active re-measure) -> plausible
        return False
    ng, pg = _liters(new, "waste"), _liters(prev, "waste")
    grey_rise = (ng - pg) if (ng is not None and pg is not None) else 0
    return grey_rise < drop - grey_margin_l
