/* cali_console.h — the firmware's line protocol (#154), identical on the host build (stdin/stdout)
 * and the ESP32 (UART).
 *
 * Input, one command per line (surrounding whitespace ignored):
 *   pair         start pairing (runner EV_START; ignored by the SM unless idle/error)
 *   passkey N    the code the unit displays, 1-6 digits; ignored unless the runner waits for it
 *   forget       drop the bond (runner EV_RESET)
 *   status       print the current STATE line (+ a "wifi" member once the WiFi runtime runs)
 *   quit         call cali_console_on_quit (the host exits; NULL hook = ignored)
 *   wifi set <ssid> <psk>   store the credentials (kv "wifi_ssid"/"wifi_psk", like POST /api/wifi)
 *                and hand them to the WiFi runner. Both are single tokens: an SSID or a password
 *                with spaces cannot be typed here (use WiFi setup). SSID 1..NET_SSID_MAX bytes, PSK
 *                NET_PSK_MIN..NET_PSK_MAX; else "LOG wifi: usage: wifi set <ssid> <psk>" / "LOG
 *                wifi: bad ssid" / "LOG wifi: bad psk". While online/retrying the new creds replace
 *                the old ones ("LOG wifi: credentials replaced, reconnecting"). The line buffers
 *                are wiped after the command (it held the passphrase; on the ESP the console
 *                queue's slot keeps its copy until a later line reuses it); the PSK is never printed.
 *   wifi status  "LOG wifi: <setup|station|off> ssid=<ssid|-> ip=<a.b.c.d|-> rssi=<dBm|-> scan=<n>"
 *                (mode as /api/wifi's; scan = networks in the last scan result)
 *   wifi forget  drop the WiFi credentials and reopen the setup hotspot (cali_wifi_run_forget)
 *   wifi scan    ask for a WiFi scan (held back while BLE pairs; cali_wifi_run.h)
 *   set <fn> <what> [value]   a control command (cali_control.h): the same builders, gates and
 *                allow-list as POST /api/command, in any WiFi mode (the console is physical
 *                access); the value is the rest of the line (none = JSON null); outcome on
 *                "LOG control: …"
 *   sensors      "SENSORS <the GET /api/sensors object>" (cali_sensors.h, R_FW_SENSORS)
 *   sensors on|off   the exp_sensors flag (cali_exp_sensors_set: "LOG exp: sensors on|off")
 *   sensors add <id> [alias|-] [keyhex|-] / sensors remove <id>   "LOG sensors ok" | "LOG sensors err <code>"
 *   pi wake      one manual Pi wake pulse (cali_pi_wake.h, R_FW_PI_WAKE): "LOG pi-wake[manual]: GPIO9
 *                low 200 ms", or "LOG pi-wake: busy" while a pulse is in progress
 *   pi hold <ms> the wiring test: GPIO9 low for 1..5000 ms (else "LOG pi-wake: usage: …")
 *   pi halt [ms] one manual Pi halt pulse (R_FW_PI_HALT): GPIO8 low 500 ms (or 1..5000 ms: the
 *                wiring test, read `pinctrl get 17` on a Pi without the gpio-shutdown overlay):
 *                "LOG pi-halt[manual]: GPIO8 low <ms> ms", or "LOG pi-halt: busy" while either line is low
 *   pi timer <min>  halt after parking, 0..1440 min (0 off; cali_ph_set_min)
 *   pi guard <pct>  the battery guard, 0..100 % (0 off; cali_ph_set_soc)
 *   pi status    "LOG pi-wake: {"pi_wake":<the /api/state device.pi_wake object>}"
 *   ble          "LOG ble: {"ble":<the /api/state device.ble object>}" (cali_ble_exp.h, R_FW_BLE_MODE)
 *   ble mode hold|quiet|off   the BLE link mode ("LOG session: ble mode <m> (console)"): hold = link +
 *                1003 heartbeat, quiet = link, heartbeat only through a read-all, off = no link
 *   ble probe    one fresh read-all now (R_FW_BLE_PROBE), then back to the mode; "LOG ble: probe refused"
 *                with no bond or a probe running
 *   ble exp <min> [HH-HH]   the night experiment (R_FW_BLE_EXP): hold -> quiet -> off, <min> (1..720) each,
 *                inside the Berlin-time window (default 22-06); "LOG ble-exp: refused (no time)" without a
 *                wall clock, "LOG usage: ble exp <1-720 min> [HH-HH] | ble exp off" for bad arguments
 *   ble exp off  end it, back to hold
 *   Before the WiFi runtime booted (host without --http) every "wifi …" line only prints
 *   "LOG wifi: not enabled".
 * anything else -> "LOG unknown command: <first word>" ("wifi <subcommand>" for an unknown wifi
 *   subcommand): never the rest of the line, which a mistyped "wifi set" fills with the passphrase.
 *   Lines up to 127 bytes (both line readers: LINE_MAX_LEN 128 in host_main.c / app_main.c).
 *
 * Output (stdout, one line each, flushed):
 *   STATE {"state":"<name>","attempts":N,"error":"<name>"|null,"address":"AA:.."|null}
 *       the keys and meaning of calictl's /api/pairing snapshot: the PAIRING state machine's
 *       state. With no pairing flow since boot and a stored bond it reads "idle" with the bond's
 *       address (as calictl does); "bonded" only right after a pairing in this boot. STATE never
 *       says whether the link is up (the session's concern, by design). The "status" command's
 *       line, once the WiFi runtime booted, ends with
 *       ,"wifi":{"mode":"setup"|"station"|"off","ssid":"…"|null,"ip":"a.b.c.d"|null}
 *       (pairing-change STATE lines never carry it).
 *   SNAP {"t":<uptime_ms>,"fn":{"<function>":{"<Field>":<int>,...},...}}
 *       every function the session holds a frame for, in CODEC_CHARS order, decoded with
 *       codec_decode (fields past a short frame's end are absent).
 *   LOG <text>   (cali_log)
 *
 * Call everything on the task that owns the transport (the NimBLE host task).
 */
#ifndef CALI_CONSOLE_H
#define CALI_CONSOLE_H

#include <stdint.h>

#include "cali_pairing_sm.h"
#include "cali_transport.h"

#ifdef __cplusplus
extern "C" {
#endif

/* Wires the console, the runner and the session together on transport t: cali_runner_init(t),
 * cali_session_init(t), and points cali_runner_on_state at a hook that prints STATE and starts /
 * stops the session (bonded -> cali_session_on_bonded, anything else -> cali_session_stop). */
void cali_console_init(const cali_transport_t *t);

void cali_console_line(const char *line);           /* pair | passkey N | forget | status | quit | wifi … | set … */
void cali_console_state(const cali_pair_state_t *s, const char *address);   /* prints STATE {...} */
void cali_console_snapshot(uint64_t t_ms);          /* prints SNAP {...} from the session snapshot */

/* Called on "quit". NULL = ignored. */
extern void (*cali_console_on_quit)(void);

#ifdef __cplusplus
}
#endif

#endif /* CALI_CONSOLE_H */
