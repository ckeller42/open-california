/* rtos_check.c — sim_rtos.c on its own: two tasks, a queue, delays and an external event on the virtual
 * clock. Prints one line per step; tests/test_rtos.py checks the times. */
#include <stdio.h>
#include <stdlib.h>

#include "sim_idf.h"

static QueueHandle_t q;

static void producer(void *arg) {
    (void)arg;
    for (int i = 1; i <= 3; i++) {
        vTaskDelay(pdMS_TO_TICKS(600000));   /* 10 virtual minutes */
        xQueueSend(q, &i, 0);
    }
}

static void fire(void *arg) { printf("event %s at %llu\n", (const char *)arg, (unsigned long long)sim_now_ms()); }

int main(void) {
    setvbuf(stdout, NULL, _IOLBF, 0);
    sim_start(5000000);
    q = xQueueCreate(2, sizeof(int));
    sim_at(1234, fire, "x");
    xTaskCreate(producer, "p", 1024, NULL, 1, NULL);
    for (;;) {
        int v;
        if (xQueueReceive(q, &v, pdMS_TO_TICKS(1000000)) == pdTRUE) printf("got %d at %llu\n", v, (unsigned long long)sim_now_ms());
        else printf("timeout at %llu\n", (unsigned long long)sim_now_ms());
    }
}
