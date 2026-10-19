/* pi_wake.c — the satellite wakes the halted Raspberry Pi (R_FW_PI_WAKE, #387) and halts it
 * (R_FW_PI_HALT). Contract: include/cali_pi_wake.h. Proven by tests/firmware/test_web_handlers.py
 * (the edge rule, the parking timer, the battery guard, the endpoints) and
 * tests/firmware/test_pi_wake_e2e.py (host tier: the fake unit's ignition -> the pulse).
 */
#include "cali_pi_wake.h"

#include <string.h>

#include "cali_auto_camper.h"
#include "cali_diag.h"
#include "cali_platform.h"
#include "cali_session.h"
#include "codec.h"
#include "codec_chars.h"

static int s_on = 1, s_prev = -1, s_low, s_auto_ever, s_ever;
static uint64_t s_release_at, s_auto_at, s_at;
static const char *s_reason;
/* the halt: settings, the parking timer, the GPIO8 pulse */
static unsigned s_ph_min = CALI_PH_MIN_DEFAULT, s_ph_soc = CALI_PH_SOC_DEFAULT;
static int s_driving, s_halted, s_hlow, s_hever, s_batt;
static uint64_t s_parked_at, s_hrelease_at, s_hat;
static const char *s_hreason;

void cali_pw_init(void) {
    uint8_t b[2];
    size_t n = 1;
    if (cali_kv_get(CALI_PW_ON_KEY, b, &n) == CALI_KV_OK && n == 1) s_on = b[0] != 0;
    n = 2;
    if (cali_kv_get(CALI_PH_MIN_KEY, b, &n) == CALI_KV_OK && n == 2 && (unsigned)(b[0] | b[1] << 8) <= CALI_PH_MIN_MAX)
        s_ph_min = (unsigned)(b[0] | b[1] << 8);
    n = 1;
    if (cali_kv_get(CALI_PH_SOC_KEY, b, &n) == CALI_KV_OK && n == 1 && b[0] <= 100) s_ph_soc = b[0];
    cali_pi_wake_line(0);   /* configure the lines, released */
    cali_pi_halt_line(0);
    s_parked_at = cali_uptime_ms();   /* a boot starts the parking timer */
    cali_log("pi-wake: %s", s_on ? "on" : "off");
    cali_log("pi-halt: after %u min parked, battery guard %u %%", s_ph_min, s_ph_soc);
}

int cali_pw_enabled(void) { return s_on; }

int cali_pw_set_enabled(int on) {
    uint8_t b = (uint8_t)(on != 0);
    if (cali_kv_set(CALI_PW_ON_KEY, &b, 1) != 0) return -1;
    s_on = b;
    cali_log("pi-wake: %s", s_on ? "ENABLED" : "disabled");
    return 0;
}

int cali_ph_min(void) { return (int)s_ph_min; }
int cali_ph_soc(void) { return (int)s_ph_soc; }

int cali_ph_set_min(unsigned min) {
    uint8_t b[2] = {(uint8_t)(min & 0xffu), (uint8_t)(min >> 8)};
    if (min > CALI_PH_MIN_MAX || cali_kv_set(CALI_PH_MIN_KEY, b, 2) != 0) return -1;
    s_ph_min = min;
    cali_log("pi-halt: after %u min parked", min);
    return 0;
}

int cali_ph_set_soc(unsigned pct) {
    uint8_t b = (uint8_t)pct;
    if (pct > 100 || cali_kv_set(CALI_PH_SOC_KEY, &b, 1) != 0) return -1;
    s_ph_soc = pct;
    cali_log("pi-halt: battery guard %u %%", pct);
    return 0;
}

int cali_pw_pulse(const char *reason, uint32_t ms) {
    uint64_t now = cali_uptime_ms();
    if (s_low || s_hlow || ms < 1 || ms > CALI_PW_HOLD_MAX_MS) return -1;
    cali_pi_wake_line(1);
    s_low = 1;
    s_release_at = now + ms;
    s_at = now;
    s_ever = 1;
    s_parked_at = now;   /* a woken Pi gets the full parking time again */
    s_halted = 0;
    s_reason = strcmp(reason, "ignition") == 0 ? "ignition" : "manual";
    cali_log("pi-wake[%s]: GPIO9 low %u ms", s_reason, (unsigned)ms);
    cali_diag_evt("pi_wake", "\"reason\":\"%s\"", s_reason);
    return 0;
}

int cali_pw_wake(const char *reason) { return cali_pw_pulse(reason, CALI_PW_PULSE_MS); }

int cali_ph_pulse(const char *reason, uint32_t ms) {
    uint64_t now = cali_uptime_ms();
    if (s_low || s_hlow || ms < 1 || ms > CALI_PW_HOLD_MAX_MS) return -1;
    cali_pi_halt_line(1);
    s_hlow = 1;
    s_hrelease_at = now + ms;
    s_hat = now;
    s_hever = 1;
    s_halted = 1;
    s_hreason = strcmp(reason, "timer") == 0 ? "timer" : strcmp(reason, "battery") == 0 ? "battery" : "manual";
    cali_log("pi-halt[%s]: GPIO8 low %u ms", s_hreason, (unsigned)ms);
    cali_diag_evt("pi_halt", "\"reason\":\"%s\"", s_hreason);
    return 0;
}

int cali_ph_halt(const char *reason) { return cali_ph_pulse(reason, CALI_PH_PULSE_MS); }

int cali_pw_batt_low(long sleep_warning, long warn_two, long soc_level, int soc_pct) {
    return sleep_warning == 1 || warn_two >= 1 || (soc_level >= 1 && soc_level <= 10 && soc_level * 10 <= soc_pct);
}

/* From the latest energy frame the session holds (any link: a parked unit sleeps); none = not low. */
static int batt_low_now(void) {
    static codec_kv_t e[CODEC_KV_MAX];   /* static: the 8 KB host-task stack */
    const uint8_t *frame;
    size_t len;
    long sw = -1, w2 = -1, lvl = -1;
    int n = 0;
    for (size_t i = 0; i < CODEC_NCHARS; i++)
        if (strcmp(CODEC_CHARS[i].function, "energy") == 0 && cali_session_frame(i, &frame, &len))
            n = codec_decode(codec_func_by_name("energy"), frame, len, e);
    for (int k = 0; k < n; k++) {
        if (strcmp(e[k].name, "SleepWarning") == 0) sw = (long)e[k].value;
        else if (strcmp(e[k].name, "WarningLevelTwo") == 0) w2 = (long)e[k].value;
        else if (strcmp(e[k].name, "SocTwoBattAfs") == 0) lvl = (long)e[k].value;
    }
    return cali_pw_batt_low(sw, w2, lvl, (int)s_ph_soc);
}

/* The halt after parking in minutes, the battery guard applied; 0 = no automatic halt. */
static unsigned limit_min(void) {
    if (s_batt && s_ph_soc && (s_ph_min == 0 || s_ph_min > CALI_PH_GUARD_MIN)) return CALI_PH_GUARD_MIN;
    return s_ph_min;
}

void cali_pw_step(int ign, uint64_t now_ms) {
    int edge = ign == 1 && s_prev != 1;
    unsigned lim;
    if (ign == 1) {
        s_driving = 1;   /* no halt while driving; the next park starts afresh */
        s_halted = 0;
    } else if (ign == 0 && s_prev != 0) {
        s_driving = 0;
        s_parked_at = now_ms;
    }   /* unknown (no link, the unit asleep): the state stands, a parked van stays parked */
    if (ign >= 0) s_prev = ign;
    s_batt = batt_low_now();
    if (edge && s_on) {   /* off: the edges stay tracked, so a later enable won't misfire */
        if (s_auto_ever && now_ms - s_auto_at < CALI_PW_MIN_GAP_MS) {
            cali_log("pi-wake: ignition edge within %u s of the last, no pulse", CALI_PW_MIN_GAP_MS / 1000u);
        } else if (cali_pw_wake("ignition") == 0) {
            s_auto_ever = 1;
            s_auto_at = now_ms;
        }
    }
    lim = limit_min();
    if (!s_driving && !s_halted && lim && now_ms - s_parked_at >= (uint64_t)lim * CALI_PH_MINUTE_MS)
        (void)cali_ph_halt(lim != s_ph_min ? "battery" : "timer");   /* busy: again next step */
}

void cali_pw_tick(uint64_t now_ms) {
    if (s_low && now_ms >= s_release_at) {
        cali_pi_wake_line(0);
        s_low = 0;
    }
    if (s_hlow && now_ms >= s_hrelease_at) {
        cali_pi_halt_line(0);
        s_hlow = 0;
    }
    cali_pw_step(cali_session_ready() ? cali_ac_ignition() : -1, now_ms);
}

void cali_pw_json(cali_json_t *j) {
    uint64_t now = cali_uptime_ms(), due;
    unsigned lim = limit_min();
    cali_json_obj_begin(j);
    cali_json_key(j, "enabled");
    cali_json_bool(j, s_on);
    cali_json_key(j, "last_s");
    if (s_ever) cali_json_int(j, (long long)((now - s_at) / 1000u));
    else cali_json_null(j);
    cali_json_key(j, "reason");
    if (s_ever) cali_json_str(j, s_reason);
    else cali_json_null(j);
    cali_json_key(j, "halt_after_min");
    cali_json_int(j, s_ph_min);
    cali_json_key(j, "halt_soc_pct");
    cali_json_int(j, s_ph_soc);
    cali_json_key(j, "halt_in_s");
    if (s_driving || s_halted || !lim) {
        cali_json_null(j);
    } else {
        due = s_parked_at + (uint64_t)lim * CALI_PH_MINUTE_MS;
        cali_json_int(j, (long long)(due > now ? (due - now + 999u) / 1000u : 0));
    }
    cali_json_key(j, "halted");
    cali_json_bool(j, s_halted);
    cali_json_key(j, "halt_last_s");
    if (s_hever) cali_json_int(j, (long long)((now - s_hat) / 1000u));
    else cali_json_null(j);
    cali_json_key(j, "halt_reason");
    if (s_hever) cali_json_str(j, s_hreason);
    else cali_json_null(j);
    cali_json_key(j, "batt_low");
    cali_json_bool(j, s_batt);
    cali_json_obj_end(j);
}
