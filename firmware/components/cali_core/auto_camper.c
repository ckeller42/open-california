/* auto_camper.c — calictl.automation.auto_camper_restore_decide in C (R_FW_AUTO_CAMPER). Contract:
 * include/cali_auto_camper.h. Line for line with the Python, comments there; held equal by
 * tests/firmware/test_auto_camper_parity.py.
 */
#include "cali_auto_camper.h"

#include <stdio.h>

void cali_ac_decide(const cali_ac_in_t *in, const cali_ac_cfg_t *cfg, cali_ac_state_t *st, cali_ac_out_t *out) {
    int ign, pign, m, pm;
    out->actuate = 0;
    out->restored = 0;
    out->notice = NULL;
    /* a read gap carries no information: freeze (no edge, no tick of the window) */
    if (in->ign < 0 || in->master < 0) return;
    ign = in->ign > 0;
    pign = st->prev_ign > 0;
    m = in->master > 0;
    pm = st->prev_master > 0;

    if (ign && !pign && pm && !st->owe) {   /* ARM: engine start with camping on */
        st->owe = 1;
        st->pre = 1;
        st->pre_usb = st->prev_usb > 0;
        st->pre_lights = st->prev_lights > 0;
        st->until = -1;
        st->fails = 0;
    }
    if (!ign && !pign && pm && !m) {        /* MANUAL CANCEL: off while parked */
        st->owe = 0;
        st->until = -1;
        st->fails = 0;
    }
    if (!ign && pign && st->owe) {          /* PARK: open the retry window */
        st->until = in->now + cfg->window;
        st->fails = 0;
    }
    if (st->owe && st->until >= 0 && !ign) {   /* RESTORING */
        if (in->now > st->until) {
            st->owe = 0;
            st->until = -1;
            out->notice = "Auto camper gave up: camping did not come back within the window after park.";
        } else if (m) {
            st->owe = 0;
            st->until = -1;
            st->fails = 0;
            out->restored = 1;
            out->notice = "Auto camper: camping restored after park.";
        } else if ((in->soc >= 0 && in->soc < cfg->min_soc) || in->warning) {
            st->owe = 0;
            st->until = -1;
            if (in->soc >= 0)
                snprintf(out->buf, sizeof out->buf,
                         "Auto camper stood down: battery low / warning (SoC %d%%) \xe2\x80\x94 not restoring.", in->soc);
            else
                snprintf(out->buf, sizeof out->buf, "Auto camper stood down: battery low / warning \xe2\x80\x94 not restoring.");
            out->notice = out->buf;
        } else if (st->fails >= cfg->max_fails) {
            st->owe = 0;
            st->until = -1;
            out->notice = "Auto camper gave up: camping would not re-enable after park.";
        } else {
            out->actuate = 1;
            st->fails++;
        }
    }
    st->prev_ign = ign;
    st->prev_master = m;
    st->prev_usb = in->usb > 0;
    st->prev_lights = in->lights > 0;
}
