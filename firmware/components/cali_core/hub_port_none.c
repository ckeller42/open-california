/* hub_port_none.c — the hub port where there is no MQTT client: the host build, QEMU and the host tests.
 * The board links cali_hub's hub_esp.c instead. app_main ticks cali_hub_tick only with CONFIG_CALI_DISPLAY
 * (ruling R24): with this stub the tick never runs, so no key is made and nothing is allocated. */
#include "cali_hub.h"

int cali_hub_port_start(const cali_hub_cfg_t *cfg) {
    (void)cfg;
    return -1;
}

void cali_hub_port_stop(void) {}

int cali_hub_port_online(void) { return 0; }

int cali_hub_port_why(void) { return CALI_HUB_WHY_NONE; }

size_t cali_hub_port_take(uint8_t *buf, size_t max) {
    (void)buf, (void)max;
    return 0;
}

int cali_hub_port_post(int topic, const uint8_t *frame, size_t n) {
    (void)topic, (void)frame, (void)n;
    return -1;
}

void cali_hub_port_tick(void) {}
