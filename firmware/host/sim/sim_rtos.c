/* sim_rtos.c — FreeRTOS on a virtual clock, for the simulated-hardware host build (sim_idf.h).
 *
 * Every task is a pthread, but only one runs at a time (one global lock, handed from task to task like
 * a single-core scheduler without preemption). A task gives the CPU away only when it blocks (a delay,
 * a queue receive, a UART read). When no task is ready the clock jumps to the earliest deadline or
 * external event (sim_at): a 600 s wait costs no wall time, and a run is deterministic, because the
 * hardware fakes answer in lockstep (sim_hw.c) and nothing reads the wall clock. */
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "sim_idf.h"

#define NEVER UINT64_MAX

typedef struct sim_task {
    pthread_cond_t cv;
    int ready;
    uint64_t wake;      /* deadline while blocked, NEVER = only a notify */
    const void *wait;   /* the object it waits on */
    TaskFunction_t fn;
    void *arg;
    struct sim_task *next;
} sim_task;

typedef struct sim_ev {
    uint64_t at;
    unsigned seq;   /* same time: in the order they were added */
    void (*fire)(void *);
    void *arg;
    struct sim_ev *next;
} sim_ev;

static pthread_mutex_t s_mu = PTHREAD_MUTEX_INITIALIZER;
static sim_task *s_tasks, *s_cur;
static __thread sim_task *s_self;
static sim_ev *s_evs;   /* sorted by (at, seq) */
static unsigned s_seq;
static uint64_t s_now, s_until = NEVER;

uint64_t sim_now_ms(void) { return s_now; }

void sim_at(uint64_t at_ms, void (*fire)(void *), void *arg) {
    sim_ev *e = calloc(1, sizeof *e), **p = &s_evs;
    if (!e) abort();
    *e = (sim_ev){at_ms < s_now ? s_now : at_ms, s_seq++, fire, arg, NULL};
    while (*p && ((*p)->at < e->at || ((*p)->at == e->at && (*p)->seq < e->seq))) p = &(*p)->next;
    e->next = *p;
    *p = e;
}

void sim_notify(const void *obj) {
    for (sim_task *t = s_tasks; t; t = t->next)
        if (!t->ready && t->wait == obj) t->ready = 1, t->wake = NEVER;
}

/* Round robin from the task after the current one. */
static sim_task *pick(void) {
    sim_task *start = s_cur && s_cur->next ? s_cur->next : s_tasks;
    for (sim_task *t = start; t; t = t->next)
        if (t->ready) return t;
    for (sim_task *t = s_tasks; t != start; t = t->next)
        if (t->ready) return t;
    return NULL;
}

/* With the lock held and the caller not ready: hand the CPU to the next ready task, advancing the clock
 * (and firing the external events) while none is. */
static void schedule(void) {
    for (;;) {
        sim_task *t = pick();
        if (t) {
            s_cur = t;
            pthread_cond_signal(&t->cv);
            return;
        }
        uint64_t at = s_evs ? s_evs->at : NEVER;
        for (t = s_tasks; t; t = t->next)
            if (t->wake < at) at = t->wake;
        if (at == NEVER) {
            fprintf(stdout, "SIM deadlock at %llu ms: every task waits forever\n", (unsigned long long)s_now);
            exit(3);
        }
        if (at > s_until) {
            fprintf(stdout, "SIM end %llu\n", (unsigned long long)s_until);
            exit(0);
        }
        if (at > s_now) printf("T %llu\n", (unsigned long long)at);   /* the fakes stamp the log lines with it */
        s_now = at;
        while (s_evs && s_evs->at <= s_now) {
            sim_ev *e = s_evs;
            s_evs = e->next;
            e->fire(e->arg);
            free(e);
        }
        for (t = s_tasks; t; t = t->next)
            if (!t->ready && t->wake <= s_now) t->ready = 1, t->wake = NEVER;
    }
}

void sim_block(const void *obj, uint64_t deadline_ms) {
    sim_task *me = s_self;
    me->ready = 0;
    me->wait = obj;
    me->wake = deadline_ms;
    schedule();
    while (s_cur != me) pthread_cond_wait(&me->cv, &s_mu);
    me->wait = NULL;
}

static sim_task *task_new(void) {
    sim_task *t = calloc(1, sizeof *t), **p = &s_tasks;
    if (!t) abort();
    pthread_cond_init(&t->cv, NULL);
    t->ready = 1;
    t->wake = NEVER;
    while (*p) p = &(*p)->next;
    *p = t;
    return t;
}

static void *trampoline(void *arg) {
    sim_task *t = arg;
    pthread_mutex_lock(&s_mu);
    s_self = t;
    while (s_cur != t) pthread_cond_wait(&t->cv, &s_mu);
    t->fn(t->arg);
    for (;;) sim_block(t, NEVER);   /* a task function that returns: never runs again */
    return NULL;
}

BaseType_t xTaskCreate(TaskFunction_t fn, const char *name, uint32_t stack, void *arg, UBaseType_t prio,
                       TaskHandle_t *out) {
    (void)name, (void)stack, (void)prio;
    sim_task *t = task_new();
    pthread_t th;
    t->fn = fn;
    t->arg = arg;
    if (pthread_create(&th, NULL, trampoline, t) != 0) abort();
    pthread_detach(th);
    if (out) *out = t;
    return pdPASS;
}

void sim_start(uint64_t until_ms) {
    s_until = until_ms;
    pthread_mutex_lock(&s_mu);   /* held by whichever task runs, from here to the exit */
    s_self = s_cur = task_new();
}

void vTaskDelay(TickType_t ticks) { sim_block(NULL, s_now + ticks); }

TickType_t xTaskGetTickCount(void) { return (TickType_t)s_now; }

/* ---- queues; a mutex is a queue that is never waited on (one task runs at a time) ---- */

struct sim_queue {
    size_t len, size, head, n;
    uint8_t *buf;
};

QueueHandle_t xQueueCreate(UBaseType_t len, UBaseType_t item_size) {
    struct sim_queue *q = calloc(1, sizeof *q);
    if (!q || !(q->buf = calloc(len ? len : 1, item_size ? item_size : 1))) abort();
    q->len = len, q->size = item_size;
    return q;
}

QueueHandle_t xQueueCreateWithCaps(UBaseType_t len, UBaseType_t item_size, uint32_t caps) {
    (void)caps;   /* no PSRAM here: plain heap */
    return xQueueCreate(len, item_size);
}

BaseType_t xQueueSend(QueueHandle_t q, const void *item, TickType_t wait) {
    (void)wait;   /* the drivers only send with 0 */
    if (q->n == q->len) return errQUEUE_FULL;
    memcpy(q->buf + (q->head + q->n) % q->len * q->size, item, q->size);
    q->n++;
    sim_notify(q);
    return pdTRUE;
}

BaseType_t xQueueOverwrite(QueueHandle_t q, const void *item) {   /* a 1-deep queue, as FreeRTOS requires */
    memcpy(q->buf, item, q->size);
    q->head = 0, q->n = 1;
    sim_notify(q);
    return pdTRUE;
}

BaseType_t xQueueReceive(QueueHandle_t q, void *out, TickType_t wait) {
    uint64_t end = wait == portMAX_DELAY ? NEVER : s_now + wait;
    for (;;) {
        if (q->n) {
            memcpy(out, q->buf + q->head * q->size, q->size);
            q->head = (q->head + 1) % q->len;
            q->n--;
            return pdTRUE;
        }
        if (s_now >= end) return pdFALSE;
        sim_block(q, end);
    }
}

BaseType_t xQueueReset(QueueHandle_t q) {
    q->head = q->n = 0;
    return pdPASS;
}

SemaphoreHandle_t xSemaphoreCreateMutex(void) { return xQueueCreate(1, 1); }
BaseType_t xSemaphoreTake(SemaphoreHandle_t m, TickType_t wait) { return (void)m, (void)wait, pdTRUE; }
BaseType_t xSemaphoreGive(SemaphoreHandle_t m) { return (void)m, pdTRUE; }
