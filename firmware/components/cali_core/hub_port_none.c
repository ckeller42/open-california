/* hub_port_none.c — the hub port where there is no MQTT client: the host build, QEMU and the host tests.
 * The board links cali_hub's hub_esp.c instead. The client never starts, so cali_hub_tick does nothing. */
#include "cali_hub.h"

int cali_hub_port_start(const cali_hub_cfg_t *cfg) {
    (void)cfg;
    return -1;
}

void cali_hub_port_stop(void) {}

int cali_hub_port_online(void) { return 0; }

size_t cali_hub_port_take(uint8_t *buf, size_t max) {
    (void)buf, (void)max;
    return 0;
}

int cali_hub_port_post(int topic, const uint8_t *frame, size_t n) {
    (void)topic, (void)frame, (void)n;
    return -1;
}

void cali_hub_port_tick(void) {}
