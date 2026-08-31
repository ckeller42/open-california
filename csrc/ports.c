/* ports.c — portable calictl decision logic. See ports.h. */
#include "ports.h"

/* Exact ladder of calictl/freshness.py:implausible_water_drop (fixed 2026-08-16:
 * grey compares with ==, not <= — any grey movement, rise OR fall, is live). */
int freshness_implausible_drop(int32_t nf, int32_t pf, int32_t ng, int32_t pg,
                               uint8_t have)
{
    if (!(have & FRESH_HAVE_NF) || !(have & FRESH_HAVE_PF))
        return 0;                    /* missing fresh -> can't judge */
    if (nf >= pf)
        return 0;                    /* not a drop (refill / same) -> plausible */
    if (!(have & FRESH_HAVE_NG) || !(have & FRESH_HAVE_PG))
        return 1;                    /* uncorroborated drop -> conservative latch */
    return ng == pg;                 /* grey EXACTLY frozen -> latch */
}
