/* sim_hw.c — the pins, the UART, the I2C bus and the board package of sim_idf.h, wired to the hardware
 * fakes in Python (the coresim package, sim/coresim) over stdin/stdout, in lockstep.
 *
 * Every hardware action is one request line on stdout, "HW <now_ms> <what...>"; the fakes answer with
 * zero or more lines and "END", while the virtual clock stands still:
 *   ACK | NACK              the I2C device acknowledged (or not) the address
 *   DATA <hex>              the bytes an I2C read returns (with ACK)
 *   EV <delay_ms> RX <text> the module sends <text>\r\n on UART1 after <delay_ms>
 *   EV <delay_ms> RXCLEAR   the module restarted: RX text it had scheduled and not yet sent never comes
 *   EV <delay_ms> <other>   handed to sim_event() of the firmware's sim main (its own events)
 *   QUIT                    end the run (exit 0)
 * Requests: BOOT | UART <line the firmware wrote> | GPIO <pin> DIR in|out | GPIO <pin> LVL 0|1 |
 *   PINS <tx> <rx> (uart_set_pin) | I2C PROBE <addr> | I2C W <addr> <hex> (one write transaction) |
 *   I2C R <addr> <written hex> <n> (a write, a repeated start, n bytes read) |
 *   whatever the firmware's sim main sends with sim_hw_req().
 *
 * The AW9523 driver here follows espressif/esp_io_expander_aw9523 + esp_io_expander (board package
 * 4.1.0): bsp_io_expander_init() creates it once (reset: 0x7F=00, 0x11=10, direction 0x04/05 = 0000,
 * output 0x02/03 = 0000) and set_dir/set_level are read-modify-writes of a cached register copy that
 * write only on a change (direction bit 0 = output on the AW9523). The board package's BSP_ERROR_CHECK=y
 * aborts on a failed init: so does this one. */
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "sim_idf.h"

#define AW9523_ADDR 0x58
#define RX_CAP 4096

void sim_event(const char *ev);   /* the firmware's sim main: an EV line other than RX */

static char s_rx[RX_CAP];
static size_t s_rx_head, s_rx_n;
static char s_tx[512];
static uint8_t s_data[64];   /* the last DATA answer */
static size_t s_data_n;
static size_t s_tx_n;

/* A scheduled RX text, tagged with the module generation it belongs to (RXCLEAR starts a new one). */
typedef struct {
    unsigned gen;
    char text[];
} rx_ev_t;
static unsigned s_rx_gen;

static void rx_fire(void *arg) {
    rx_ev_t *ev = arg;
    for (const char *p = ev->text; ev->gen == s_rx_gen && *p; p++) {
        if (s_rx_n == RX_CAP) break;   /* a full ring drops bytes, as the driver's RX buffer would */
        s_rx[(s_rx_head + s_rx_n++) % RX_CAP] = *p;
    }
    free(ev);
    sim_notify(s_rx);
}

static void ev_fire(void *arg) {
    sim_event(arg);
    free(arg);
}

/* One request, its answer read up to "END". 1 = ACK, 0 = NACK, -1 = neither. */
int sim_hw_req(const char *fmt, ...) {
    char line[1024];
    va_list ap;
    int ack = -1;
    printf("HW %llu ", (unsigned long long)sim_now_ms());
    va_start(ap, fmt);
    vprintf(fmt, ap);
    va_end(ap);
    putchar('\n');
    fflush(stdout);
    while (fgets(line, sizeof line, stdin)) {
        line[strcspn(line, "\r\n")] = 0;
        unsigned long long delay;
        int off = 0;
        if (!strcmp(line, "END")) return ack;
        if (!strcmp(line, "ACK")) ack = 1;
        else if (!strcmp(line, "NACK")) ack = 0;
        else if (!strcmp(line, "QUIT")) exit(0);
        else if (!strncmp(line, "DATA ", 5))
            for (s_data_n = 0; s_data_n < sizeof s_data && sscanf(line + 5 + 2 * s_data_n, "%2hhx", &s_data[s_data_n]) == 1;)
                s_data_n++;
        else if (sscanf(line, "EV %llu %n", &delay, &off) == 1 && off) {
            const char *what = line + off;
            if (!strcmp(what, "RXCLEAR")) {
                s_rx_gen++;   /* at once, whatever the delay: the restart is now */
            } else if (!strncmp(what, "RX ", 3)) {
                size_t n = strlen(what + 3);
                rx_ev_t *ev = malloc(sizeof *ev + n + 3);
                if (!ev) abort();
                ev->gen = s_rx_gen;
                memcpy(ev->text, what + 3, n);
                memcpy(ev->text + n, "\r\n", 3);
                sim_at(sim_now_ms() + delay, rx_fire, ev);
            } else {
                char *copy = strdup(what);
                if (!copy) abort();
                sim_at(sim_now_ms() + delay, ev_fire, copy);
            }
        } else {
            fprintf(stdout, "SIM bad answer: %s\n", line);
            exit(4);
        }
    }
    exit(0);   /* the fakes are gone */
}

/* ---- GPIO ---- */

esp_err_t gpio_set_direction(gpio_num_t pin, gpio_mode_t mode) {
    sim_hw_req("GPIO %d DIR %s", pin, mode == GPIO_MODE_OUTPUT ? "out" : "in");
    return ESP_OK;
}

esp_err_t gpio_set_level(gpio_num_t pin, uint32_t level) {
    sim_hw_req("GPIO %d LVL %u", pin, level ? 1u : 0u);
    return ESP_OK;
}

/* ---- UART1 ---- */

static int s_installed;

esp_err_t uart_driver_install(uart_port_t port, int rx_buf, int tx_buf, int queue_size, QueueHandle_t *queue,
                              int intr_flags) {
    (void)port, (void)rx_buf, (void)tx_buf, (void)queue_size, (void)queue, (void)intr_flags;
    if (s_installed) return ESP_FAIL;
    s_installed = 1;
    return ESP_OK;
}

esp_err_t uart_driver_delete(uart_port_t port) {
    (void)port;
    s_installed = 0;
    return ESP_OK;
}

esp_err_t uart_param_config(uart_port_t port, const uart_config_t *cfg) {
    (void)port;
    return cfg->baud_rate == 115200 ? ESP_OK : ESP_FAIL;   /* the RAK3172's default */
}

esp_err_t uart_set_pin(uart_port_t port, int tx, int rx, int rts, int cts) {
    (void)port, (void)rts, (void)cts;
    sim_hw_req("PINS %d %d", tx, rx);
    return ESP_OK;
}

int uart_read_bytes(uart_port_t port, void *buf, uint32_t len, TickType_t wait) {
    (void)port;
    uint64_t end = wait == portMAX_DELAY ? UINT64_MAX : sim_now_ms() + wait;
    while (!s_rx_n) {
        if (sim_now_ms() >= end) return 0;
        sim_block(s_rx, end);
    }
    uint32_t n = 0;
    for (; n < len && s_rx_n; n++, s_rx_n--, s_rx_head = (s_rx_head + 1) % RX_CAP) ((char *)buf)[n] = s_rx[s_rx_head];
    return (int)n;
}

/* Bytes to the module: one UART request per line (the AT driver ends each command with \r\n). */
int uart_write_bytes(uart_port_t port, const void *src, size_t len) {
    (void)port;
    for (size_t i = 0; i < len; i++) {
        char c = ((const char *)src)[i];
        if (c == '\n') {
            s_tx[s_tx_n] = 0;
            sim_hw_req("UART %s", s_tx);
            s_tx_n = 0;
        } else if (c != '\r' && s_tx_n < sizeof s_tx - 1) {
            s_tx[s_tx_n++] = c;
        }
    }
    return (int)len;
}

/* ---- I2C ---- */

struct sim_i2c_bus {
    int up;
};
static struct sim_i2c_bus s_bus;

esp_err_t i2c_master_probe(i2c_master_bus_handle_t bus, uint16_t addr, int timeout_ms) {
    (void)timeout_ms;
    if (!bus || !bus->up) return ESP_FAIL;
    return sim_hw_req("I2C PROBE %02x", addr) == 1 ? ESP_OK : ESP_ERR_NOT_FOUND;
}

static void hex(char *out, const uint8_t *b, size_t n) {
    out[0] = 0;
    for (size_t i = 0; i < n && i < 16; i++) sprintf(out + 2 * i, "%02x", b[i]);
}

static esp_err_t i2c_write(uint16_t addr, const uint8_t *data, size_t n) {
    char h[2 * 16 + 1];
    hex(h, data, n);
    return sim_hw_req("I2C W %02x %s", addr, h) == 1 ? ESP_OK : ESP_FAIL;
}

struct sim_i2c_dev {
    uint16_t addr;
};

esp_err_t i2c_master_bus_add_device(i2c_master_bus_handle_t bus, const i2c_device_config_t *cfg,
                                    i2c_master_dev_handle_t *out) {
    if (!bus || !bus->up || !(*out = calloc(1, sizeof **out))) return ESP_FAIL;
    (*out)->addr = cfg->device_address;
    return ESP_OK;
}

esp_err_t i2c_master_transmit(i2c_master_dev_handle_t dev, const uint8_t *buf, size_t n, int timeout_ms) {
    (void)timeout_ms;
    return i2c_write(dev->addr, buf, n);
}

esp_err_t i2c_master_transmit_receive(i2c_master_dev_handle_t dev, const uint8_t *wr, size_t wn, uint8_t *rd,
                                      size_t rn, int timeout_ms) {
    char h[2 * 16 + 1];
    (void)timeout_ms;
    hex(h, wr, wn);
    s_data_n = 0;
    if (sim_hw_req("I2C R %02x %s %u", dev->addr, h, (unsigned)rn) != 1) return ESP_FAIL;
    if (s_data_n != rn) {
        printf("SIM bad answer: I2C R %02x wants %u bytes, got %u\n", dev->addr, (unsigned)rn, (unsigned)s_data_n);
        exit(4);
    }
    memcpy(rd, s_data, rn);
    return ESP_OK;
}

/* ---- the AW9523 behind esp_io_expander ---- */

struct sim_io_expander {
    uint32_t output, direction;   /* the driver's copies of 0x02/03 and 0x04/05 */
};
static struct sim_io_expander s_aw;
static int s_aw_up;

static esp_err_t aw_write16(uint8_t reg, uint32_t v) {
    const uint8_t d[3] = {reg, (uint8_t)(v & 0xff), (uint8_t)(v >> 8)};
    return i2c_write(AW9523_ADDR, d, sizeof d);
}

esp_err_t esp_io_expander_set_dir(esp_io_expander_handle_t h, uint32_t mask, esp_io_expander_dir_t dir) {
    uint32_t d = dir == IO_EXPANDER_OUTPUT ? h->direction & ~mask : h->direction | mask;   /* dir_out_bit_zero */
    if (d == h->direction) return ESP_OK;
    if (aw_write16(0x04, d) != ESP_OK) return ESP_FAIL;
    h->direction = d;
    return ESP_OK;
}

esp_err_t esp_io_expander_set_level(esp_io_expander_handle_t h, uint32_t mask, uint8_t level) {
    if (h->direction & mask) return ESP_FAIL;   /* ESP_ERR_INVALID_STATE: a pin in input mode */
    uint32_t o = level ? h->output | mask : h->output & ~mask;
    if (o == h->output) return ESP_OK;
    if (aw_write16(0x02, o) != ESP_OK) return ESP_FAIL;
    h->output = o;
    return ESP_OK;
}

static void bsp_abort(const char *what) {
    printf("SIM abort: BSP_ERROR_CHECK: %s\n", what);
    fflush(stdout);
    abort();
}

esp_err_t bsp_i2c_init(void) {
    s_bus.up = 1;
    return ESP_OK;
}

i2c_master_bus_handle_t bsp_i2c_get_handle(void) {
    bsp_i2c_init();
    return &s_bus;
}

esp_io_expander_handle_t bsp_io_expander_init(void) {
    if (s_aw_up) return &s_aw;
    bsp_i2c_init();
    const uint8_t rst[2] = {0x7f, 0x00}, gcr[2] = {0x11, 0x10};   /* reset, then P0 push-pull */
    if (i2c_write(AW9523_ADDR, rst, 2) != ESP_OK || i2c_write(AW9523_ADDR, gcr, 2) != ESP_OK ||
        aw_write16(0x04, 0) != ESP_OK || aw_write16(0x02, 0) != ESP_OK)
        bsp_abort("esp_io_expander_new_i2c_aw9523");
    s_aw = (struct sim_io_expander){0, 0};
    s_aw_up = 1;
    return &s_aw;
}

/* One task runs at a time and none blocks while it holds the lock: nothing to wait for. */
bool bsp_display_lock(uint32_t timeout_ms) { return (void)timeout_ms, true; }
void bsp_display_unlock(void) {}

/* ---- app desc, RNG ---- */

const esp_app_desc_t *esp_app_get_description(void) {
    static const esp_app_desc_t d = {"sim-host"};
    return &d;
}

void esp_fill_random(void *buf, size_t len) {   /* deterministic: a run repeats exactly */
    static uint32_t x = 0x12345678u;
    for (size_t i = 0; i < len; i++) {
        x ^= x << 13, x ^= x >> 17, x ^= x << 5;
        ((uint8_t *)buf)[i] = (uint8_t)x;
    }
}

uint32_t esp_random(void) {
    uint32_t v;
    esp_fill_random(&v, sizeof v);
    return v;
}
