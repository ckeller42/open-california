/* battery.c — the CoreS3's battery reading + runtime estimate, see include/cali_battery.h.
 * Implements R_FW_BATTERY's portable part (docs/firmware.md); the chip read lives in display.c. */
#include "cali_battery.h"

static cali_batt_t s_last = CALI_BATT_UNKNOWN;
static struct {
    uint64_t t;
    int pct;
} s_win[CALI_BATT_SAMPLES];
static int s_head, s_n;   /* oldest sample, count */

void cali_battery_reset(void) {
    const cali_batt_t u = CALI_BATT_UNKNOWN;
    s_last = u;
    s_head = s_n = 0;
}

/* pct / (drop / span), in minutes; -1 until the window is wide and deep enough. */
static int estimate(void) {
    int oldest, newest, drop;
    uint64_t span;
    if (s_n < 2) return -1;
    oldest = s_head;
    newest = (s_head + s_n - 1) % CALI_BATT_SAMPLES;
    span = s_win[newest].t - s_win[oldest].t;
    drop = s_win[oldest].pct - s_win[newest].pct;
    if (span < CALI_BATT_MIN_SPAN_MS || drop < CALI_BATT_MIN_DROP) return -1;
    return (int)((uint64_t)s_win[newest].pct * span / (uint64_t)drop / 60000u);
}

void cali_battery_update(const cali_batt_t *r, uint64_t now_ms) {
    s_last = *r;
    if (r->pct < 0 || r->usb != 0 || r->charging != 0) {   /* USB / charging / unknown: start over */
        s_head = s_n = 0;
    } else {
        while (s_n && now_ms - s_win[s_head].t > CALI_BATT_WINDOW_MS) {   /* drop what left the window */
            s_head = (s_head + 1) % CALI_BATT_SAMPLES;
            s_n--;
        }
        if (s_n == CALI_BATT_SAMPLES) {   /* fed faster than CALI_BATT_READ_MS: the oldest goes */
            s_head = (s_head + 1) % CALI_BATT_SAMPLES;
            s_n--;
        }
        s_win[(s_head + s_n) % CALI_BATT_SAMPLES].t = now_ms;
        s_win[(s_head + s_n) % CALI_BATT_SAMPLES].pct = r->pct;
        s_n++;
    }
    s_last.runtime_min = estimate();
}

void cali_battery_get(cali_batt_t *out) { *out = s_last; }

static void member(cali_json_t *j, const char *key, int v, int is_bool) {
    cali_json_key(j, key);
    if (v < 0) cali_json_null(j);
    else if (is_bool) cali_json_bool(j, v);
    else cali_json_int(j, v);
}

void cali_battery_json(cali_json_t *j) {
    cali_json_obj_begin(j);
    member(j, "pct", s_last.pct, 0);
    member(j, "mv", s_last.mv, 0);
    member(j, "usb", s_last.usb, 1);
    member(j, "charging", s_last.charging, 1);
    member(j, "runtime_min", s_last.runtime_min, 0);
    cali_json_obj_end(j);
}
