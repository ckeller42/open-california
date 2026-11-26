/* lora_port_none.c — the LoRa port where there is no module driver: the host build, QEMU and the host
 * tests (R_FW_LORA). The device build links cali_lora's lora_esp.c instead. The module reads "absent". */
#include "cali_lora.h"
#include "cali_platform.h"

void cali_lora_port_post(const cali_lora_snap_t *s) { (void)s; }

/* No module posts here; the one-slot queue lets a host test hand the owner task a command (web_cli "loracmd"). */
static struct {
    const char *fn, *what, *value;
    uint64_t at;
    int on;
} s_cmd;

void cali_lora_port_cmd_post(const char *fn, const char *what, const char *value) {
    if (!s_cmd.on) s_cmd.fn = fn, s_cmd.what = what, s_cmd.value = value, s_cmd.at = cali_uptime_ms(), s_cmd.on = 1;
}

int cali_lora_port_cmd_take(const char **fn, const char **what, const char **value, uint64_t *at_ms) {
    if (!s_cmd.on) return 0;
    *fn = s_cmd.fn, *what = s_cmd.what, *value = s_cmd.value, *at_ms = s_cmd.at;
    s_cmd.on = 0;
    return 1;
}

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

int cali_lora_port_at(const char *name) {
    (void)name;
    return -1;
}
