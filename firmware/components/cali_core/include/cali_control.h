/* cali_control.h — the satellite's control path (#154 B): calictl.control's builders + gates in C
 * (control.c, pure) and the one-command-at-a-time sequencer onto the session's link (control_run.c).
 *
 * Plan (control.c): cali_ctl_plan() answers what calictl would for "set <fn> <what> <value>" over the
 * decoded state behind get(): ELSEWHERE for the roof and any function but the five in
 * control_consts.h; `lighting wakeup` without a `local_now` is ELSEWHERE with CALI_REASON_WAKEUP_CLOCK
 * (the page is the ESP's clock: its wall clock read as UTC, seconds), else REFUSED with command_precondition's
 * text, else BAD_VALUE / NONE (control.build raised / returned None), else OK with the writes in
 * calictl.device.actuate's order (preface, commit, frame, commit; commits only for lighting, each
 * at least CODEC_FOLLOW_DELAY_MS after the previous write's ACK). value is calictl's string form of the JSON
 * value (an integer's decimal text; null = ""), shorter than CALI_CTL_VALUE_MAX. Held equal to Python
 * by tests/vectors/control.json (tests/firmware/test_control_parity.py).
 *
 * The write allow-list (cali_ctl_write_ok): a characteristic write is allowed only to one of the five
 * control chars of CALI_CTL_CHARS at exactly its frame length — never the roof's control char. The
 * 1003 heartbeat is the transport's own write_heartbeat. Both control_run.c (before every write) and
 * the NimBLE transport's write() (for any caller) call it: the single choke point.
 *
 * Run (control_run.c, on the owner task like everything in cali_core): cali_ctl_submit() refuses at
 * once (BUSY, NOT_READY, REFUSED, ELSEWHERE, BAD_VALUE, NONE — each logged "control: <fn>/<what> …")
 * or accepts (PENDING); the frames then go out on cali_ctl_tick(), one write with response at a time
 * (transport write -> CALI_TEV_WRITTEN -> cali_ctl_on_written); done(result, reason) is called exactly
 * once with OK, FAILED (a write not issued, refused with an ATT error, or the link lost), TIMEOUT
 * (CALI_CTL_DEADLINE_MS after the submit) or REFUSED (a wake-up whose config the pull did not bring,
 * reason CALI_WAKEUP_UNKNOWN; reason is NULL for every other result). A `plan.pull` refusal on an armed
 * link becomes PENDING: REQUEST_CONFIG + commit go out, then up to CODEC_CONFIG_PULL_MS after the
 * commit's ACK the command is planned again every tick over the unit's reply frames (calictl
 * serve._pull_lighting_config), then written or refused — one deadline, CALI_CTL_DEADLINE_MS +
 * CODEC_CONFIG_PULL_MS, under the HTTP core's CALI_HTTP_PENDING_MAX_MS. local_now: the page's wall clock
 * read as UTC (s), < 0 = none (the console). NOT_READY = cali_session_ready() is 0 (no armed link: the
 * link up for CODEC_ARM_DELAY_MS with its first read-all done) or the function's state was not read
 * (or pushed) on this link. ELSEWHERE, NONE and BAD_VALUE answer without waiting for it, armed or not.
 *
 * C99, no malloc, no NimBLE/ESP-IDF includes.
 */
#ifndef CALI_CONTROL_H
#define CALI_CONTROL_H

#include <stddef.h>
#include <stdint.h>

#include "cali_transport.h"
#include "codec.h"

#ifdef __cplusplus
extern "C" {
#endif

enum {
    CALI_CTL_OK = 0,      /* plan: frames to write / run: every frame written and ACKed */
    CALI_CTL_REFUSED,     /* a command_precondition gate; reason = its text */
    CALI_CTL_ELSEWHERE,   /* not on the ESP; reason = CALI_REASON_ELSEWHERE */
    CALI_CTL_BAD_VALUE,   /* control.build raised */
    CALI_CTL_NONE,        /* control.build returned None */
    CALI_CTL_PENDING,     /* run: accepted, frames going out */
    CALI_CTL_BUSY,        /* run: a command (or an unacknowledged write) is still in flight */
    CALI_CTL_NOT_READY,   /* run: no armed link yet, or the function's state is unknown */
    CALI_CTL_FAILED,      /* run: a write not issued, refused by the unit, or the link lost */
    CALI_CTL_TIMEOUT      /* run: not done within CALI_CTL_DEADLINE_MS */
};

#define CALI_CTL_MAX_FRAMES 4
#define CALI_CTL_VALUE_MAX 64
#define CALI_CTL_DEADLINE_MS 4000u
#define CALI_CTL_TICK_MS 100u   /* the session tick period (TICK_MS in host_main.c / app_main.c) */

typedef struct {
    uint16_t chr;       /* control char short id */
    uint16_t delay_ms;  /* after the previous write's ACK */
    uint8_t len;
    uint8_t data[CODEC_FRAME_MAX];
} cali_ctl_frame_t;

typedef struct {
    int rc;
    const char *reason;  /* REFUSED / ELSEWHERE: static text, else NULL */
    int pull;            /* 1 = REFUSED only for want of the unit's lighting config (reason CALI_WAKEUP_UNKNOWN): the
                          * sequencer pulls it with REQUEST_CONFIG and plans again */
    size_t n;
    cali_ctl_frame_t f[CALI_CTL_MAX_FRAMES];
} cali_ctl_plan_t;

/* 1 and *out = the decoded value of fn's state field, or 0 when unknown (no frame, or the frame
 * too short for the field). */
typedef int (*cali_ctl_get_t)(const char *fn, const char *field, uint32_t *out);
typedef void (*cali_ctl_done_t)(int result, const char *reason);   /* reason: REFUSED's text, else NULL */

/* The lighting configuration the unit reports in its own 1502 frames — semantics.lighting_config in C:
 * Mode 20 = wake-up (Timestamp + LightValue), Mode 16 / ProfileNumber 8 = door contact (LightValue),
 * Mode 12 with ProfileNumber != 13 (13 = the app's own REQUEST echo) = stored favourites (LightValue
 * & 0x7f), Mode 4 with ProfileNumber 1-7 adds that favourite's bit once the bits are known.
 * cali_light_cfg(get, out): the keys get() already answers (a latch carried over), then this frame's
 * own config. Held equal to Python by T_FW_LIGHT_CFG_PARITY. */
enum { CALI_LCFG_WAKE_TS, CALI_LCFG_WAKE_LV, CALI_LCFG_DOOR, CALI_LCFG_FAVS, CALI_LCFG_N };
extern const char *const CALI_LCFG_KEYS[CALI_LCFG_N];   /* semantics.LIGHT_CONFIG_KEYS, same order */
typedef struct {
    unsigned have;   /* bit k: v[k] known */
    uint32_t v[CALI_LCFG_N];
} cali_light_cfg_t;
void cali_light_cfg(cali_ctl_get_t get, cali_light_cfg_t *out);   /* = semantics.lighting_config(None, state) */

void cali_ctl_plan(const char *fn, const char *what, const char *value, int64_t local_now,
                   cali_ctl_get_t get, cali_ctl_plan_t *out);   /* local_now < 0 = none */
void cali_ctl_pull_plan(cali_ctl_plan_t *out);   /* REQUEST_CONFIG @0, commit @CODEC_FOLLOW_DELAY_MS */
int cali_ctl_write_ok(uint16_t chr, size_t len);

void cali_ctl_run_init(const cali_transport_t *t);
int cali_ctl_submit(const char *fn, const char *what, const char *value, int64_t local_now,
                    cali_ctl_done_t done, const char **reason);
void cali_ctl_tick(uint64_t now_ms);
int cali_ctl_busy(void);   /* 1 while a command runs or a write awaits its ACK (submit would say BUSY) */
void cali_ctl_on_written(const cali_tevent_t *e);

#ifdef __cplusplus
}
#endif

#endif /* CALI_CONTROL_H */
