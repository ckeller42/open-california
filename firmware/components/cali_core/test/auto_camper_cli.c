/* auto_camper_cli.c — line driver for tests/firmware/test_auto_camper_parity.py ONLY; not in the ESP build.
 *
 * argv: min_soc max_fails window now. stdin, one case per line (-1 = None, pre -1 -1 = None):
 *   ign master usb lights soc warning owe pre_usb pre_lights until fails prev_ign prev_master prev_usb prev_lights
 * stdout per case:
 *   actuate restored owe pre_usb pre_lights until fails prev_ign prev_master prev_usb prev_lights|notice
 */
#include <stdio.h>
#include <stdlib.h>

#include "cali_auto_camper.h"

int main(int argc, char **argv) {
    cali_ac_cfg_t cfg;
    long long now, until;
    if (argc != 5) return 2;
    cfg.min_soc = atoi(argv[1]);
    cfg.max_fails = atoi(argv[2]);
    cfg.window = atoll(argv[3]);
    now = atoll(argv[4]);
    setvbuf(stdout, NULL, _IOLBF, 0);
    for (;;) {
        cali_ac_in_t in;
        cali_ac_state_t st;
        cali_ac_out_t out;
        int pu, pl;
        if (scanf("%d %d %d %d %d %d %d %d %d %lld %d %d %d %d %d", &in.ign, &in.master, &in.usb, &in.lights,
                  &in.soc, &in.warning, &st.owe, &pu, &pl, &until, &st.fails, &st.prev_ign, &st.prev_master,
                  &st.prev_usb, &st.prev_lights) != 15)
            return 0;
        in.now = now;
        st.pre = pu >= 0;
        st.pre_usb = pu > 0;
        st.pre_lights = pl > 0;
        st.until = until;
        cali_ac_decide(&in, &cfg, &st, &out);
        printf("%d %d %d %d %d %lld %d %d %d %d %d|%s\n", out.actuate, out.restored, st.owe,
               st.pre ? st.pre_usb : -1, st.pre ? st.pre_lights : -1, (long long)st.until, st.fails, st.prev_ign,
               st.prev_master, st.prev_usb, st.prev_lights, out.notice ? out.notice : "");
    }
}
