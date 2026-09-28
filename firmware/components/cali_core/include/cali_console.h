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
 *                with spaces cannot be typed here (use the setup page). SSID 1..NET_SSID_MAX bytes, PSK
 *                NET_PSK_MIN..NET_PSK_MAX; else "LOG wifi: usage: wifi set <ssid> <psk>" / "LOG
 *                wifi: bad ssid" / "LOG wifi: bad psk". While online/retrying the new creds replace
 *                the old ones ("LOG wifi: credentials replaced, reconnecting"). The line buffers
 *                are wiped after the command (it held the passphrase; on the ESP the console
 *                queue's slot keeps its copy until a later line reuses it); the PSK is never printed.
 *   wifi status  "LOG wifi: <setup|station|off> ssid=<ssid|-> ip=<a.b.c.d|-> rssi=<dBm|-> scan=<n>"
 *                (mode as /api/wifi's; scan = networks in the last scan result)
 *   wifi forget  drop the WiFi credentials and reopen the setup hotspot (cali_wifi_run_forget)
 *   wifi scan    ask for a WiFi scan (held back while BLE pairs; cali_wifi_run.h)
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

void cali_console_line(const char *line);           /* pair | passkey N | forget | status | quit | wifi … */
void cali_console_state(const cali_pair_state_t *s, const char *address);   /* prints STATE {...} */
void cali_console_snapshot(uint64_t t_ms);          /* prints SNAP {...} from the session snapshot */

/* Called on "quit". NULL = ignored. */
extern void (*cali_console_on_quit)(void);

#ifdef __cplusplus
}
#endif

#endif /* CALI_CONSOLE_H */
