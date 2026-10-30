/**
 * @file cali_pi_wake.h
 * @brief The satellite wakes and halts the Raspberry Pi over two GPIO wires.
 *
 * **Wake**: a wire from Port B GPIO9 (PB_OUT, open drain) to the Pi's pin 5 (GPIO3), GND to pin 6. A
 * Pi 4 halted with EEPROM `WAKE_ON_GPIO=1` boots when GPIO3 is pulled to GND (cali_pi_wake_line in
 * cali_platform.h).
 *
 * Trigger (pi_wake.c, owner task, every tick while the session is ready): the ignition rising edge.
 * Ignition is vehicle TerminalOneFive OR campingmode Enable, the auto camper's rule
 * (cali_ac_ignition). An edge is a previous 0 or unknown (boot) followed by 1. An unknown reading
 * (not read on this link) never updates the previous one, so 1 -> unknown -> 1 is no edge. There is
 * at most one automatic pulse per CALI_PW_MIN_GAP_MS. A manual wake (`POST /api/pi_wake`, console
 * `pi wake`) is refused only while a pulse is in progress. A pulse holds the line low for
 * CALI_PW_PULSE_MS and the tick then releases it (the owner task never sleeps). Each pulse logs
 * `pi-wake[REASON]: GPIO9 low MS ms` and one diag event `{"ev":"evt","kind":"pi_wake","reason":...}`.
 * The toggle persists (kv CALI_PW_ON_KEY, default ON).
 *
 * @verbatim embed:rst:leading-asterisk
 * .. req:: The satellite wakes the halted Raspberry Pi through a GPIO
 *    :id: R_FW_PI_WAKE
 *    :status: implemented
 *    :tags: esp32, gpio, power
 *
 *    The firmware shall drive Port B GPIO9 as an open-drain output that is released (high-Z) from
 *    boot and never driven high, and pull it low for 200 ms on the ignition rising edge (vehicle
 *    terminal 15 or campingmode Enable; previous 0 or unknown -> 1, a read gap never an edge), at most
 *    once per 60 s, while "Wake on ignition" is on (NVS ``pw_ign``, default on). ``POST /api/pi_wake``
 *    (station mode only, 409 while a pulse runs) and the console wake it by hand; ``POST
 *    /api/pi_wake_setting`` switches the setting; ``/api/state`` reports ``device.pi_wake``
 *    ``{enabled, last_s, reason}``; Satellite settings shows the box (EN + DE).
 * @endverbatim
 *
 * **Halt**: a second wire, Port B GPIO8 (open drain) to the Pi's pin 11 (GPIO17). The Pi runs
 * `dtoverlay=gpio-shutdown,gpio_pin=17`, so the line low is KEY_POWER, a clean poweroff. A halt holds
 * GPIO8 low for CALI_PH_PULSE_MS.
 *
 * The automatic halt is a parking timer. `parked_since` is set by the boot, by an ignition 0 reading
 * after a 1 or an unknown, and by every wake pulse. A known ignition 1 means driving (no halt). An
 * unknown ignition keeps the state (a parked unit sleeps and drops the link). The halt comes once the
 * unit has been parked for `ph_min` minutes (kv CALI_PH_MIN_KEY, uint16 LE, 0 = off, missing = 30).
 * The battery guard (kv CALI_PH_SOC_KEY, uint8 percent, 0 = off, missing = 30) shortens that to at
 * most CALI_PH_GUARD_MIN minutes while the battery is low (cali_pw_batt_low over the latest energy
 * frame held, from any link). After a halt there is no automatic halt until a wake pulse or an
 * ignition 1. Wake and halt never overlap: a pulse on one line is refused (busy) while the other is
 * low. Each halt logs `pi-halt[timer|battery|manual]: GPIO8 low MS ms` and one diag event
 * `{"ev":"evt","kind":"pi_halt","reason":...}`.
 *
 * @verbatim embed:rst:leading-asterisk
 * .. req:: The satellite halts the Raspberry Pi after parking and on a low battery
 *    :id: R_FW_PI_HALT
 *    :status: implemented
 *    :tags: esp32, gpio, power
 *    :links: R_FW_PI_WAKE
 *
 *    The firmware shall drive Port B GPIO8 as GPIO9 (open drain, released from boot, never driven
 *    high) and pull it low for 500 ms once the van has been parked (the boot, the ignition 0 after 1
 *    or unknown, or a wake pulse; unknown ignition keeps the state) for the "Shut down after parking"
 *    minutes (NVS ``ph_min``, 0 off, default 30), or at most 10 minutes while the battery is low and
 *    the guard is on (NVS ``ph_soc``, default 30 %: SleepWarning, WarningLevelTwo, or the SoC level ×10
 *    at or below it). Once per park: only a wake pulse or the ignition re-arms it. The two lines are
 *    never low together. ``POST /api/pi_halt`` (station mode only, 409 busy) and the console halt it
 *    by hand; ``POST /api/pi_wake_setting`` sets both settings; ``/api/state`` reports the halt in
 *    ``device.pi_wake``; Satellite settings shows it (EN + DE).
 * @endverbatim
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

/** @brief Length of a wake pulse, in ms. */
#define CALI_PW_PULSE_MS 200u
/** @brief Longest hold in ms for console `pi hold MS`, the wiring test. */
#define CALI_PW_HOLD_MAX_MS 5000u
/** @brief Minimum gap in ms between automatic (ignition) pulses: at most one per minute. */
#define CALI_PW_MIN_GAP_MS 60000u
/** @brief kv key of the wake toggle: 1 byte, 0 off, 1 on, missing = on. */
#define CALI_PW_ON_KEY "pw_ign"
/** @brief Length of a halt pulse, in ms. */
#define CALI_PH_PULSE_MS 500u
/** @brief The battery guard halts after at most this many minutes parked. */
#define CALI_PH_GUARD_MIN 10u
/** @brief Milliseconds in one minute of the parking timer. */
#define CALI_PH_MINUTE_MS 60000u
/** @brief kv key of the halt delay: uint16 LE, minutes 0..CALI_PH_MIN_MAX, 0 = off. */
#define CALI_PH_MIN_KEY "ph_min"
/** @brief Halt delay in minutes when the key is missing. */
#define CALI_PH_MIN_DEFAULT 30u
/** @brief Largest halt delay in minutes. */
#define CALI_PH_MIN_MAX 1440u
/** @brief kv key of the battery guard threshold: uint8 percent 0..100, 0 = off. */
#define CALI_PH_SOC_KEY "ph_soc"
/** @brief Guard threshold in percent when the key is missing. */
#define CALI_PH_SOC_DEFAULT 30u

/** @brief Load the settings and release both lines. Call on the owner task after cali_platform_init. */
void cali_pw_init(void);

/**
 * @brief End a pulse that is due and run one step. Call on the owner task; the ignition is unknown while the session is not ready.
 * @param now_ms  Uptime in ms.
 */
void cali_pw_tick(uint64_t now_ms);

/**
 * @brief Feed one ignition reading: the wake edge rule and the parking timer. The tick's step; tests drive it.
 * @param ign     Ignition: -1 unknown, 0 or 1.
 * @param now_ms  Uptime in ms.
 */
void cali_pw_step(int ign, uint64_t now_ms);

/**
 * @brief Pull the wake line low now for a given time.
 * @param reason  "ignition" or "manual".
 * @param ms      Hold time in ms, 1..CALI_PW_HOLD_MAX_MS.
 * @return 0 ok, -1 busy (a pulse in progress) or ms out of range.
 */
int cali_pw_pulse(const char *reason, uint32_t ms);

/**
 * @brief A wake pulse of CALI_PW_PULSE_MS.
 * @param reason  "ignition" or "manual".
 * @return 0 ok, -1 busy.
 */
int cali_pw_wake(const char *reason);

/**
 * @brief Switch the wake on ignition and persist the toggle.
 * @param on  1 on, 0 off.
 * @return 0 ok, -1 not saved.
 */
int cali_pw_set_enabled(int on);

/**
 * @brief Get the wake-on-ignition switch.
 * @return 1 when the wake on ignition is on, else 0.
 */
int cali_pw_enabled(void);

/**
 * @brief Pull the halt line (GPIO8) low now for a given time.
 * @param reason  "timer", "battery" or "manual".
 * @param ms      Hold time in ms, 1..CALI_PW_HOLD_MAX_MS.
 * @return 0 ok, -1 busy (either line low) or ms out of range.
 */
int cali_ph_pulse(const char *reason, uint32_t ms);

/**
 * @brief A halt pulse of CALI_PH_PULSE_MS.
 * @param reason  "timer", "battery" or "manual".
 * @return 0 ok, -1 busy.
 */
int cali_ph_halt(const char *reason);

/**
 * @brief Set the halt delay and persist it.
 * @param min  Minutes parked before the halt, 0..CALI_PH_MIN_MAX, 0 = off.
 * @return 0 ok, -1 out of range or not saved.
 */
int cali_ph_set_min(unsigned min);

/**
 * @brief Set the battery guard threshold and persist it.
 * @param pct  Percent 0..100, 0 = off.
 * @return 0 ok, -1 out of range or not saved.
 */
int cali_ph_set_soc(unsigned pct);

/**
 * @brief Get the halt delay.
 * @return The halt delay in minutes (0 = off).
 */
int cali_ph_min(void);

/**
 * @brief Get the battery guard threshold.
 * @return The battery guard threshold in percent (0 = off).
 */
int cali_ph_soc(void);

/**
 * @brief Pure: whether the energy frame says the battery is low.
 * @param sleep_warning  The frame's SleepWarning, -1 = not read.
 * @param warn_two       The frame's WarningLevelTwo, -1 = not read.
 * @param soc_level      The frame's SocTwoBattAfs level, -1 = not read.
 * @param soc_pct        The guard threshold in percent.
 * @return 1 when SleepWarning is 1, WarningLevelTwo is at least 1, or the level is 1..10
 *         (times 10 = the app's percent; 0 and 11..15 are unknown) and level times 10 is at most `soc_pct`.
 */
int cali_pw_batt_low(long sleep_warning, long warn_two, long soc_level, int soc_pct);

/**
 * @brief Write the `device.pi_wake` object.
 *
 * Members: `enabled` (bool), `last_s` (seconds since the last wake pulse, or null), `reason`
 * (`"ignition"`, `"manual"` or null), `halt_after_min`, `halt_soc_pct`, `halt_in_s` (seconds to the
 * automatic halt, or null while driving, halted or with no limit), `halted` (bool), `halt_last_s`
 * (seconds, or null), `halt_reason` (`"timer"`, `"battery"`, `"manual"` or null) and `batt_low` (bool).
 *
 * @param j  The JSON writer; the caller writes the key first.
 */
void cali_pw_json(cali_json_t *j);

#ifdef __cplusplus
}
#endif

#endif /* CALI_PI_WAKE_H */
