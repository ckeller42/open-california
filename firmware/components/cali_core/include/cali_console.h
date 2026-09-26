/* cali_console.h — the firmware's line protocol (#154), identical on the host build (stdin/stdout)
 * and the ESP32 (UART).
 *
 * Input, one command per line (surrounding whitespace ignored):
 *   pair         start pairing (runner EV_START; ignored by the SM unless idle/error)
 *   passkey N    the code the unit displays, 1-6 digits; ignored unless the runner waits for it
 *   forget       drop the bond (runner EV_RESET)
 *   status       print the current STATE line
 *   quit         call cali_console_on_quit (the host exits; NULL hook = ignored)
 * anything else -> "LOG unknown command: <line>".
 *
 * Output (stdout, one line each, flushed):
 *   STATE {"state":"<name>","attempts":N,"error":"<name>"|null,"address":"AA:.."|null}
 *       the keys of calictl's /api/pairing snapshot. While the runner is idle but the session
 *       holds a stored bond (after a boot with a bond), the state reads "bonded" with that address.
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

void cali_console_line(const char *line);           /* pair | passkey N | forget | status | quit */
void cali_console_state(const cali_pair_state_t *s, const char *address);   /* prints STATE {...} */
void cali_console_snapshot(uint64_t t_ms);          /* prints SNAP {...} from the session snapshot */

/* Called on "quit". NULL = ignored. */
extern void (*cali_console_on_quit)(void);

#ifdef __cplusplus
}
#endif

#endif /* CALI_CONSOLE_H */
