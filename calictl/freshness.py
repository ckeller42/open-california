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

So the guard fires on the latch SIGNATURE — a fresh drop while grey is frozen — and keeps showing
the last plausible reading (flagged stale). Any grey movement proves the unit is live-measuring, so
the fresh drop is real (drinking/cooking/an external grey drain make fresh fall faster than grey
fills; that is NOT the latch). See ``docs/business-logic/value-freshness.md``.
"""
from __future__ import annotations


def _liters(water: dict, tank: str):
    """Fresh/waste tank level in liters, or None if absent."""
    t = (water or {}).get(tank)
    return t.get("liters") if isinstance(t, dict) else None


def implausible_water_drop(new: dict, prev: dict) -> bool:
    """True when ``new``'s fresh-water drop looks like the parked latch, not real usage.

    The discriminator is whether the GREY tank moved, NOT conservation of mass. The unit freezes
    both tanks when unpowered, so a fresh drop while grey is frozen is the latch signature (hold the
    last plausible value). Any grey rise proves the unit is live-measuring, so the fresh drop is
    real — even when fresh falls faster than grey fills (drinking/cooking/an external grey drain).
    A non-drop (refill / same / re-measure) is always plausible. Missing fresh returns False (can't
    judge); a fresh drop we can't corroborate with grey is treated as the latch (conservative — the
    parked-decay ratchet this prevents is worse than briefly holding a real drop, which self-clears
    the moment usage pauses or grey moves).

    :param new: freshly-interpreted water dict (``{"fresh":{"liters":..}, "waste":{"liters":..}}``).
    :param prev: the last plausible water dict to compare against.
    :returns: True if ``new`` is the stale latch vs ``prev``.

    .. req:: Reject the parked stale-latch fresh-water reading
       :id: R_WATER_STALE_GUARD
       :status: implemented
       :tags: water, freshness, ui

       The daemon shall treat a fresh-water drop while the grey tank is frozen as the stale latch
       (the unit freezes both tanks when the water system is off) and keep displaying the last
       plausible reading; a fresh drop accompanied by any grey rise is real usage and is shown.
    """
    nf, pf = _liters(new, "fresh"), _liters(prev, "fresh")
    if nf is None or pf is None:
        return False
    if nf >= pf:                        # not a drop (refill / same / active re-measure) -> plausible
        return False
    ng, pg = _liters(new, "waste"), _liters(prev, "waste")
    if ng is None or pg is None:
        return True                     # fresh dropped, grey unknown -> can't corroborate -> latch
    return ng <= pg                     # grey frozen/fell -> latch; grey rose -> live measurement
