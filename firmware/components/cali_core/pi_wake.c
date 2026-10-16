/* pi_wake.c — the satellite wakes the halted Raspberry Pi (R_FW_PI_WAKE, #387). Contract:
 * include/cali_pi_wake.h. Proven by tests/firmware/test_web_handlers.py (the edge rule, the endpoints)
 * and tests/firmware/test_pi_wake_e2e.py (host tier: the fake unit's ignition -> the pulse).
 */
#include "cali_pi_wake.h"

#include <string.h>

#include "cali_auto_camper.h"
#include "cali_diag.h"
#include "cali_platform.h"
#include "cali_session.h"

static int s_on = 1, s_prev = -1, s_low, s_auto_ever, s_ever;
static uint64_t s_release_at, s_auto_at, s_at;
static const char *s_reason;

void cali_pw_init(void) {
    uint8_t b;
    size_t n = 1;
    if (cali_kv_get(CALI_PW_ON_KEY, &b, &n) == CALI_KV_OK && n == 1) s_on = b != 0;
    cali_pi_wake_line(0);   /* configure the line, released */
    cali_log("pi-wake: %s", s_on ? "on" : "off");
}

int cali_pw_enabled(void) { return s_on; }

int cali_pw_set_enabled(int on) {
    uint8_t b = (uint8_t)(on != 0);
    if (cali_kv_set(CALI_PW_ON_KEY, &b, 1) != 0) return -1;
    s_on = b;
    cali_log("pi-wake: %s", s_on ? "ENABLED" : "disabled");
    return 0;
}

int cali_pw_pulse(const char *reason, uint32_t ms) {
    uint64_t now = cali_uptime_ms();
    if (s_low || ms < 1 || ms > CALI_PW_HOLD_MAX_MS) return -1;
    cali_pi_wake_line(1);
    s_low = 1;
    s_release_at = now + ms;
    s_at = now;
    s_ever = 1;
    s_reason = strcmp(reason, "ignition") == 0 ? "ignition" : "manual";
    cali_log("pi-wake[%s]: GPIO9 low %u ms", s_reason, (unsigned)ms);
    cali_diag_evt("pi_wake", "\"reason\":\"%s\"", s_reason);
    return 0;
}

int cali_pw_wake(const char *reason) { return cali_pw_pulse(reason, CALI_PW_PULSE_MS); }

void cali_pw_step(int ign, uint64_t now_ms) {
    int edge;
    if (ign < 0) return;   /* not read on this link: the previous reading stands */
    edge = ign == 1 && s_prev != 1;
    s_prev = ign;
    if (!edge || !s_on) return;   /* off: the edges stay tracked, so a later enable won't misfire */
    if (s_auto_ever && now_ms - s_auto_at < CALI_PW_MIN_GAP_MS) {
        cali_log("pi-wake: ignition edge within %u s of the last, no pulse", CALI_PW_MIN_GAP_MS / 1000u);
        return;
    }
    if (cali_pw_wake("ignition") == 0) {
        s_auto_ever = 1;
        s_auto_at = now_ms;
    }
}

void cali_pw_tick(uint64_t now_ms) {
    if (s_low && now_ms >= s_release_at) {
        cali_pi_wake_line(0);
        s_low = 0;
    }
    if (cali_session_ready()) cali_pw_step(cali_ac_ignition(), now_ms);
}

void cali_pw_json(cali_json_t *j) {
    cali_json_obj_begin(j);
    cali_json_key(j, "enabled");
    cali_json_bool(j, s_on);
    cali_json_key(j, "last_s");
    if (s_ever) cali_json_int(j, (long long)((cali_uptime_ms() - s_at) / 1000u));
    else cali_json_null(j);
    cali_json_key(j, "reason");
    if (s_ever) cali_json_str(j, s_reason);
    else cali_json_null(j);
    cali_json_obj_end(j);
}
