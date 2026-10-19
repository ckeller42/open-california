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
 * The halt (R_FW_PI_HALT): a second wire, Port B GPIO8 (open drain) to the Pi's pin 11 (GPIO17); the Pi
 * runs dtoverlay=gpio-shutdown,gpio_pin=17, so the line low = KEY_POWER = a clean poweroff. A halt =
 * GPIO8 low for CALI_PH_PULSE_MS. Automatic: the parking timer — parked_since is (re)set by the boot,
 * the ignition 0 reading after a 1 or an unknown, and every wake pulse; known ignition 1 = driving (no
 * halt); unknown keeps the state (a parked unit sleeps and drops the link). Halt once parked for
 * ph_min minutes (kv CALI_PH_MIN_KEY, uint16 LE, 0 = off, missing = 30). The battery guard (kv
 * CALI_PH_SOC_KEY, uint8 %, 0 = off, missing = 30) shortens that to at most CALI_PH_GUARD_MIN minutes
 * while the battery is low (cali_pw_batt_low over the latest energy frame held, any link). After a
 * halt no automatic halt until a wake pulse or ignition 1. Wake and halt never overlap: a pulse on one
 * line is refused (busy) while the other is low. Logs "pi-halt[timer|battery|manual]: GPIO8 low
 * <ms> ms", one diag event {"ev":"evt","kind":"pi_halt","reason":…}.
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
#define CALI_PH_PULSE_MS 500u
#define CALI_PH_GUARD_MIN 10u       /* the battery guard halts after at most this many minutes parked */
#define CALI_PH_MINUTE_MS 60000u
#define CALI_PH_MIN_KEY "ph_min"    /* uint16 LE: halt after parking, minutes 0..CALI_PH_MIN_MAX; 0 off */
#define CALI_PH_MIN_DEFAULT 30u
#define CALI_PH_MIN_MAX 1440u
#define CALI_PH_SOC_KEY "ph_soc"    /* uint8: the battery guard threshold, % 0..100; 0 off */
#define CALI_PH_SOC_DEFAULT 30u

void cali_pw_init(void);              /* owner task, after cali_platform_init: the settings; releases both lines */
void cali_pw_tick(uint64_t now_ms);   /* owner task: ends a pulse; one step (ignition unknown while not ready) */
/* One ignition reading (-1 unknown, 0, 1) at now_ms: the wake edge rule and the parking timer above.
 * The tick's step; tests drive it. */
void cali_pw_step(int ign, uint64_t now_ms);
/* Pull the line low for ms (1..CALI_PW_HOLD_MAX_MS) now; reason "ignition" | "manual". 0 ok, -1 busy
 * (a pulse in progress) or ms out of range. cali_pw_wake = a CALI_PW_PULSE_MS pulse. */
int cali_pw_pulse(const char *reason, uint32_t ms);
int cali_pw_wake(const char *reason);
int cali_pw_set_enabled(int on);   /* persisted: 0 ok, -1 not saved */
int cali_pw_enabled(void);
/* Pull GPIO8 low for ms (1..CALI_PW_HOLD_MAX_MS); reason "timer" | "battery" | "manual". 0 ok, -1 busy
 * (either line low) or ms out of range. cali_ph_halt = a CALI_PH_PULSE_MS pulse. */
int cali_ph_pulse(const char *reason, uint32_t ms);
int cali_ph_halt(const char *reason);
int cali_ph_set_min(unsigned min);   /* persisted: 0 ok, -1 out of range or not saved */
int cali_ph_set_soc(unsigned pct);   /* persisted: 0 ok, -1 out of range or not saved */
int cali_ph_min(void);
int cali_ph_soc(void);
/* Pure: the energy frame's SleepWarning, WarningLevelTwo, SocTwoBattAfs level (-1 = not read) against
 * the guard threshold soc_pct: 1 when SleepWarning == 1, WarningLevelTwo >= 1, or the level is 1..10
 * (x10 = the app's %; 0 and 11..15 unknown) and level*10 <= soc_pct. */
int cali_pw_batt_low(long sleep_warning, long warn_two, long soc_level, int soc_pct);
/* device.pi_wake: {"enabled":<bool>,"last_s":<s since the last pulse>|null,"reason":"ignition"|"manual"|null,
 * "halt_after_min":n,"halt_soc_pct":n,"halt_in_s":<s to the automatic halt>|null (driving, halted, off),
 * "halted":<bool>,"halt_last_s":<s>|null,"halt_reason":"timer"|"battery"|"manual"|null,"batt_low":<bool>} */
void cali_pw_json(cali_json_t *j);

#ifdef __cplusplus
}
#endif

#endif /* CALI_PI_WAKE_H */
