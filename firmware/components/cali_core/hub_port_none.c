/* hub_port_none.c — the hub port where there is no MQTT client: the host build, QEMU and the host tests.
 * The board links cali_hub's hub_esp.c instead. The hub reads offline; no QR, no rekey. */
#include "cali_hub.h"

void cali_hub_port_status(int *online, uint32_t *last_pub_s) { *online = 0, *last_pub_s = 0; }

void cali_hub_port_cfg_changed(void) {}

size_t cali_hub_port_qr(char *out, size_t max) {
    (void)out, (void)max;
    return 0;
}

int cali_hub_port_rekey(void) { return -1; }
