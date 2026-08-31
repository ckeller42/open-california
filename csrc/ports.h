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

#endif /* PORTS_H */
