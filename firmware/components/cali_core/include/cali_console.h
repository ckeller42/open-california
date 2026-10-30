/**
 * @file cali_console.h
 * @brief The firmware's line protocol, identical on the host build (stdin and stdout) and the
 *        ESP32 (UART).
 *
 * Call everything on the task that owns the transport (the NimBLE host task).
 *
 * @section console_input Input
 *
 * One command per line; surrounding whitespace is ignored. Lines are up to 127 bytes (both line
 * readers: LINE_MAX_LEN 128 in host_main.c and app_main.c).
 *
 * - `pair`: start pairing (runner EV_START; ignored by the state machine unless idle or error).
 * - `passkey N`: the code the unit displays, 1-6 digits; ignored unless the runner waits for it.
 * - `forget`: drop the bond (runner EV_RESET).
 * - `status`: print the current STATE line (plus a "wifi" member once the WiFi runtime runs).
 * - `quit`: call cali_console_on_quit (the host exits; a NULL hook is ignored).
 * - `wifi set <ssid> <psk>`: store the credentials (kv `wifi_ssid` and `wifi_psk`, like
 *   POST /api/wifi) and hand them to the WiFi runner. Both are single tokens: an SSID or a
 *   password with spaces cannot be typed here (use WiFi setup). SSID 1..NET_SSID_MAX bytes, PSK
 *   NET_PSK_MIN..NET_PSK_MAX; otherwise `LOG wifi: usage: wifi set <ssid> <psk>`,
 *   `LOG wifi: bad ssid` or `LOG wifi: bad psk`. While online or retrying, the new credentials
 *   replace the old ones (`LOG wifi: credentials replaced, reconnecting`). The line buffers are
 *   wiped after the command (they held the passphrase; on the ESP the console queue's slot keeps
 *   its copy until a later line reuses it). The PSK is never printed.
 * - `wifi status`: `LOG wifi: <setup|station|off> ssid=<ssid|-> ip=<a.b.c.d|-> rssi=<dBm|-> scan=<n>`
 *   (mode as /api/wifi's; scan = networks in the last scan result).
 * - `wifi forget`: drop the WiFi credentials and reopen the setup hotspot (cali_wifi_run_forget).
 * - `wifi scan`: ask for a WiFi scan (held back while BLE pairs; see cali_wifi_run.h).
 * - `set <fn> <what> [value]`: a control command (cali_control.h). It uses the same builders,
 *   gates and allow-list as POST /api/command, in any WiFi mode (the console is physical access).
 *   The value is the rest of the line (none = JSON null). The outcome appears on `LOG control: ...`.
 * - `sensors`: `SENSORS <the GET /api/sensors object>` (cali_sensors.h, R_FW_SENSORS).
 * - `sensors on|off`: the exp_sensors flag (cali_exp_sensors_set: `LOG exp: sensors on|off`).
 * - `sensors add <id> [alias|-] [keyhex|-]` and `sensors remove <id>`: `LOG sensors ok` or
 *   `LOG sensors err <code>`.
 * - `pi wake`: one manual Pi wake pulse (cali_pi_wake.h, R_FW_PI_WAKE): `LOG pi-wake[manual]: GPIO9
 *   low 200 ms`, or `LOG pi-wake: busy` while a pulse is in progress.
 * - `pi hold <ms>`: the wiring test, GPIO9 low for 1..5000 ms (otherwise `LOG pi-wake: usage: ...`).
 * - `pi halt [ms]`: one manual Pi halt pulse (R_FW_PI_HALT): GPIO8 low 500 ms, or 1..5000 ms as the
 *   wiring test (read `pinctrl get 17` on a Pi without the gpio-shutdown overlay).
 *   `LOG pi-halt[manual]: GPIO8 low <ms> ms`, or `LOG pi-halt: busy` while either line is low.
 * - `pi timer <min>`: halt after parking, 0..1440 min (0 = off; cali_ph_set_min).
 * - `pi guard <pct>`: the battery guard, 0..100 % (0 = off; cali_ph_set_soc).
 * - `pi status`: `LOG pi-wake: {"pi_wake":<the /api/state device.pi_wake object>}`.
 * - `ble`: `LOG ble: {"ble":<the /api/state device.ble object>}` (cali_ble_exp.h, R_FW_BLE_MODE).
 * - `ble mode hold|quiet|off`: the BLE link mode (`LOG session: ble mode <m> (console)`). hold =
 *   link plus 1003 heartbeat, quiet = link with the heartbeat only through a read-all, off = no link.
 * - `ble probe`: one fresh read-all now (R_FW_BLE_PROBE), then back to the mode. `LOG ble: probe
 *   refused` with no bond or a probe running.
 * - `ble exp <min> [HH-HH]`: the night experiment (R_FW_BLE_EXP): hold, then quiet, then off,
 *   1..720 minutes each, inside the Berlin-time window (default 22-06). `LOG ble-exp: refused (no
 *   time)` without a wall clock, `LOG usage: ble exp <1-720 min> [HH-HH] | ble exp off` for bad
 *   arguments.
 * - `ble exp off`: end it, back to hold.
 *
 * Before the WiFi runtime booted (host build without --http), every `wifi ...` line only prints
 * `LOG wifi: not enabled`. Anything else prints `LOG unknown command: <first word>` (for an
 * unknown wifi subcommand: `LOG unknown command: wifi <subcommand>`). It never prints the rest of the line, which a
 * mistyped `wifi set` fills with the passphrase.
 *
 * @section console_output Output
 *
 * stdout, one line each, flushed.
 *
 * - `STATE {"state":"<name>","attempts":N,"error":"<name>"|null,"address":"AA:.."|null}`: the keys
 *   and meaning of calictl's /api/pairing snapshot, the pairing state machine's state. With no
 *   pairing flow since boot and a stored bond it reads "idle" with the bond's address (as calictl
 *   does); "bonded" only right after a pairing in this boot. STATE never says whether the link is
 *   up (the session's concern, by design). The `status` command's line, once the WiFi runtime
 *   booted, ends with `,"wifi":{"mode":"setup"|"station"|"off","ssid":"…"|null,"ip":"a.b.c.d"|null}`
 *   (pairing-change STATE lines never carry it).
 * - `SNAP {"t":<uptime_ms>,"fn":{"<function>":{"<Field>":<int>,...},...}}`: every function the
 *   session holds a frame for, in CODEC_CHARS order, decoded with codec_decode (fields past a
 *   short frame's end are absent).
 * - `LOG <text>`: a line from cali_log.
 */
#ifndef CALI_CONSOLE_H
#define CALI_CONSOLE_H

#include <stdint.h>

#include "cali_pairing_sm.h"
#include "cali_transport.h"

#ifdef __cplusplus
extern "C" {
#endif

/**
 * @brief Wires the console, the runner and the session together on a transport.
 *
 * Calls cali_runner_init(t) and cali_session_init(t), and points cali_runner_on_state at a hook
 * that prints STATE and starts or stops the session (bonded: cali_session_on_bonded; anything
 * else: cali_session_stop).
 *
 * @param t The BLE transport.
 */
void cali_console_init(const cali_transport_t *t);

/**
 * @brief Runs one console command line (see @ref console_input).
 * @param line The line, NUL-terminated, without the newline.
 */
void cali_console_line(const char *line);

/**
 * @brief Prints a STATE line.
 * @param s       The pairing state.
 * @param address The bond's address, or NULL for null.
 */
void cali_console_state(const cali_pair_state_t *s, const char *address);

/**
 * @brief Prints a SNAP line from the session snapshot.
 * @param t_ms The uptime in milliseconds.
 */
void cali_console_snapshot(uint64_t t_ms);

/** Called on "quit". NULL = ignored. */
extern void (*cali_console_on_quit)(void);

#ifdef __cplusplus
}
#endif

#endif /* CALI_CONSOLE_H */
