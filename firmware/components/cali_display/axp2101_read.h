/* axp2101_read.h — the AXP2101 battery read of display.c (R_FW_BATTERY), included once by display.c and
 * once by the sim tier (firmware/host/sim/axp_sim.c), so both run this one copy. Not a public header: the
 * includer provides io_ok(), BUS_AXP, LTR_IO_MS, PROBE_MS, cali_log, bsp_i2c_get_handle and the IDF I2C
 * master API. */
#ifndef CALI_AXP2101_READ_H
#define CALI_AXP2101_READ_H

/* AXP2101 battery read (R_FW_BATTERY), on the same bus. Register meanings as M5Stack's own drivers:
 * M5Unified src/utility/power/AXP2101_Class.inl (isVBUS: 0x00 bit 5 VBUS good; getBatState: 0x00
 * bit 3 battery present; getChargeStatus: 0x01 bits 6:5 = 01 charging / 10 discharging / 00 standby;
 * getBatteryLevel: 0xA4 percent; getBatteryVoltage: readRegister14(0x34) = (0x34 & 0x3F) << 8 | 0x35,
 * mV) and m5stack/esphome-yaml components/axp2101/axp2101.cpp (XPowersLib's register map:
 * enableBattVoltageMeasure = 0x30 bit 0 ADC channel; enableGauge = 0x18 bit 3 fuel gauge).
 * Only those two enable bits are ever written (read-modify-write, once at init); no power-rail
 * register. M5Unified's CoreS3 setup also writes 0x30 = 0x0F; BSP 4.1.0 touches neither. */
#define AXP_STATUS1 0x00
#define AXP_STATUS2 0x01
#define AXP_GAUGE_CTRL 0x18   /* bit 3: fuel gauge enable */
#define AXP_ADC_CTRL 0x30     /* bit 0: battery voltage ADC enable */
#define AXP_VBAT_H 0x34
#define AXP_PCT 0xA4

static i2c_master_dev_handle_t s_axp;   /* NULL: no power chip handle (battery stays unknown) */
static int s_axp_enabled;               /* both enable bits confirmed set (else retried with each read) */

static int axp_get(uint8_t reg, uint8_t *v, size_t n) {   /* LTR_IO_MS per transfer, as the LTR reads */
    return io_ok(BUS_AXP, i2c_master_transmit_receive(s_axp, &reg, 1, v, n, LTR_IO_MS)) ? 0 : -1;
}

/* Read-modify-write, only when the bit is clear. 0 = the bit is set (now or already), -1 = a failed transfer. */
static int axp_set_bit(uint8_t reg, uint8_t bit) {
    uint8_t v, w[2];
    if (axp_get(reg, &v, 1) != 0) return -1;
    if (v & bit) return 0;
    w[0] = reg;
    w[1] = (uint8_t)(v | bit);
    if (i2c_master_transmit(s_axp, w, 2, PROBE_MS) == ESP_OK) return 0;
    cali_log("battery: axp2101 enable 0x%02x failed", reg);
    return -1;
}

static void axp_enable(void) {   /* at init, then with each read until both bits are confirmed */
    int adc = axp_set_bit(AXP_ADC_CTRL, 0x01), gauge = axp_set_bit(AXP_GAUGE_CTRL, 0x08);
    s_axp_enabled = adc == 0 && gauge == 0;
}

static void axp_init(void) {   /* the chip answered the probe in cali_display_init */
    i2c_device_config_t cfg = {.dev_addr_length = I2C_ADDR_BIT_LEN_7, .device_address = AXP2101_ADDR, .scl_speed_hz = 400000};
    if (i2c_master_bus_add_device(bsp_i2c_get_handle(), &cfg, &s_axp) != ESP_OK) {
        s_axp = NULL;
        cali_log("battery: axp2101 not added, battery unknown");
        return;
    }
    axp_enable();
}

/* One reading: a failed status read -> all unknown; no battery (or a failed read of it) -> pct / mv unknown. */
static void axp_read(cali_batt_t *b) {
    uint8_t s1, s2, v[2], pct;
    const cali_batt_t unknown = CALI_BATT_UNKNOWN;
    *b = unknown;
    if (s_axp && !s_axp_enabled) axp_enable();
    if (!s_axp || axp_get(AXP_STATUS1, &s1, 1) != 0 || axp_get(AXP_STATUS2, &s2, 1) != 0) return;
    b->usb = (s1 & 0x20) != 0;
    b->charging = ((s2 >> 5) & 0x03) == 0x01;
    if (!(s1 & 0x08)) return;   /* no battery present */
    if (axp_get(AXP_VBAT_H, v, 2) == 0) b->mv = (v[0] & 0x3F) << 8 | v[1];
    if (axp_get(AXP_PCT, &pct, 1) == 0 && pct <= 100) b->pct = pct;
}
#endif
