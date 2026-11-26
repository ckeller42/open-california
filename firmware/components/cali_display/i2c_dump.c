/* i2c_dump.c — the console's read-only register dump (`i2c dump 0x58|0x34`) for the HIL bench and the sim's
 * fakes: the AW9523 IO expander and the AXP2101 power chip, read register by register, one log line each.
 * Reads only, and only these registers: no write, no other address, never AW9523 0x7F (a write that resets
 * it) or its write-only dimming registers 0x20-0x2F. The device is probed first: the board package aborts
 * on an I2C fault (BSP_ERROR_CHECK=y). */
#include <stdlib.h>
#include <string.h>

#include "bsp/esp-bsp.h"
#include "cali_display.h"
#include "cali_platform.h"
#include "driver/i2c_master.h"

#define DUMP_MS 30

/* AW9523B: input 0x00/01, output 0x02/03, config 0x04/05, interrupt 0x06/07, ID 0x10, global control 0x11,
 * LED mode 0x12/13. AXP2101: what display.c reads (0x00 0x01 0x18 0x30 0x34 0x35 0xA4) plus the bench probe's
 * set (0x02 0x10 0x26 0x27 0x68 0x80 0x90). */
static const uint8_t AW_REGS[] = {0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x10, 0x11, 0x12, 0x13};
static const uint8_t AXP_REGS[] = {0x00, 0x01, 0x02, 0x10, 0x18, 0x26, 0x27, 0x30, 0x34, 0x35, 0x68, 0x80, 0x90, 0xA4};

static void dump(uint16_t addr, const uint8_t *regs, size_t n) {
    static i2c_master_dev_handle_t s_dev[2];   /* one handle per chip, added once */
    i2c_master_dev_handle_t *dev = &s_dev[addr == 0x34];
    (void)bsp_i2c_init();
    if (i2c_master_probe(bsp_i2c_get_handle(), addr, DUMP_MS) != ESP_OK) {
        cali_log("i2c: 0x%02x does not answer", addr);
        return;
    }
    const i2c_device_config_t cfg = {.dev_addr_length = I2C_ADDR_BIT_LEN_7, .device_address = addr, .scl_speed_hz = 400000};
    if (!*dev && i2c_master_bus_add_device(bsp_i2c_get_handle(), &cfg, dev) != ESP_OK) {
        *dev = NULL;
        cali_log("i2c: 0x%02x not added", addr);
        return;
    }
    for (size_t i = 0; i < n; i++) {
        uint8_t v;
        if (i2c_master_transmit_receive(*dev, &regs[i], 1, &v, 1, DUMP_MS) == ESP_OK)
            cali_log("i2c: 0x%02x reg 0x%02x = 0x%02x", addr, regs[i], v);
        else
            cali_log("i2c: 0x%02x reg 0x%02x read failed", addr, regs[i]);
    }
}

int cali_i2c_dump_line(const char *line) {
    if (strncmp(line, "i2c dump", 8) != 0 || (line[8] != ' ' && line[8] != 0)) return 0;
    char *end;
    unsigned long addr = strtoul(line + 8, &end, 16);
    if (end != line + 8 && *end == 0 && addr == 0x58) dump(0x58, AW_REGS, sizeof AW_REGS);
    else if (end != line + 8 && *end == 0 && addr == 0x34) dump(0x34, AXP_REGS, sizeof AXP_REGS);
    else cali_log("i2c: usage: i2c dump 0x58|0x34 (read only: the AW9523 and the AXP2101)");
    return 1;
}
