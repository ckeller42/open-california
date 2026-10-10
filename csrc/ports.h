/* ports.h — portable calictl decision logic shared with the ESP32 port (#154).
 *
 * Pure functions only: no clock, no I/O, no state. The BLE-bound orchestration
 * around each of these (sampling cadence, persistence, actuation) stays
 * platform-native; what is shared is the correctness-critical DECISION,
 * pinned to the Python originals by tests/test_ports_parity.py.
 */
#ifndef PORTS_H
#define PORTS_H
#include <stdint.h>

/* Water stale-latch guard — port of calictl/freshness.py:implausible_water_drop.
 * Parked, the unit freezes both tanks and hands out a 1 L fresh latch; a fresh
 * DROP to <= FRESH_LATCH_MAX_L while grey is EXACTLY frozen is the latch signature
 * (hold the last plausible value). A drop that stays above it, or any grey
 * movement, is a live measurement. Inputs are liters
 * (the raw water Level field IS liters — linear scale — so the ESP feeds raw
 * decoded fields directly). `have` presence bits: 1=new-fresh, 2=prev-fresh,
 * 4=new-grey, 8=prev-grey. Returns 1 = stale latch, 0 = plausible. */
#define FRESH_LATCH_MAX_L 1   /* freshness.WATER_LATCH_MAX_L: the observed latch value */
#define FRESH_HAVE_NF 0x1u
#define FRESH_HAVE_PF 0x2u
#define FRESH_HAVE_NG 0x4u
#define FRESH_HAVE_PG 0x8u
int freshness_implausible_drop(int32_t nf, int32_t pf, int32_t ng, int32_t pg,
                               uint8_t have);

/* Water ramp debounce — port of calictl/freshness.py:settle_water. Starting a
 * measurement the unit ramps the level 1 -> real value in ~4 s, so a NEW level
 * (!= the baseline) is adopted only once the same (fresh, grey) has been seen for
 * settle_ms. The baseline is (pf, pg) with the same `have` bits (no FRESH_HAVE_PF =
 * cold start: a level <= FRESH_LATCH_MAX_L is held, nothing shown). `*p` is the
 * caller's candidate, updated in place (zero-init). Returns 1 = adopt nf/ng as the
 * new baseline, 0 = hold (show the baseline, or nothing). */
#define FRESH_SETTLE_MS 5000u  /* freshness.WATER_SETTLE_S */
typedef struct {
    int32_t fresh, waste;
    uint8_t have;              /* bit0: a candidate is pending, bit1: its grey is known */
    uint64_t since_ms;
} fresh_pending_t;
int freshness_settle(int32_t nf, int32_t pf, int32_t ng, int32_t pg, uint8_t have,
                     uint64_t now_ms, uint32_t settle_ms, fresh_pending_t *p);

#endif /* PORTS_H */
