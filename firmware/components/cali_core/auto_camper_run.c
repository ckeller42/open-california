/* auto_camper_run.c — calictl.automation.AutoCamper on the satellite (R_FW_AUTO_CAMPER). Contract:
 * include/cali_auto_camper.h. Proven end to end by tests/firmware/test_auto_camper_e2e.py.
 *
 * Inputs per step, from this link's frames (cali_session_frame_live), as AutoCamper.step reads them:
 * ignition = vehicle TerminalOneFive, OR campingmode Enable (the unit's own terminal-15 mirror: the
 * shed's 1202 push carries Enable=1, so a step that lands between the shed and the 1004 push still
 * sees the ignition on and arms, never a "manual off"); camping master/usb/lights as
 * semantics.campingmode; soc = SocTwoBattAfs x 10 for 0-10 (soc2_pct); warning = SleepWarning or
 * WarningLevelActive or WarningLevelTwo >= 1. A function not read on this link is unknown (-1).
 *
 * Persisted debt (CALI_AC_STATE_KEY, 10 bytes, written only when it changes): owe, pre, pre_usb,
 * pre_lights, restoring (a retry window open), fails, prev ign/master/usb/lights (+1: 0 = None).
 * serve keeps a wall-clock deadline; the ESP may boot with no wall clock (no SNTP, no 1004 yet), so a reboot inside the window
 * opens a fresh window at the first step (fails carried over, so the tries stay bounded).
 */
#include "cali_auto_camper.h"

#include <stdio.h>
#include <string.h>

#include "cali_control.h"
#include "cali_diag.h"
#include "cali_platform.h"
#include "cali_session.h"
#include "codec.h"
#include "codec_chars.h"

#define STATE_LEN 10

static cali_ac_state_t S = {0, 0, 0, 0, -1, 0, -1, -1, -1, -1};
static int s_on = 1, s_resume;
static uint32_t s_step_ms = CALI_AC_STEP_MS;
static uint64_t s_next;
static uint8_t s_saved[STATE_LEN];
/* the restore: campingmode master on, then usb / lights on, one command at a time */
static const char *s_chain[3];
static int s_n, s_i, s_wait;
static uint64_t s_until, s_now;

/* fields[] of fn's live frame: the count, 0 when not read on this link */
static int frame_fields(const char *fn, codec_kv_t kv[CODEC_KV_MAX]) {
    const uint8_t *frame;
    size_t len;
    for (size_t i = 0; i < CODEC_NCHARS; i++)
        if (strcmp(CODEC_CHARS[i].function, fn) == 0)
            return cali_session_frame_live(i, &frame, &len) ? codec_decode(codec_func_by_name(fn), frame, len, kv) : 0;
    return 0;
}

static long field(const codec_kv_t *kv, int n, const char *name) {
    for (int k = 0; k < n; k++)
        if (strcmp(kv[k].name, name) == 0) return (long)kv[k].value;
    return -1;
}

int cali_ac_ignition(void) {
    static codec_kv_t v[CODEC_KV_MAX], c[CODEC_KV_MAX];   /* static: the 8 KB host-task stack */
    int nv = frame_fields("vehicle", v), nc = frame_fields("campingmode", c);
    long t15 = field(v, nv, "TerminalOneFive"), en = field(c, nc, "Enable");
    return t15 < 0 && en < 0 ? -1 : t15 > 0 || en > 0;
}

int cali_ac_owed(void) { return S.owe != 0; }

static void pack(uint8_t b[STATE_LEN]) {
    b[0] = (uint8_t)S.owe;
    b[1] = (uint8_t)S.pre;
    b[2] = (uint8_t)S.pre_usb;
    b[3] = (uint8_t)S.pre_lights;
    b[4] = (uint8_t)(S.until >= 0 || s_resume);
    b[5] = (uint8_t)(S.fails > 255 ? 255 : S.fails);
    b[6] = (uint8_t)(S.prev_ign + 1);
    b[7] = (uint8_t)(S.prev_master + 1);
    b[8] = (uint8_t)(S.prev_usb + 1);
    b[9] = (uint8_t)(S.prev_lights + 1);
}

static int save(void) {
    uint8_t b[STATE_LEN];
    pack(b);
    if (memcmp(b, s_saved, sizeof b) == 0) return 0;
    if (cali_kv_set(CALI_AC_STATE_KEY, b, sizeof b) != 0) {
        cali_log("auto-camper: state not saved");
        return -1;
    }
    memcpy(s_saved, b, sizeof b);
    return 0;
}

void cali_ac_init(void) {
    uint8_t b[STATE_LEN];
    size_t n = 1;
    if (cali_kv_get(CALI_AC_ON_KEY, b, &n) == CALI_KV_OK && n == 1) s_on = b[0] != 0;
    n = sizeof b;
    if (cali_kv_get(CALI_AC_STATE_KEY, b, &n) == CALI_KV_OK && n == sizeof b) {
        S.owe = b[0];
        S.pre = b[1];
        S.pre_usb = b[2];
        S.pre_lights = b[3];
        s_resume = b[4] && b[0];
        S.until = -1;
        S.fails = b[5];
        S.prev_ign = b[6] - 1;
        S.prev_master = b[7] - 1;
        S.prev_usb = b[8] - 1;
        S.prev_lights = b[9] - 1;
        memcpy(s_saved, b, sizeof b);
    }
    cali_log("auto-camper: %s%s", s_on ? "on" : "off", S.owe ? " (restore owed)" : "");
}

int cali_ac_enabled(void) { return s_on; }

int cali_ac_set_enabled(int on) {
    uint8_t b = (uint8_t)(on != 0);
    /* AutoCamper.set_enabled: off clears the debt; so does off -> on, in case an earlier off's
     * state write failed and a stale debt is still stored */
    if (!b || !s_on) {
        S.owe = 0;
        S.until = -1;
        S.fails = 0;
        s_resume = 0;
        s_n = 0;
    }
    s_on = b;
    cali_log("auto-camper: %s", s_on ? "ENABLED" : "disabled");
    if (save() != 0) return -1;   /* the debt is not cleared in NVS: don't store the toggle either */
    return cali_kv_set(CALI_AC_ON_KEY, &b, 1);
}

void cali_ac_set_step_ms(uint32_t ms) {
    s_step_ms = ms;
    s_next = 0;
}

void cali_ac_json(cali_json_t *j) {
    cali_json_obj_begin(j);
    cali_json_key(j, "enabled");
    cali_json_bool(j, s_on);
    cali_json_key(j, "state");
    cali_json_str(j, !S.owe ? "idle" : (S.until >= 0 || s_resume) ? "restoring" : "armed");
    cali_json_obj_end(j);
}

static const char *py(int v) { return v < 0 ? "None" : v ? "True" : "False"; }

static void chain_done(int result, const char *reason) {
    (void)reason;
    s_wait = 0;
    if (result == CALI_CTL_OK) {
        s_i++;
        s_until = s_now + s_step_ms;
        return;
    }
    cali_log("auto-camper: restore actuation failed: campingmode/%s result %d", s_chain[s_i], result);
    s_n = 0;
}

static void chain_tick(void) {
    const char *reason;
    int rc;
    if (s_wait || s_i >= s_n) return;
    rc = cali_ctl_submit("campingmode", s_chain[s_i], "on", -1, chain_done, &reason, "auto_camper");
    if (rc == CALI_CTL_PENDING) {
        s_wait = 1;
    } else if (!(rc == CALI_CTL_BUSY || rc == CALI_CTL_NOT_READY || rc == CALI_CTL_REFUSED) || s_now >= s_until) {
        cali_log("auto-camper: restore actuation failed: campingmode/%s not sent (%d)", s_chain[s_i], rc);
        s_n = 0;
    }   /* else: busy, a reconnect, or usb/lights before the master's push: again next tick */
}

static void step(uint64_t now) {
    static codec_kv_t c[CODEC_KV_MAX], e[CODEC_KV_MAX];   /* static: the 8 KB host-task stack */
    static const cali_ac_cfg_t cfg = {CALI_AC_MIN_SOC, CALI_AC_MAX_FAILS, CALI_AC_WINDOW_MS};
    int nc = frame_fields("campingmode", c), ne = frame_fields("energy", e);
    long lvl = field(e, ne, "SocTwoBattAfs");
    cali_ac_in_t in;
    cali_ac_out_t out;
    const char *event;
    int armed_before = S.owe;
    in.ign = cali_ac_ignition();
    in.master = (int)field(c, nc, "State");
    if (in.master > 0) in.master = 1;
    in.usb = field(c, nc, "UsbCharger") > 0;
    in.lights = in.master > 0 && field(c, nc, "InteriorLight") == 0 && field(c, nc, "OutsideLight") == 0;
    if (in.master < 0) in.usb = in.lights = -1;
    in.soc = lvl >= 0 && lvl <= 10 ? (int)lvl * 10 : -1;
    in.warning = field(e, ne, "SleepWarning") > 0 || field(e, ne, "WarningLevelActive") > 0 ||
                 field(e, ne, "WarningLevelTwo") >= 1;
    in.now = (int64_t)now;
    if (!s_on) {   /* keep the edges tracked so a mid-cycle enable won't misfire */
        if (in.ign >= 0) S.prev_ign = in.ign;
        if (in.master >= 0) S.prev_master = in.master, S.prev_usb = in.usb, S.prev_lights = in.lights;
        save();
        return;
    }
    if (s_resume && S.owe) S.until = in.now + cfg.window;   /* rebooted inside the window: a fresh one */
    s_resume = 0;
    cali_ac_decide(&in, &cfg, &S, &out);
    if (S.owe && !armed_before) event = "armed_engine_shed";
    else if (out.restored) event = "restored";
    else if (out.actuate) event = "restoring";
    else if (out.notice) event = strstr(out.notice, "battery") ? "stood_down" : "gave_up";
    else event = NULL;
    if (event) {
        char soc[12];
        if (in.soc >= 0) snprintf(soc, sizeof soc, "%d", in.soc);
        else strcpy(soc, "None");
        cali_log("auto-camper[%s]: %s (ignition=%s camping=%s soc=%s warn=%s fails=%d owe=%s)", event,
                 out.notice ? out.notice : strcmp(event, "armed_engine_shed") == 0 ? "armed engine shed" : event,
                 py(in.ign), py(in.master), soc, py(in.warning), S.fails, py(S.owe));
        cali_diag_evt("auto_camper", "\"decision\":\"%s\",\"soc\":%s,\"fails\":%d", event,
                      in.soc >= 0 ? soc : "null", S.fails);
    }
    if (out.actuate) {   /* master first (it gates usb + lights), as AutoCamper.step */
        s_n = 0;
        s_chain[s_n++] = "master";
        if (S.pre && S.pre_usb) s_chain[s_n++] = "usb";
        if (S.pre && S.pre_lights) s_chain[s_n++] = "lights";
        s_i = 0;
        s_wait = 0;
        s_until = now + s_step_ms;   /* each write may wait up to one step (sequencer busy, master's push) */
    }
    save();
}

void cali_ac_tick(uint64_t now_ms) {
    s_now = now_ms;
    chain_tick();
    if (s_i < s_n || now_ms < s_next || !cali_session_ready()) return;   /* a restore still going out */
    s_next = now_ms + s_step_ms;
    step(now_ms);
}
