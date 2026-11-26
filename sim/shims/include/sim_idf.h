/* sim_idf.h — the slice of ESP-IDF, FreeRTOS and the m5stack_core_s3 board package that the ESP-only
 * drivers use, for a Linux host build of ESP32-S3 / M5Stack CoreS3 firmware on simulated hardware (coresim,
 * sim/README.md).
 * Every header the drivers include (freertos/..., driver/..., bsp/esp-bsp.h, esp_*.h) is a one-line
 * include of this file. The declarations follow the IDF v6.1 / board package 4.1.0 signatures; only
 * what the drivers call is here. sim_rtos.c and sim_hw.c implement it. */
#ifndef SIM_IDF_H
#define SIM_IDF_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/* ---- esp_err ---- */
typedef int esp_err_t;
#define ESP_OK 0
#define ESP_FAIL -1
#define ESP_ERR_NOT_FOUND 0x105
#define ESP_ERR_TIMEOUT 0x107

/* ---- FreeRTOS: one virtual clock, 1 tick = 1 ms (CONFIG_FREERTOS_HZ=1000) ---- */
typedef uint32_t TickType_t;
typedef int BaseType_t;
typedef unsigned UBaseType_t;
#define pdTRUE 1
#define pdFALSE 0
#define pdPASS 1
#define errQUEUE_FULL 0
#define portMAX_DELAY ((TickType_t)0xffffffffu)
#define pdMS_TO_TICKS(ms) ((TickType_t)(ms))
typedef struct sim_queue *QueueHandle_t;
typedef struct sim_queue *SemaphoreHandle_t;
typedef void *TaskHandle_t;
typedef void (*TaskFunction_t)(void *);
#define MALLOC_CAP_SPIRAM (1u << 10)

QueueHandle_t xQueueCreate(UBaseType_t len, UBaseType_t item_size);
QueueHandle_t xQueueCreateWithCaps(UBaseType_t len, UBaseType_t item_size, uint32_t caps);
BaseType_t xQueueSend(QueueHandle_t q, const void *item, TickType_t wait);
BaseType_t xQueueOverwrite(QueueHandle_t q, const void *item);
BaseType_t xQueueReceive(QueueHandle_t q, void *out, TickType_t wait);
BaseType_t xQueueReset(QueueHandle_t q);
SemaphoreHandle_t xSemaphoreCreateMutex(void);
BaseType_t xSemaphoreTake(SemaphoreHandle_t m, TickType_t wait);
BaseType_t xSemaphoreGive(SemaphoreHandle_t m);
BaseType_t xTaskCreate(TaskFunction_t fn, const char *name, uint32_t stack, void *arg, UBaseType_t prio,
                       TaskHandle_t *out);
void vTaskDelay(TickType_t ticks);
TickType_t xTaskGetTickCount(void);

/* ---- GPIO ---- */
typedef int gpio_num_t;
typedef enum { GPIO_MODE_INPUT = 1, GPIO_MODE_OUTPUT = 2 } gpio_mode_t;
esp_err_t gpio_set_direction(gpio_num_t pin, gpio_mode_t mode);
esp_err_t gpio_set_level(gpio_num_t pin, uint32_t level);

/* ---- UART ---- */
typedef int uart_port_t;
#define UART_NUM_1 1
#define UART_PIN_NO_CHANGE (-1)
typedef enum { UART_DATA_8_BITS = 3 } uart_word_length_t;
typedef enum { UART_PARITY_DISABLE = 0 } uart_parity_t;
typedef enum { UART_STOP_BITS_1 = 1 } uart_stop_bits_t;
typedef enum { UART_HW_FLOWCTRL_DISABLE = 0 } uart_hw_flowcontrol_t;
typedef enum { UART_SCLK_DEFAULT = 0 } uart_sclk_t;
typedef struct {
    int baud_rate;
    uart_word_length_t data_bits;
    uart_parity_t parity;
    uart_stop_bits_t stop_bits;
    uart_hw_flowcontrol_t flow_ctrl;
    uart_sclk_t source_clk;
} uart_config_t;
esp_err_t uart_driver_install(uart_port_t port, int rx_buf, int tx_buf, int queue_size, QueueHandle_t *queue,
                              int intr_flags);
esp_err_t uart_driver_delete(uart_port_t port);
esp_err_t uart_param_config(uart_port_t port, const uart_config_t *cfg);
esp_err_t uart_set_pin(uart_port_t port, int tx, int rx, int rts, int cts);
int uart_read_bytes(uart_port_t port, void *buf, uint32_t len, TickType_t wait);
int uart_write_bytes(uart_port_t port, const void *src, size_t len);

/* ---- I2C master (the bus the board package owns) ---- */
typedef struct sim_i2c_bus *i2c_master_bus_handle_t;
esp_err_t i2c_master_probe(i2c_master_bus_handle_t bus, uint16_t addr, int timeout_ms);

/* ---- esp_io_expander (+ the AW9523 driver behind bsp_io_expander_init) ---- */
typedef struct sim_io_expander *esp_io_expander_handle_t;
typedef enum { IO_EXPANDER_INPUT, IO_EXPANDER_OUTPUT } esp_io_expander_dir_t;
#define IO_EXPANDER_PIN_NUM_1 (1u << 1)
#define IO_EXPANDER_PIN_NUM_4 (1u << 4)
#define IO_EXPANDER_PIN_NUM_15 (1u << 15)
esp_err_t esp_io_expander_set_dir(esp_io_expander_handle_t h, uint32_t mask, esp_io_expander_dir_t dir);
esp_err_t esp_io_expander_set_level(esp_io_expander_handle_t h, uint32_t mask, uint8_t level);

/* ---- the board package (espressif/m5stack_core_s3, BSP_ERROR_CHECK=y) ---- */
esp_err_t bsp_i2c_init(void);
i2c_master_bus_handle_t bsp_i2c_get_handle(void);
esp_io_expander_handle_t bsp_io_expander_init(void);
bool bsp_display_lock(uint32_t timeout_ms);
void bsp_display_unlock(void);

/* ---- app desc, RNG ---- */
typedef struct {
    char version[32];
} esp_app_desc_t;
const esp_app_desc_t *esp_app_get_description(void);
void esp_fill_random(void *buf, size_t len);
uint32_t esp_random(void);

/* ---- the simulator itself (sim_rtos.c), for sim_hw.c and the sim's main ---- */
uint64_t sim_now_ms(void);
/* Block the calling task until sim_notify(obj) or the virtual time reaches deadline_ms (UINT64_MAX = never). */
void sim_block(const void *obj, uint64_t deadline_ms);
void sim_notify(const void *obj);
/* An external event at virtual time at_ms: fire(arg) runs on the scheduler, between task switches. */
void sim_at(uint64_t at_ms, void (*fire)(void *), void *arg);
/* Make the calling (main) thread the first task and run the scheduler; until_ms ends the run (exit 0). */
void sim_start(uint64_t until_ms);
#endif
