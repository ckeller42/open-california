/* cali_auto_camper.h — auto camper mode on the satellite (R_FW_AUTO_CAMPER): restore camping after
 * you park, as calictl serve does (calictl/automation.py, docs/business-logic/auto-camper-mode.md).
 *
 * Decide (auto_camper.c, pure): cali_ac_decide() is calictl.automation.auto_camper_restore_decide in
 * C — same branches, same notice texts — held equal by tests/vectors/auto_camper.json
 * (tools/gen_auto_camper_vectors.py, tests/firmware/test_auto_camper_parity.py). Booleans that may be
 * unknown are -1 (Python None); restore_until -1 = None. The clock is the caller's: the function only
 * compares now with restore_until, so the ESP passes uptime ms and a window in ms.
 *
 * Run (auto_camper_run.c, owner task): every CALI_AC_STEP_MS (serve's 30 s poll) while the session is
 * ready, one decide step over this link's frames (vehicle TerminalOneFive, campingmode, energy); an
 * actuate goes out through the control sequencer as campingmode master on, then usb / lights on
 * (cali_ctl_submit, the same path as POST /api/command). The toggle (default ON) and the restore debt
 * persist in the kv store (NVS namespace "cali", keys CALI_AC_ON_KEY / CALI_AC_STATE_KEY). Logs
 * "auto-camper[<event>]: …" like serve.
 *
 * C99, no malloc, no NimBLE/ESP-IDF includes.
 */
#ifndef CALI_AUTO_CAMPER_H
#define CALI_AUTO_CAMPER_H

#include <stdint.h>

#include "cali_json.h"

#ifdef __cplusplus
extern "C" {
#endif

/* calictl.automation's defaults (window in ms here). */
#define CALI_AC_WINDOW_MS 120000
#define CALI_AC_MAX_FAILS 3
#define CALI_AC_MIN_SOC 20
#define CALI_AC_STEP_MS 30000u   /* serve's POLL_INTERVAL: one decision (and at most one restore try) per step */

#define CALI_AC_ON_KEY "ac_on"         /* 1 byte: 0 off, 1 on; missing = on */
#define CALI_AC_STATE_KEY "ac_state"   /* the restore debt, see auto_camper_run.c */

typedef struct {
    int owe;                 /* owe_restore */
    int pre;                 /* pre_drive known (its master is always on) */
    int pre_usb, pre_lights;
    int64_t until;           /* restore_until, -1 = None */
    int fails;
    int prev_ign, prev_master, prev_usb, prev_lights;   /* -1 = None */
} cali_ac_state_t;

typedef struct {
    int ign, master, usb, lights;   /* -1 = unknown this step */
    int soc;                        /* leisure battery %, -1 = None */
    int warning;
    int64_t now;
} cali_ac_in_t;

typedef struct {
    int min_soc, max_fails;
    int64_t window;
} cali_ac_cfg_t;

typedef struct {
    int actuate;          /* write master on (+ usb / lights per the state's pre_*) */
    int restored;
    const char *notice;   /* NULL, or the one-line notice (may point into buf) */
    char buf[96];
} cali_ac_out_t;

/* One poll's decision: updates *st in place (the Python function's returned state). */
void cali_ac_decide(const cali_ac_in_t *in, const cali_ac_cfg_t *cfg, cali_ac_state_t *st, cali_ac_out_t *out);

/* ---- the controller (auto_camper_run.c) ---- */
void cali_ac_init(void);                 /* owner task, after cali_platform_init: load toggle + debt */
void cali_ac_tick(uint64_t now_ms);      /* owner task, after cali_session_tick */
int cali_ac_set_enabled(int on);         /* persisted; off, and off -> on, clear the debt. 0 ok, -1 not saved
                                          * (a failed debt write stores no toggle) */
/* This link's ignition: vehicle TerminalOneFive OR campingmode Enable; -1 when neither is read here.
 * The step's input, shared with the Pi wake (cali_pi_wake.h). */
int cali_ac_ignition(void);
/* 1 while a camping restore is owed (device.auto_camper.state armed/restoring): the power-save rest
 * stays away from it (R_FW_BLE_REST). */
int cali_ac_owed(void);
void cali_ac_set_step_ms(uint32_t ms);   /* tests only (cali-host "acstep"): the step period */
/* device.auto_camper: {"enabled":<bool>,"state":"idle"|"armed"|"restoring"} (a value: key first). */
void cali_ac_json(cali_json_t *j);

#ifdef __cplusplus
}
#endif

#endif /* CALI_AUTO_CAMPER_H */
