/* ports.h — portable calictl decision logic shared with the ESP32 port (#154).
 *
 * Pure functions only: no clock, no I/O, no state. The BLE-bound orchestration
 * around each of these (sampling cadence, persistence, actuation) stays
 * platform-native; what is shared is the safety/correctness-critical DECISION,
 * pinned to the Python originals by tests/test_ports_parity.py.
 */
#ifndef PORTS_H
#define PORTS_H
#include <stdint.h>

/* Water stale-latch guard — port of calictl/freshness.py:implausible_water_drop.
 * Parked, the unit freezes both tanks and decays fresh toward a ~1 L latch; a
 * fresh DROP while grey is EXACTLY frozen is the latch signature (hold the last
 * plausible value). Any grey movement proves live measurement. Inputs are liters
 * (the raw water Level field IS liters — linear scale — so the ESP feeds raw
 * decoded fields directly). `have` presence bits: 1=new-fresh, 2=prev-fresh,
 * 4=new-grey, 8=prev-grey. Returns 1 = stale latch, 0 = plausible. */
#define FRESH_HAVE_NF 0x1u
#define FRESH_HAVE_PF 0x2u
#define FRESH_HAVE_NG 0x4u
#define FRESH_HAVE_PG 0x8u
int freshness_implausible_drop(int32_t nf, int32_t pf, int32_t ng, int32_t pg,
                               uint8_t have);

/* Plausibility anchors — port of calictl/anchors.py:check (the decode-drift
 * alarm). Same hard-coded physical constants as the Python original (they are
 * physics, not protocol facts); inputs are the INTERPRETED values (scaling
 * raw fields is the caller's concern, #154). Violations return as a bitmask of
 * the stable IDs below; `have` uses the same bit positions for presence
 * (absent value = never a violation). The installed gates mirror Python: cooler
 * and roof anchors only fire while that subsystem reports installed. */
#define ANCHOR_BATT2_V       (1u << 0)   /* 8.0 .. 16.0 V   */
#define ANCHOR_SOC2_LEVEL    (1u << 1)   /* 0 .. 15         */
#define ANCHOR_COOLER_LEVEL  (1u << 2)   /* 1 .. 5          */
#define ANCHOR_QUIET_FROM    (1u << 3)   /* 0 .. 23 h       */
#define ANCHOR_QUIET_TO      (1u << 4)   /* 0 .. 23 h       */
#define ANCHOR_ROOF_POSITION (1u << 5)   /* 0 .. 15         */
#define ANCHOR_LEVEL_ROLL    (1u << 6)   /* |deg| <= 90     */
#define ANCHOR_LEVEL_PITCH   (1u << 7)   /* |deg| <= 90     */

typedef struct {
    float   batt2_v;
    int32_t soc2_level;
    int32_t cooler_level, quiet_from, quiet_to;
    int32_t roof_position;
    float   level_roll, level_pitch;
    uint16_t have;               /* ANCHOR_* bit set = value provided */
    uint8_t cooler_installed, roof_installed;
} anchors_in_t;

uint32_t anchors_check(const anchors_in_t *in);   /* violation bitmask; 0 = clean */

#endif /* PORTS_H */
