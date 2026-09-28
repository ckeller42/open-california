/*
 * cali-host — the firmware on the upstream NimBLE Linux port (#154): the same cali_core (console,
 * pairing runner, session) and NimBLE transport as the ESP32 build, talking HCI over TCP to a
 * (Bumble) controller. The e2e tests (tests/firmware/test_host_e2e.py) drive it through the console
 * line protocol on stdin/stdout (cali_core/include/cali_console.h).
 *
 * Usage: cali-host --hci-port <tcp-port> [--store <dir>] [--http <port> [--fake-wifi <script>]]
 *   --store      the kv-store directory (bond, WiFi credentials); default "."
 *   --http       run the WiFi side too (#154): cali_net over POSIX sockets on 127.0.0.1 with the
 *                scripted fake WiFi (platform/host/net_host.c), the WiFi runner (wifi_run.c), the
 *                captive DNS (UDP 53 — only where bindable, e.g. as root in the CI container) and the
 *                web endpoints on 127.0.0.1:<port>. Without it nothing network-related runs.
 *   --fake-wifi  the fake WiFi's script (cali_net_host.h); missing = no networks (ruling R2)
 *
 * Threads: main runs nimble_port_run() — the NimBLE host task; EVERY cali_core and transport call
 * happens there (the --http setup in main() runs before it, on the same thread). The HCI socket RX
 * thread is NimBLE's own. The stdin reader thread only queues lines and posts one event onto the
 * NimBLE default event queue; that event's callback feeds them to cali_console_line on the host
 * task. A 100 ms callout drives the tick: with --http first the network (cali_net_host_poll ->
 * cali_wifi_run_tick -> cali_captive_dns_poll -> cali_web_poll, each non-blocking), then
 * cali_runner_tick/cali_session_tick.
 */
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "host/ble_hs.h"
#include "nimble/nimble_npl.h"
#include "nimble/nimble_port.h"

#include "cali_ble_nimble.h"
#include "cali_captive.h"
#include "cali_console.h"
#include "cali_net_host.h"
#include "cali_platform.h"
#include "cali_runner.h"
#include "cali_session.h"
#include "cali_web.h"
#include "cali_wifi_run.h"

void ble_hci_sock_ack_handler(void *param);
void ble_hci_sock_set_device(int dev);

#define TICK_MS 100
#define NLINES 16
#define LINE_MAX_LEN 128

static char s_lines[NLINES][LINE_MAX_LEN];
static unsigned s_head, s_len;
static pthread_mutex_t s_mu = PTHREAD_MUTEX_INITIALIZER;
static struct ble_npl_event s_line_ev;
static struct ble_npl_callout s_tick;
static volatile int s_synced;   /* written once on the host task; read by the stdin thread */
static int s_http;              /* --http given: the network side runs on the tick */

/* Host task: feed every queued line to the console (only once the stack is synced). */
static void drain(void) {
    for (;;) {
        char line[LINE_MAX_LEN];
        pthread_mutex_lock(&s_mu);
        if (!s_len) {
            pthread_mutex_unlock(&s_mu);
            return;
        }
        memcpy(line, s_lines[s_head], sizeof line);
        memset(s_lines[s_head], 0, sizeof s_lines[s_head]);
        s_head = (s_head + 1) % NLINES;
        s_len--;
        pthread_mutex_unlock(&s_mu);
        cali_console_line(line);
        memset(line, 0, sizeof line);   /* a "wifi set" line holds a passphrase */
    }
}

static void line_ev_cb(struct ble_npl_event *ev) {
    (void)ev;
    if (s_synced) drain();
}

static void tick_cb(struct ble_npl_event *ev) {
    (void)ev;
    uint64_t now = cali_uptime_ms();
    if (s_http) {
        cali_net_host_poll(now);        /* WiFi events -> the runner's sink */
        cali_wifi_run_tick(now);
        cali_captive_dns_poll();
        cali_web_poll(now);
    }
    cali_runner_tick(now);
    cali_session_tick(now);
    ble_npl_callout_reset(&s_tick, ble_npl_time_ms_to_ticks32(TICK_MS));
}

static void on_sync(void) {
    s_synced = 1;
    cali_session_boot();          /* stored bond -> reconnect; none -> stay idle, never scan */
    cali_console_line("status");  /* the boot STATE line */
    drain();                      /* lines typed before the stack was ready */
}

static void on_quit(void) {
    fflush(stdout);
    exit(0);
}

/* stdin thread: queue the line and wake the host task. Never calls NimBLE or cali_core. */
static void post_line(const char *line) {
    for (;;) {
        pthread_mutex_lock(&s_mu);
        if (s_len < NLINES) {
            snprintf(s_lines[(s_head + s_len) % NLINES], LINE_MAX_LEN, "%s", line);
            s_len++;
            pthread_mutex_unlock(&s_mu);
            break;
        }
        pthread_mutex_unlock(&s_mu);
        usleep(10000);            /* the host task is behind: wait for a free slot */
    }
    ble_npl_eventq_put(nimble_port_get_dflt_eventq(), &s_line_ev);
}

static void *stdin_thread(void *arg) {
    (void)arg;
    char line[LINE_MAX_LEN];
    while (fgets(line, sizeof line, stdin) != NULL) {
        line[strcspn(line, "\r\n")] = 0;
        if (!s_synced && strcmp(line, "quit") == 0) exit(0);   /* the host never synced */
        post_line(line);
    }
    if (!s_synced) exit(0);
    post_line("quit");            /* stdin closed: same as quit */
    return NULL;
}

static void *hci_thread(void *arg) {
    ble_hci_sock_ack_handler(arg);
    return NULL;
}

/* A TCP port 1..65535, all decimal digits; else -1 (so "--http 0"/"--http abc" is a usage error, not
 * a silent run without the network side). */
static int parse_port(const char *s) {
    long v = 0;
    if (!*s) return -1;
    for (; *s; s++) {
        if (*s < '0' || *s > '9' || v > 65535) return -1;
        v = v * 10 + (*s - '0');
    }
    return v >= 1 && v <= 65535 ? (int)v : -1;
}

int main(int argc, char **argv) {
    int port = 0, http = 0;
    const char *store = NULL, *wifi_script = NULL;
    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--hci-port") == 0 && i + 1 < argc) port = atoi(argv[++i]);
        else if (strcmp(argv[i], "--store") == 0 && i + 1 < argc) store = argv[++i];
        else if (strcmp(argv[i], "--http") == 0 && i + 1 < argc) http = parse_port(argv[++i]);
        else if (strcmp(argv[i], "--fake-wifi") == 0 && i + 1 < argc) wifi_script = argv[++i];
        else port = 0, i = argc;  /* unknown argument: usage */
    }
    if (port <= 0 || http < 0 || (wifi_script && !http)) {
        fprintf(stderr, "usage: %s --hci-port <tcp-port> [--store <dir>] [--http <port> "
                        "[--fake-wifi <script>]]\n", argv[0]);
        return 2;
    }
    setvbuf(stdout, NULL, _IOLBF, 0);
    if (cali_platform_init(store) != 0) {
        fprintf(stderr, "cali-host: store directory %s unusable\n", store ? store : ".");
        return 2;
    }
    ble_hci_sock_set_device(port);

    nimble_port_init();           /* also connects the TCP HCI socket (ble_transport_ll_init) */
    cali_ble_nimble_init(on_sync);
    cali_console_on_quit = on_quit;
    cali_console_init(cali_ble_nimble_transport());

    if (http) {
        if (cali_net_host_init(wifi_script) != 0) {    /* the bad line is already logged */
            fprintf(stderr, "cali-host: fake-wifi script %s unusable\n", wifi_script);
            return 2;
        }
        cali_wifi_run_init(cali_net_host());
        cali_wifi_run_boot();       /* saved creds -> join; none -> setup hotspot (events on the tick) */
        if (cali_web_init(cali_net_host(), cali_ble_nimble_transport(), (uint16_t)http) != 0) {
            fprintf(stderr, "cali-host: cannot listen on http port %d\n", http);
            return 2;
        }
        s_http = 1;
    }

    ble_npl_event_init(&s_line_ev, line_ev_cb, NULL);
    ble_npl_callout_init(&s_tick, nimble_port_get_dflt_eventq(), tick_cb, NULL);
    ble_npl_callout_reset(&s_tick, ble_npl_time_ms_to_ticks32(TICK_MS));

    pthread_t hci, in;
    if (pthread_create(&hci, NULL, hci_thread, NULL) != 0 ||
        pthread_create(&in, NULL, stdin_thread, NULL) != 0) {
        fprintf(stderr, "cali-host: pthread_create failed\n");
        return 1;
    }
    nimble_port_run();
    return 0;
}
