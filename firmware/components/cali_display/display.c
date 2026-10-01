/* display.c — paints the pure status model (cali_display_model.h) on the CoreS3 screen (#154).
 * Runs on the owner task (cali_display_tick from do_work); LVGL renders on esp_lvgl_port's task, so
 * every LVGL call sits under bsp_display_lock. Only changed labels/dots are touched. */
#include <stdio.h>
#include <string.h>

#include "bsp/esp-bsp.h"
#include "cali_display.h"
#include "cali_display_model.h"
#include "cali_platform.h"
#include "cali_status.h"
#include "driver/i2c_master.h"
#include "lvgl.h"
#include "net_consts.h"

#define AXP2101_ADDR 0x34 /* power chip: BSP 4.1.0 src/bsp_feature_en.c BSP_AXP2101_ADDR */
#define AW9523_ADDR 0x58  /* IO expander: ESP_IO_EXPANDER_I2C_AW9523_ADDRESS_00 (BSP_IO_EXPANDER_ADDRESS) */
#define PROBE_MS 50

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

void cali_display_tick(uint64_t now_ms) {
    cali_status_t st;
    cali_display_view_t v;
    if (!s_ok || now_ms - s_last_paint < DISPLAY_REFRESH_MS) return;
    s_last_paint = now_ms;
    /* The owner task is the NimBLE host task: never wait on a wedged render. A skipped paint leaves
     * the model and the painted-text buffers untouched, so the next try repaints whatever changed. */
    if (!bsp_display_lock(DISPLAY_LOCK_TIMEOUT_MS)) return;
    cali_status_get(&st, s_t, now_ms);
    cali_display_model(&s_model, &st, now_ms, LANG, &v);

    paint_row(&s_rows[0], &v.device);
    paint_row(&s_rows[1], &v.wifi);
    paint_row(&s_rows[2], &v.camper);
    if (strcmp(s_footer_now, v.footer)) {
        memcpy(s_footer_now, v.footer, sizeof s_footer_now);
        lv_label_set_text(s_footer, v.footer);
    }
    bsp_display_unlock();

    /* only after a successful paint; can still block/abort inside the board package (cali_display.h) */
    if (s_brightness != v.brightness_pct) {
        s_brightness = v.brightness_pct;
        bsp_display_brightness_set(v.brightness_pct);
        cali_log("display: brightness %u", (unsigned)v.brightness_pct);
    }
}
