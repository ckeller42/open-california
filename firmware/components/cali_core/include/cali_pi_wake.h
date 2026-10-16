/* cali_pi_wake.h — the satellite wakes the halted Raspberry Pi (R_FW_PI_WAKE, #387): a wire from
 * Port B GPIO9 (PB_OUT, open drain) to the Pi's pin 5 (GPIO3), GND to pin 6. A Pi 4 halted with
 * EEPROM WAKE_ON_GPIO=1 boots when GPIO3 is pulled to GND (cali_platform.h cali_pi_wake_line).
 *
 * Trigger (pi_wake.c, owner task, every tick while the session is ready): the ignition rising edge —
 * ignition = vehicle TerminalOneFive OR campingmode Enable, the auto camper's rule
 * (cali_ac_ignition). Edge = previous 0 or unknown (boot) -> now 1; an unknown reading (not read on
 * this link) never updates the previous one, so 1 -> unknown -> 1 is no edge. At most one automatic
 * pulse per CALI_PW_MIN_GAP_MS; a manual wake (POST /api/pi_wake, console "pi wake") is refused only
 * while a pulse is in progress. A pulse = the line low for CALI_PW_PULSE_MS, then released by the tick
 * (never a sleep in the owner task). Logs "pi-wake[<reason>]: GPIO9 low <ms> ms", one diag event
 * {"ev":"evt","kind":"pi_wake","reason":…}. The toggle persists (kv CALI_PW_ON_KEY, default ON).
 *
 * C99, no malloc, no NimBLE/ESP-IDF includes.
 */
#ifndef CALI_PI_WAKE_H
#define CALI_PI_WAKE_H

#include <stdint.h>

#include "cali_json.h"

#ifdef __cplusplus
extern "C" {
#endif

#define CALI_PW_PULSE_MS 200u
#define CALI_PW_HOLD_MAX_MS 5000u   /* console "pi hold <ms>": the wiring test's longest hold */
#define CALI_PW_MIN_GAP_MS 60000u   /* automatic (ignition) pulses: at most one per minute */
#define CALI_PW_ON_KEY "pw_ign"     /* 1 byte: 0 off, 1 on; missing = on */

void cali_pw_init(void);              /* owner task, after cali_platform_init: the toggle; releases the line */
void cali_pw_tick(uint64_t now_ms);   /* owner task: ends a pulse; while ready, one ignition step */
/* One ignition reading (-1 unknown, 0, 1) at now_ms: the edge rule above. The tick's step; tests drive it. */
void cali_pw_step(int ign, uint64_t now_ms);
/* Pull the line low for ms (1..CALI_PW_HOLD_MAX_MS) now; reason "ignition" | "manual". 0 ok, -1 busy
 * (a pulse in progress) or ms out of range. cali_pw_wake = a CALI_PW_PULSE_MS pulse. */
int cali_pw_pulse(const char *reason, uint32_t ms);
int cali_pw_wake(const char *reason);
int cali_pw_set_enabled(int on);   /* persisted: 0 ok, -1 not saved */
int cali_pw_enabled(void);
/* device.pi_wake: {"enabled":<bool>,"last_s":<s since the last pulse>|null,"reason":"ignition"|"manual"|null} */
void cali_pw_json(cali_json_t *j);

#ifdef __cplusplus
}
#endif

#endif /* CALI_PI_WAKE_H */
