/* lora_port_none.c — the LoRa port where there is no module driver: the host build, QEMU and the host
 * tests (R_FW_LORA). The device build links cali_lora's lora_esp.c instead. The module reads "absent". */
#include "cali_lora.h"

void cali_lora_port_post(const cali_lora_snap_t *s) { (void)s; }

int cali_lora_port_status(cali_lora_status_t *out) {
    static const cali_lora_status_t none = {.present = 0, .sf = 12, .note = "no module driver in this build"};
    *out = none;
    return 1;
}
