/* display.c — paints the pure status model (cali_display_model.h) on the CoreS3 screen (#154).
 * Runs on the owner task (cali_display_tick from do_work); LVGL renders on esp_lvgl_port's task, so
 * every LVGL call sits under bsp_display_lock. Only changed labels/dots are touched.
 * Wake + brightness (R_FW_DISPLAY_WAKE): reads the LTR-553ALS light/proximity sensor on the board
 * package's I2C bus every tick and filters the touch input (a wake tap is swallowed); the model
 * decides on / dim / off and the level. Motion wake: reads the BMI270 accelerometer every tick too.
 * Battery (R_FW_BATTERY): reads the AXP2101 every CALI_BATT_READ_MS into cali_battery_update.
 * Touch (R_FW_TOUCH_NO_ABORT, #346): the touch controller is read here, not by esp_lvgl_port, whose read
 * ESP_ERROR_CHECKs it — one failed I2C read on the shared bus aborted the whole firmware. */
#include <stdio.h>
#include <string.h>

#include "bmi270.h"
#include "bsp/esp-bsp.h"
#include "cali_battery.h"
#include "cali_display.h"
#include "cali_diag.h"
#include "cali_display_model.h"
#include "cali_platform.h"
#include "cali_status.h"
#include "driver/i2c_master.h"
#include "esp_lcd_touch.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "lvgl.h"
#include "net_consts.h"

#define AXP2101_ADDR 0x34 /* power chip: BSP 4.1.0 src/bsp_feature_en.c BSP_AXP2101_ADDR */
#define AW9523_ADDR 0x58  /* IO expander: ESP_IO_EXPANDER_I2C_AW9523_ADDRESS_00 (BSP_IO_EXPANDER_ADDRESS) */
#define PROBE_MS 50

/* LTR-553ALS-WA ambient light + proximity, I2C 0x23 on the BSP's bus (no BSP helper in 4.1.0; the INT
 * pin reaches no ESP GPIO, so it is polled). Registers + bits: Lite-On LTR-553ALS-WA data sheet Rev 1.0
 * (22 May 2013), pp. 13-24: ALS_CONTR 0x80 (gain 4:2, SW reset 1, active 0), PS_CONTR 0x81 (active
 * 1:0 = 10), PS_LED 0x82, PS_N_PULSES 0x83, ALS_MEAS_RATE 0x85 (integration 5:3, rate 2:0), PART_ID
 * 0x86 (= 0x92), ALS_DATA_CH1 0x88/0x89 then CH0 0x8A/0x8B (read CH1 first: the four lock until 0x8B
 * is read), ALS_PS_STATUS 0x8C (bit 7 = ALS data INVALID), PS_DATA 0x8D + 0x8E bits 2:0 (11 bit). */
#define LTR_ADDR 0x23
#define LTR_GAIN 8   /* ALS gain 8x: 0.125 .. 8k lux (dark van to daylight through glass) */
#define LTR_IO_MS 10 /* per transfer: a dead sensor costs the owner (NimBLE host) task one 10 ms wait per tick */
static const uint8_t LTR_INIT[][2] = {
    {0x82, 0x7B},              /* PS LED 60 kHz, 100 % duty, 50 mA (the data sheet's own example) */
    {0x83, 0x04},              /* 4 PS pulses */
    {0x85, 0x01},              /* ALS integration 100 ms, repeat 100 ms */
    {0x80, (3 << 2) | 1},      /* ALS gain 8x, active */
    {0x81, 0x02},              /* PS active, gain 16x */
};

/* BMI270 accelerometer, I2C 0x69 on the same bus (M5Unified's CoreS3 address; the BMM150 behind it is
 * unused). It outputs no data until Bosch's 8 KB config blob is loaded (INIT_CTRL 0x59 / INIT_DATA 0x5E,
 * then INTERNAL_STATUS 0x21 = 0x01): Bosch's SensorAPI, the Linux bmi270 driver, M5Unified and
 * espressif/bmi270 all upload it, and Bosch's forum confirms no data without it. BSP 4.1.0 already
 * links espressif/bmi270 (its sensor-hub IMU, Apache-2.0, the blob inside), so bmi270_create does the
 * upload and no blob is vendored here. Then only the accelerometer is powered (PWR_CTRL 0x7D = 0x04),
 * range +-8 g (ACC_RANGE 0x41 = 0x02, 4096 LSB/g), the reset ACC_CONF (100 Hz). ACC_X..Z = 0x0C..0x11,
 * int16 little endian. ponytail: bmi270_create's transfers wait without a timeout (the driver's -1);
 * a probe of 0x69 goes first, and it runs once at init — the tick's read keeps LTR_IO_MS. */
#define IMU_ACC_X 0x0C
static const uint8_t IMU_INIT[][2] = {{0x41, 0x02}, {0x7D, 0x04}};

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

#if CONFIG_CALI_DISPLAY_LANG_EN
#define LANG CALI_LANG_EN
#else
#define LANG CALI_LANG_DE
#endif

/* layout, 320 x 240 landscape */
#define W 320
#define PAD 8
#define HEAD_SEP_Y 40
#define ROW_Y0 46
#define ROW_H 46 /* two 21 px text lines + gap */
#define DOT 12
#define LABEL_X 30
#define VALUE_X 106 /* LABEL_X + widest label ("Camper" 65 px) + gap; tests/test_display_font.py checks */
#define TITLE_END_X 200 /* "calictl satellite" at 24 px ends at about x 190 */

LV_FONT_DECLARE(font_latin1_16);
LV_FONT_DECLARE(font_latin1_24);

static const uint32_t DOT_RGB[] = {
    [CALI_DOT_GREEN] = 0x2ecc71, [CALI_DOT_AMBER] = 0xf5a623, [CALI_DOT_RED] = 0xe74c3c, [CALI_DOT_GREY] = 0x7f8c8d};

typedef struct {
    lv_obj_t *dot, *label, *value;
    int dot_now;            /* -1 = never painted */
    const char *label_now;  /* model labels are string constants: compare the pointer */
    char text_now[CALI_ROW_TEXT_MAX];
} row_ui_t;

static int s_ok;
static const cali_transport_t *s_t;
static cali_display_model_t s_model;
static uint64_t s_last_paint;
static row_ui_t s_rows[3];
static lv_obj_t *s_footer;
static char s_footer_now[CALI_ROW_TEXT_MAX];
static int s_brightness = -1;
static i2c_master_dev_handle_t s_ltr;   /* NULL: no light/proximity sensor */
static i2c_master_dev_handle_t s_axp;   /* NULL: no power chip handle (battery stays unknown) */
static bmi270_handle_t *s_imu;          /* NULL: no accelerometer (motion wake off, device.display.motion null) */
static uint64_t s_last_batt;
static int s_batt_read;                 /* a battery read has happened (the first one is due at once) */
static int s_axp_enabled;               /* both enable bits confirmed set (else retried with each read) */
static esp_lcd_touch_handle_t s_tp;     /* NULL: touch off (the port's own read is never called) */
static uint32_t s_touch_errs;           /* LVGL task -> owner task, __atomic */
static const char *s_touch_err;         /* the last one's esp_err_to_name, __atomic */
static uint32_t s_touch_logged;         /* owner task: the count in the last "touch" evt */
static uint64_t s_touch_log_ms;
static uint8_t s_swallow;               /* LVGL task only */
static int s_touched, s_awake = 1;      /* LVGL task <-> owner task, __atomic */

static lv_obj_t *box(lv_obj_t *parent, int x, int y, int w, int h, uint32_t rgb) {
    lv_obj_t *o = lv_obj_create(parent);
    lv_obj_remove_style_all(o);
    lv_obj_set_pos(o, x, y);
    lv_obj_set_size(o, w, h);
    lv_obj_set_style_bg_opa(o, LV_OPA_COVER, 0);
    lv_obj_set_style_bg_color(o, lv_color_hex(rgb), 0);
    return o;
}

static lv_obj_t *label(lv_obj_t *parent, const lv_font_t *font, const char *text) {
    lv_obj_t *l = lv_label_create(parent);
    lv_obj_set_style_text_font(l, font, 0);
    lv_label_set_text(l, text);
    return l;
}

static void build(void) {
    lv_obj_t *scr = lv_screen_active();
    int line = lv_font_get_line_height(&font_latin1_16);
    char fw[48];
    lv_obj_set_scrollable(scr, false);
    lv_obj_set_style_bg_color(scr, lv_color_hex(0x101418), 0);
    lv_obj_set_style_bg_opa(scr, LV_OPA_COVER, 0);
    lv_obj_set_style_text_color(scr, lv_color_hex(0xe6e6e6), 0);

    lv_obj_set_pos(label(scr, &font_latin1_24, "calictl satellite"), PAD, PAD);
    snprintf(fw, sizeof fw, "fw %s", cali_fw_version());
    {  /* a long version (-dirty, git describe) ends in "…" instead of running into the title */
        lv_obj_t *l = label(scr, &font_latin1_16, fw);
        lv_label_set_long_mode(l, LV_LABEL_LONG_MODE_DOTS);
        lv_obj_set_width(l, W - PAD - TITLE_END_X);
        lv_obj_set_style_text_align(l, LV_TEXT_ALIGN_RIGHT, 0);
        lv_obj_set_pos(l, TITLE_END_X, PAD + 6);
    }
    box(scr, PAD, HEAD_SEP_Y, W - 2 * PAD, 1, 0x3a4048);

    for (int i = 0; i < 3; i++) {
        row_ui_t *r = &s_rows[i];
        int y = ROW_Y0 + i * ROW_H;
        r->dot = box(scr, PAD + 2, y + (line - DOT) / 2, DOT, DOT, DOT_RGB[CALI_DOT_GREY]);
        lv_obj_set_style_radius(r->dot, LV_RADIUS_CIRCLE, 0);
        r->label = label(scr, &font_latin1_16, "");
        lv_obj_set_pos(r->label, LABEL_X, y);
        r->value = label(scr, &font_latin1_16, "");
        lv_label_set_long_mode(r->value, LV_LABEL_LONG_MODE_DOTS);
        lv_obj_set_pos(r->value, VALUE_X, y);
        lv_obj_set_size(r->value, W - PAD - VALUE_X, 2 * line);  /* two lines: DOTS cuts at the end of the 2nd */
        r->dot_now = -1;
    }

    box(scr, PAD, ROW_Y0 + 3 * ROW_H - 2, W - 2 * PAD, 1, 0x3a4048);
    s_footer = label(scr, &font_latin1_16, "");
    lv_label_set_long_mode(s_footer, LV_LABEL_LONG_MODE_WRAP);  /* setup: SSID + password may need 2 lines */
    lv_obj_set_width(s_footer, W - 2 * PAD);
    lv_obj_set_style_text_align(s_footer, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_set_pos(s_footer, PAD, ROW_Y0 + 3 * ROW_H + 4);  /* two setup lines end at 230 */
}

int cali_display_ready(void) { return s_ok; }

#if CONFIG_LV_USE_GESTURE_RECOGNITION
#error "touch_read replaces esp_lvgl_port's read, which also feeds LVGL's gesture recognizers"
#endif

/* esp_lvgl_port 2.9.0 (pinned in idf_component.yml) keeps the esp_lcd_touch handle only in its private
 * lvgl_port_touch_ctx_t (src/lvgl9/esp_lvgl_port_touch.c), the indev's driver data, as the FIRST member;
 * C lets a struct pointer be read as a pointer to its first member. BSP 4.1.0 keeps its own copy in a
 * static and exposes none. A layout change fails the plausibility check (the BSP's 320x240 config):
 * then touch is off (the hand-near wake still works) rather than back on the aborting read. */
typedef struct { esp_lcd_touch_handle_t handle; } port_touch_ctx_head_t;

static esp_lcd_touch_handle_t port_touch_handle(lv_indev_t *in) {
    const port_touch_ctx_head_t *ctx = lv_indev_get_driver_data(in);
    esp_lcd_touch_handle_t h = ctx ? ctx->handle : NULL;
    return h && h->read_data && h->config.x_max == BSP_LCD_H_RES && h->config.y_max == BSP_LCD_V_RES ? h : NULL;
}

/* The touch read in LVGL's task, as the port's (point 0, no scaling: the BSP sets none) but a failed
 * read is "released" for this tick and counted, never an abort. Then: note the touch for the model,
 * swallow a wake tap (cali_display_touch_gate). */
static void touch_read(lv_indev_t *indev, lv_indev_data_t *data) {
    int pressed;
    (void)indev;
    data->state = LV_INDEV_STATE_RELEASED;
    if (s_tp) {
        esp_lcd_touch_point_data_t p[CONFIG_ESP_LCD_TOUCH_MAX_POINTS];
        uint8_t n = 0;
        esp_err_t e = esp_lcd_touch_read_data(s_tp);
        if (e == ESP_OK) e = esp_lcd_touch_get_data(s_tp, p, &n, CONFIG_ESP_LCD_TOUCH_MAX_POINTS);
        if (e != ESP_OK) {
            n = 0;
            __atomic_store_n(&s_touch_err, esp_err_to_name(e), __ATOMIC_RELAXED);
            __atomic_add_fetch(&s_touch_errs, 1, __ATOMIC_RELEASE);
        }
        if (n) {
            data->state = LV_INDEV_STATE_PRESSED;
            data->point.x = p[0].x;
            data->point.y = p[0].y;
        }
    }
    pressed = data->state == LV_INDEV_STATE_PRESSED;
    if (pressed) __atomic_store_n(&s_touched, 1, __ATOMIC_RELAXED);
    if (!cali_display_touch_gate(&s_swallow, pressed, __atomic_load_n(&s_awake, __ATOMIC_RELAXED)))
        data->state = LV_INDEV_STATE_RELEASED;
}

static int ltr_get(uint8_t reg, uint8_t *v) {   /* single-byte combined reads, as the data sheet shows */
    return i2c_master_transmit_receive(s_ltr, &reg, 1, v, 1, LTR_IO_MS) == ESP_OK ? 0 : -1;
}

static void ltr_init(void) {
    i2c_device_config_t cfg = {.dev_addr_length = I2C_ADDR_BIT_LEN_7, .device_address = LTR_ADDR, .scl_speed_hz = 400000};
    uint8_t id = 0;
    /* The LTR-553 sits on the camera/proximity flex, powered by the camera rails (AXP2101 ALDO3 +
     * BLDO1/2), which BSP 4.1.0 switches on only in BSP_FEATURE_CAMERA (bsp_camera_start). Unpowered,
     * it NACKs 0x23 (live: device.display.sensor "absent"). M5Unified's CoreS3 setup enables every LDO
     * (AXP2101 0x90 = 0xBF) before any sensor; then wait the data sheet's 100 ms power-up time.
     * ponytail: this also releases the GC0308 reset (AW9523 P1_0); camera idles without XCLK. */
    if (bsp_feature_enable(BSP_FEATURE_CAMERA, true) != ESP_OK) cali_log("display: camera/sensor rail enable failed");
    vTaskDelay(pdMS_TO_TICKS(100));
    if (i2c_master_probe(bsp_i2c_get_handle(), LTR_ADDR, PROBE_MS) != ESP_OK ||
        i2c_master_bus_add_device(bsp_i2c_get_handle(), &cfg, &s_ltr) != ESP_OK) {
        s_ltr = NULL;
        cali_log("display: no light sensor (ltr553), fixed brightness");
        return;
    }
    for (size_t i = 0; i < sizeof LTR_INIT / sizeof LTR_INIT[0]; i++)
        if (i2c_master_transmit(s_ltr, LTR_INIT[i], 2, PROBE_MS) != ESP_OK) cali_log("display: ltr553 init write failed");
    (void)ltr_get(0x86, &id);
    cali_log("display: light sensor ltr553 part 0x%02x", id);
}

/* lux (-1 = no valid reading) and proximity counts (-1 = none). Lux: Lite-On's family formula (the
 * LTR-553/-329 data sheets' "Appendix A", not printed in the WA Rev 1.0 PDF; coefficients as in
 * ESPHome's ltr_als_ps driver): ratio = ch1/(ch0+ch1); < 0.45: 1.7743 ch0 + 1.1059 ch1;
 * < 0.64: 4.2785 ch0 - 1.9548 ch1; < 0.85: 0.5926 ch0 + 0.1185 ch1; else 0; / gain / (integration/100 ms).
 * The WA sheet's typical 200 lux point (gain 96: ch0 4900, ch1 1800) gives ~111 lux by it, and the
 * cover glass dims it further: absolute lux is uncalibrated — DISPLAY_LUX_LOW/HIGH are the knobs. */
static void ltr_read(int32_t *lux, int32_t *prox) {
    uint8_t st, b[4], p0, p1;
    *lux = *prox = -1;
    if (ltr_get(0x8C, &st) != 0 || ltr_get(0x8D, &p0) != 0 || ltr_get(0x8E, &p1) != 0) return;   /* first failure ends it */
    *prox = (int32_t)((p1 & 0x07) << 8 | p0);
    for (int i = 0; i < 4; i++)
        if (ltr_get((uint8_t)(0x88 + i), &b[i]) != 0) return;
    if (st & 0x80) return;   /* ALS data invalid */
    {
        float ch1 = (float)(b[1] << 8 | b[0]), ch0 = (float)(b[3] << 8 | b[2]);
        float r = ch0 + ch1 > 0 ? ch1 / (ch0 + ch1) : 0, l;
        l = r < 0.45f ? 1.7743f * ch0 + 1.1059f * ch1 : r < 0.64f ? 4.2785f * ch0 - 1.9548f * ch1
            : r < 0.85f ? 0.5926f * ch0 + 0.1185f * ch1 : 0;
        *lux = (int32_t)(l / LTR_GAIN + 0.5f);
    }
}

static void imu_init(void) {   /* after ltr_init: the camera/sensor rails are on by then, whichever it needs */
    bmi270_driver_config_t cfg = {.addr = BMI270_I2C_ADDRESS_H, .interface = BMI270_USE_I2C, .i2c_bus = bsp_i2c_get_handle()};
    if (i2c_master_probe(bsp_i2c_get_handle(), BMI270_I2C_ADDRESS_H, PROBE_MS) != ESP_OK) {
        cali_log("display: no accelerometer (bmi270), motion wake off");
        return;
    }
    if (bmi270_create(&cfg, &s_imu) != ESP_OK) {   /* chip id, soft reset, the config upload, INIT_OK */
        s_imu = NULL;
        cali_log("display: bmi270 init failed, motion wake off");
        return;
    }
    for (size_t i = 0; i < sizeof IMU_INIT / sizeof IMU_INIT[0]; i++)
        if (i2c_master_transmit(s_imu->i2c_handle, IMU_INIT[i], 2, PROBE_MS) != ESP_OK) {
            (void)bmi270_delete(s_imu);
            s_imu = NULL;
            cali_log("display: bmi270 accel enable failed, motion wake off");
            return;
        }
    cali_log("display: accelerometer bmi270 ready");
}

/* Acceleration in mg; 0 = a reading, -1 = none (the model then compares nothing). */
static int imu_read(int32_t acc[3]) {
    uint8_t reg = IMU_ACC_X, b[6];
    if (i2c_master_transmit_receive(s_imu->i2c_handle, &reg, 1, b, 6, LTR_IO_MS) != ESP_OK) return -1;
    for (int i = 0; i < 3; i++) acc[i] = (int32_t)(int16_t)(b[2 * i + 1] << 8 | b[2 * i]) * 1000 / 4096;
    return 0;
}

static int axp_get(uint8_t reg, uint8_t *v, size_t n) {   /* LTR_IO_MS per transfer, as the LTR reads */
    return i2c_master_transmit_receive(s_axp, &reg, 1, v, n, LTR_IO_MS) == ESP_OK ? 0 : -1;
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

int cali_display_init(const cali_transport_t *t) {
#if CONFIG_CALI_DISPLAY_FORCE_FAIL  /* test-only: the failure path without unplugging anything */
    cali_log("display: unavailable (forced)");
    return -1;
#endif
    /* BSP_ERROR_CHECK=y: bsp_i2c_init and bsp_display_start abort on their own faults, so prove the
     * chips the board package needs answer first. */
    (void)bsp_i2c_init();
    if (i2c_master_probe(bsp_i2c_get_handle(), AXP2101_ADDR, PROBE_MS) != ESP_OK) { cali_log("display: unavailable (axp2101)"); return -1; }
    if (i2c_master_probe(bsp_i2c_get_handle(), AW9523_ADDR, PROBE_MS) != ESP_OK) { cali_log("display: unavailable (aw9523)"); return -1; }
    (void)bsp_display_start();
    if (!bsp_display_lock(DISPLAY_LOCK_TIMEOUT_MS)) {  /* LVGL task wedged already: no UI, tick stays a no-op */
        cali_log("display: unavailable (lock)");
        return -1;
    }
    build();
    {
        lv_indev_t *in = bsp_display_get_input_dev();
        if (in) {
            s_tp = port_touch_handle(in);
            if (!s_tp) cali_log("display: touch handle not found, touch off");
            lv_indev_set_read_cb(in, touch_read);
        }
    }
    ltr_init();
    imu_init();
    axp_init();
    bsp_display_unlock();
    bsp_display_backlight_on();
    s_t = t;
    cali_display_model_init(&s_model);
    s_ok = 1;
    return 0;
}

static void paint_row(row_ui_t *r, const cali_row_t *m) {
    if (r->dot_now != (int)m->dot) {
        r->dot_now = (int)m->dot;
        lv_obj_set_style_bg_color(r->dot, lv_color_hex(DOT_RGB[m->dot]), 0);
    }
    if (r->label_now != m->label) {
        r->label_now = m->label;
        lv_label_set_text(r->label, m->label);
    }
    if (strcmp(r->text_now, m->text)) {
        memcpy(r->text_now, m->text, sizeof r->text_now);
        lv_label_set_text(r->value, m->text);
    }
}

/* Owner task: {"ev":"evt","kind":"touch","errors":<since boot>,"last":"<esp_err name>"} when the count
 * grew, at most once per CALI_DIAG_PERIOD_MS (a dead bus fails every read); retried until the log takes it. */
static void touch_errors_evt(uint64_t now_ms) {
    uint32_t n = __atomic_load_n(&s_touch_errs, __ATOMIC_ACQUIRE);
    if (n == s_touch_logged || (s_touch_logged && now_ms - s_touch_log_ms < CALI_DIAG_PERIOD_MS)) return;
    if (cali_diag_evt("touch", "\"errors\":%lu,\"last\":\"%s\"", (unsigned long)n,
                      __atomic_load_n(&s_touch_err, __ATOMIC_RELAXED)) != 0) return;
    s_touch_logged = n;
    s_touch_log_ms = now_ms;
}

void cali_display_tick(uint64_t now_ms) {
    cali_status_t st;
    cali_display_view_t v;
    cali_display_in_t in = {-1, -1, s_ltr != NULL, 0, CALI_BATT_UNKNOWN, 0, {0, 0, 0}};
    if (!s_ok || now_ms - s_last_paint < DISPLAY_REFRESH_MS) return;
    s_last_paint = now_ms;
    if (!s_batt_read || now_ms - s_last_batt >= CALI_BATT_READ_MS) {   /* off the paint's lock; ~5 short reads */
        cali_batt_t b;
        s_batt_read = 1;
        s_last_batt = now_ms;
        axp_read(&b);
        cali_battery_update(&b, now_ms);
    }
    cali_battery_get(&in.batt);
    /* The owner task is the NimBLE host task: never wait on a wedged render. A skipped paint leaves
     * the model and the painted-text buffers untouched, so the next try repaints whatever changed. */
    if (!bsp_display_lock(DISPLAY_LOCK_TIMEOUT_MS)) return;
    if (s_ltr) ltr_read(&in.lux, &in.prox);
    if (s_imu) in.acc_ok = imu_read(in.acc) == 0;
    in.touched = (uint8_t)__atomic_exchange_n(&s_touched, 0, __ATOMIC_RELAXED);
    cali_status_get(&st, s_t, now_ms);
    cali_display_model(&s_model, &st, now_ms, LANG, &in, cali_display_cfg(), &v);
    __atomic_store_n(&s_awake, v.state == CALI_DISP_ON, __ATOMIC_RELAXED);

    paint_row(&s_rows[0], &v.device);
    paint_row(&s_rows[1], &v.wifi);
    paint_row(&s_rows[2], &v.camper);
    if (strcmp(s_footer_now, v.footer)) {
        memcpy(s_footer_now, v.footer, sizeof s_footer_now);
        lv_label_set_text(s_footer, v.footer);
    }
    bsp_display_unlock();
    cali_display_publish(&v);   /* /api/state device.display */
    touch_errors_evt(now_ms);

    /* only after a successful paint; can still block/abort inside the board package (cali_display.h) */
    if (s_brightness != v.brightness_pct) {
        s_brightness = v.brightness_pct;
        bsp_display_brightness_set(v.brightness_pct);
        cali_log("display: brightness %u", (unsigned)v.brightness_pct);
    }
}
