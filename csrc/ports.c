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

/* Exact checks of calictl/anchors.py:check — same constants, same installed
 * gates; a value simply not provided (have bit clear) is never a violation. */
uint32_t anchors_check(const anchors_in_t *in)
{
    uint32_t out = 0;
    if ((in->have & ANCHOR_BATT2_V) &&
        !(in->batt2_v >= 8.0f && in->batt2_v <= 16.0f))
        out |= ANCHOR_BATT2_V;
    if ((in->have & ANCHOR_SOC2_LEVEL) &&
        !(in->soc2_level >= 0 && in->soc2_level <= 15))
        out |= ANCHOR_SOC2_LEVEL;
    if (in->cooler_installed) {
        if ((in->have & ANCHOR_COOLER_LEVEL) &&
            !(in->cooler_level >= 1 && in->cooler_level <= 5))
            out |= ANCHOR_COOLER_LEVEL;
        if ((in->have & ANCHOR_QUIET_FROM) &&
            !(in->quiet_from >= 0 && in->quiet_from <= 23))
            out |= ANCHOR_QUIET_FROM;
        if ((in->have & ANCHOR_QUIET_TO) &&
            !(in->quiet_to >= 0 && in->quiet_to <= 23))
            out |= ANCHOR_QUIET_TO;
    }
    if (in->roof_installed && (in->have & ANCHOR_ROOF_POSITION) &&
        !(in->roof_position >= 0 && in->roof_position <= 15))
        out |= ANCHOR_ROOF_POSITION;
    if ((in->have & ANCHOR_LEVEL_ROLL) &&
        (in->level_roll > 90.0f || in->level_roll < -90.0f))
        out |= ANCHOR_LEVEL_ROLL;
    if ((in->have & ANCHOR_LEVEL_PITCH) &&
        (in->level_pitch > 90.0f || in->level_pitch < -90.0f))
        out |= ANCHOR_LEVEL_PITCH;
    return out;
}

/* Exact formula of device._roof_safety_counter (mask AFTER the add, in 64-bit,
 * so a seed near 2^32 wraps identically to Python's & 0xFFFFFFFF). */
uint32_t roof_safety_counter(uint32_t seed, uint64_t elapsed_ms, uint32_t tick_ms)
{
    return (uint32_t)(((uint64_t)seed + elapsed_ms / tick_ms) & 0xFFFFFFFFu);
}

void roof_beat_bytes(uint32_t ctr, uint8_t out[4])
{
    out[0] = (uint8_t)(ctr >> 24);
    out[1] = (uint8_t)(ctr >> 16);
    out[2] = (uint8_t)(ctr >> 8);
    out[3] = (uint8_t)ctr;
}
