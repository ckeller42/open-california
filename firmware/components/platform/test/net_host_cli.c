/* net_host_cli.c — line-protocol driver for tests/firmware/test_net_host.py ONLY; not part of the
 * ESP build. Drives the host cali_net (platform/host/net_host.c) through its table only.
 *
 * Usage: net_host_cli FAKE_WIFI_SCRIPT (may be missing: no networks), then lines on stdin:
 *   sta_start SSID PSK | sta_stop | scan    the WiFi calls (PSK "-" = open)
 *   ap_start [SSID PSK] | ap_stop           default SSID/PSK = NET_AP_SSID / NET_AP_PSK
 *   poll N                                  advance the virtual clock by N ms, cali_net_host_poll()
 *   rssi                                    prints "RSSI <dBm>"
 *   mark                                    prints "MARK" (orders output against events)
 *   aps                                     prints the last SCAN_DONE's list (copied in the sink),
 *                                           one "AP <ssid> <rssi> <secure>" per network
 *   tcp_echo                                tcp_listen(0), prints "PORT <n>", serves ONE client:
 *                                           every received byte upper-cased back, until it closes
 *   udp_echo                                udp_bind(0), prints "PORT <n>", answers ONE datagram
 *                                           upper-cased to its sender, prints "FROM <a.b.c.d>"
 *   quit
 * Every event prints "EV <name> <reason> <ip|0> <nscan>". A call that returns nonzero prints "ERR <cmd> <rc>". Exit 2 when the
 * script is malformed.
 */
#define _POSIX_C_SOURCE 200809L /* nanosleep */

#include <ctype.h>
#include <stdio.h>
#include <string.h>
#include <time.h>

#include "cali_net_host.h"

static const char *const EV_NAMES[] = {"STA_GOT_IP", "STA_LOST", "STA_FAILED",
                                       "AP_STARTED", "AP_STOPPED", "SCAN_DONE"};
static const char *const REASON_NAMES[] = {"NONE", "NOT_FOUND", "AUTH", "OTHER"};

static cali_net_ap_t s_scan[NET_SCAN_MAX];
static int s_nscan;

static void print_ip(uint32_t ip) {
    if (ip == 0) fputs("0", stdout);
    else printf("%u.%u.%u.%u", (unsigned)(ip >> 24), (unsigned)(ip >> 16 & 0xff),
                (unsigned)(ip >> 8 & 0xff), (unsigned)(ip & 0xff));
}

static void sink(const cali_net_event_t *e, void *ctx) {
    (void)ctx;
    printf("EV %s %s ", EV_NAMES[e->ev], REASON_NAMES[e->reason]);
    print_ip(e->ip);
    printf(" %d\n", e->nscan);
    if (e->ev == CALI_NET_EV_SCAN_DONE) { /* e->scan is valid only during this call: copy it */
        s_nscan = e->nscan < NET_SCAN_MAX ? e->nscan : NET_SCAN_MAX;
        memcpy(s_scan, e->scan, (size_t)s_nscan * sizeof s_scan[0]);
    }
}

static void nap(void) {
    struct timespec ts = {0, 1000000L}; /* 1 ms */
    nanosleep(&ts, NULL);
}

#define IO_BUDGET 10000 /* 1 ms naps: a stuck peer ends the command after ~10 s, never hangs CI */

static void tcp_echo(const cali_net_t *net) {
    int lfd = net->tcp_listen(0), fd = -1;
    char buf[256];
    if (lfd < 0) {
        printf("ERR tcp_listen %d\n", lfd);
        return;
    }
    printf("PORT %d\n", cali_net_host_bound_port(lfd));
    fflush(stdout);
    for (int t = 0; t < IO_BUDGET && fd < 0; t++) {
        fd = net->tcp_accept(lfd);
        if (fd == -2) break;
        if (fd < 0) nap();
    }
    for (int t = 0; fd >= 0 && t < IO_BUDGET; t++) {
        int n = net->tcp_recv(fd, buf, sizeof buf);
        if (n == -2) break;
        if (n == -1) {
            nap();
            continue;
        }
        for (int i = 0; i < n; i++) buf[i] = (char)toupper((unsigned char)buf[i]);
        for (int off = 0; off < n && t < IO_BUDGET; t++) {
            int s = net->tcp_send(fd, buf + off, (size_t)(n - off));
            if (s == -2) break;
            if (s == -1) nap();
            else off += s;
        }
    }
    net->tcp_close(fd);
    net->tcp_close(lfd);
}

static void udp_echo(const cali_net_t *net) {
    int fd = net->udp_bind(0);
    char buf[512];
    if (fd < 0) {
        printf("ERR udp_bind %d\n", fd);
        return;
    }
    printf("PORT %d\n", cali_net_host_bound_port(fd));
    fflush(stdout);
    for (int t = 0; t < IO_BUDGET; t++) {
        uint32_t ip = 0;
        uint16_t port = 0;
        int n = net->udp_recvfrom(fd, buf, sizeof buf, &ip, &port);
        if (n == -1) {
            nap();
            continue;
        }
        if (n >= 0) {
            for (int i = 0; i < n; i++) buf[i] = (char)toupper((unsigned char)buf[i]);
            if (net->udp_sendto(fd, buf, (size_t)n, ip, port) != n) printf("ERR udp_sendto\n");
            fputs("FROM ", stdout);
            print_ip(ip);
            putchar('\n');
        }
        break;
    }
    net->tcp_close(fd); /* close() either way on the host */
}

int main(int argc, char **argv) {
    char line[512];
    uint64_t now = 0;
    const cali_net_t *net = cali_net_host();

    setvbuf(stdout, NULL, _IOLBF, 0);
    if (argc != 2) {
        fprintf(stderr, "usage: net_host_cli FAKE_WIFI_SCRIPT\n");
        return 2;
    }
    if (cali_net_host_init(argv[1]) != 0) return 2;
    net->set_sink(sink, NULL);

    while (fgets(line, sizeof line, stdin)) {
        char cmd[16], a[128] = "", b[128] = "";
        int n = sscanf(line, "%15s %127s %127s", cmd, a, b), rc = 0;
        const char *psk = strcmp(b, "-") == 0 ? "" : b;
        if (n < 1) continue;

        if (strcmp(cmd, "sta_start") == 0) rc = net->sta_start(a, psk);
        else if (strcmp(cmd, "sta_stop") == 0) rc = net->sta_stop();
        else if (strcmp(cmd, "scan") == 0) rc = net->scan();
        else if (strcmp(cmd, "ap_start") == 0)
            rc = n >= 3 ? net->ap_start(a, psk) : net->ap_start(NET_AP_SSID, NET_AP_PSK);
        else if (strcmp(cmd, "ap_stop") == 0) rc = net->ap_stop();
        else if (strcmp(cmd, "poll") == 0) {
            unsigned long dt = 0;
            sscanf(a, "%lu", &dt);
            now += dt;
            cali_net_host_poll(now);
        } else if (strcmp(cmd, "rssi") == 0) printf("RSSI %d\n", net->sta_rssi());
        else if (strcmp(cmd, "mark") == 0) puts("MARK");
        else if (strcmp(cmd, "aps") == 0)
            for (int i = 0; i < s_nscan; i++)
                printf("AP %s %d %d\n", s_scan[i].ssid, s_scan[i].rssi, s_scan[i].secure);
        else if (strcmp(cmd, "tcp_echo") == 0) tcp_echo(net);
        else if (strcmp(cmd, "udp_echo") == 0) udp_echo(net);
        else if (strcmp(cmd, "quit") == 0) break;
        else printf("ERR unknown %s\n", cmd);
        if (rc != 0) printf("ERR %s %d\n", cmd, rc);
    }
    return 0;
}
