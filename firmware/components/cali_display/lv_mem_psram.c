/*
 * LVGL's heap in PSRAM (CONFIG_LV_USE_CUSTOM_MALLOC). With the C library's malloc every LVGL object,
 * style and label text is smaller than CONFIG_SPIRAM_MALLOC_ALWAYSINTERNAL (16 KB) and lands in the
 * internal heap (13.5 KB measured on the board). Only the LVGL and owner tasks touch these objects,
 * never an ISR and never with the flash cache off; the LCD DMA reads esp_lvgl_port's own draw
 * buffer, not this heap. PSRAM first, the internal heap only if PSRAM is full.
 */
#include <stddef.h>

#include "esp_heap_caps.h"
#include "lvgl.h"

#define LV_MEM_CAPS_PSRAM (MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT)
#define LV_MEM_CAPS_INTERNAL (MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT)

void lv_mem_init(void) {}

void lv_mem_deinit(void) {}

lv_mem_pool_t lv_mem_add_pool(void *mem, size_t bytes) {
    LV_UNUSED(mem);
    LV_UNUSED(bytes);
    return NULL;   /* not supported, as with the C library's malloc */
}

void lv_mem_remove_pool(lv_mem_pool_t pool) { LV_UNUSED(pool); }

void *lv_malloc_core(size_t size) { return heap_caps_malloc_prefer(size, 2, LV_MEM_CAPS_PSRAM, LV_MEM_CAPS_INTERNAL); }

void *lv_realloc_core(void *p, size_t new_size) {
    return heap_caps_realloc_prefer(p, new_size, 2, LV_MEM_CAPS_PSRAM, LV_MEM_CAPS_INTERNAL);
}

void lv_free_core(void *p) { heap_caps_free(p); }

void lv_mem_monitor_core(lv_mem_monitor_t *mon_p) { LV_UNUSED(mon_p); }

lv_result_t lv_mem_test_core(void) { return LV_RESULT_OK; }
