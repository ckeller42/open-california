/* lora_port_none.c — the LoRa port where there is no module driver: the host build, QEMU and the host
 * tests (R_FW_LORA). The device build links cali_lora's lora_esp.c instead. The module reads "absent". */
#include "cali_lora.h"

void cali_lora_port_post(const cali_lora_snap_t *s) { (void)s; }

int cali_lora_port_status(cali_lora_status_t *out) {
    static const cali_lora_status_t none = {.present = 0, .sf = 12, .note = "no module driver in this build"};
    *out = none;
    return 1;
}

int cali_lora_port_qr(char *out, size_t max) {
    cali_lora_status_t st;
    cali_lora_port_status(&st);
    return (int)cali_lora_qr_text(out, max, &st, cali_exp_lora());
}

int cali_lora_port_setup_qr(char *out, size_t max) {
    cali_lora_status_t st;
    cali_lora_port_status(&st);
    return (int)cali_lora_setup_qr_text(out, max, &st, cali_exp_lora());
}

void cali_lora_port_setup_request(void) {}

int cali_lora_port_setup_pending(void) { return 0; }

void cali_lora_port_forgotten(void) {}
