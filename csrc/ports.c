/* ports.c — portable calictl decision logic. See ports.h. */
#include "ports.h"

/* Exact ladder of calictl/freshness.py:implausible_water_drop (fixed 2026-08-16:
 * grey compares with ==, not <= — any grey movement, rise OR fall, is live;
 * 2026-10-10: only a drop to <= FRESH_LATCH_MAX_L is a latch candidate). */
int freshness_implausible_drop(int32_t nf, int32_t pf, int32_t ng, int32_t pg,
                               uint8_t have)
{
    if (!(have & FRESH_HAVE_NF) || !(have & FRESH_HAVE_PF))
        return 0;                    /* missing fresh -> can't judge */
    if (nf >= pf || nf > FRESH_LATCH_MAX_L)
        return 0;                    /* not a drop, or not down to the latch -> live */
    if (!(have & FRESH_HAVE_NG) || !(have & FRESH_HAVE_PG))
        return 1;                    /* uncorroborated drop -> conservative latch */
    return ng == pg;                 /* grey EXACTLY frozen -> latch */
}

/* Exact ladder of calictl/freshness.py:settle_water (2026-10-10 ramp debounce). */
int freshness_settle(int32_t nf, int32_t pf, int32_t ng, int32_t pg, uint8_t have,
                     uint64_t now_ms, uint32_t settle_ms, fresh_pending_t *p)
{
    uint8_t hng = (have & FRESH_HAVE_NG) ? 2u : 0u;
    if (!(have & FRESH_HAVE_NF)) {
        p->have = 0;
        return 1;                    /* no fresh level -> pass through */
    }
    if (!(have & FRESH_HAVE_PF)) {
        if (nf <= FRESH_LATCH_MAX_L) {
            p->have = 0;
            return 0;                /* cold start on the 1 L latch: show nothing */
        }
    } else if (freshness_implausible_drop(nf, pf, ng, pg, have)) {
        p->have = 0;
        return 0;
    } else if (nf == pf && hng == ((have & FRESH_HAVE_PG) ? 2u : 0u) && (!hng || ng == pg)) {
        p->have = 0;
        return 1;                    /* the baseline itself */
    }
    if (!(p->have & 1u) || p->fresh != nf || (p->have & 2u) != hng || (hng && p->waste != ng)) {
        p->fresh = nf;
        p->waste = hng ? ng : 0;
        p->have = (uint8_t)(1u | hng);
        p->since_ms = now_ms;
    }
    if (now_ms - p->since_ms >= settle_ms) {
        p->have = 0;
        return 1;
    }
    return 0;
}
