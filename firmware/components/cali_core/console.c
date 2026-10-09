/* console.c — the line protocol (#154). Contract: include/cali_console.h. */
#include "cali_console.h"

#include <ctype.h>
#include <stdio.h>
#include <string.h>

#include "cali_control.h"
#include "cali_json.h"
#include "cali_platform.h"
#include "cali_runner.h"
#include "cali_session.h"
#include "cali_snapshot.h"
#include "cali_wifi_run.h"
#include "pairing_consts.h"

void (*cali_console_on_quit)(void);

static const cali_transport_t *s_t;

/* "wifi set <32-byte ssid> <63-byte psk>" is 105 bytes: room for it plus whitespace */
#define CONSOLE_LINE_MAX 160

/* A SNAP of every function (14 functions, ~160 fields) is ~4.5 KB; built whole (behind the "STATE
 * "/"SNAP " line prefix, via cali_json), then written with one fputs so no other output can land
 * inside it. */
#define SNAP_MAX 8192
static char s_buf[SNAP_MAX];
static int s_over;

#define PREFIX_STATE "STATE "
#define PREFIX_SNAP "SNAP "

/* Writes prefix into s_buf and starts a cali_json_t right after it. */
static void begin_line(cali_json_t *j, const char *prefix) {
    size_t n = strlen(prefix);
    memcpy(s_buf, prefix, n);
    cali_json_begin(j, s_buf + n, SNAP_MAX - n);
}

/* Closes j's document, appends the trailing newline, and sets s_over on any overflow (of the
 * JSON itself, or of the one extra byte the newline needs). */
static void finish_line(cali_json_t *j, const char *prefix) {
    int n = cali_json_end(j);
    if (n < 0) {
        s_over = 1;
        return;
    }
    size_t total = strlen(prefix) + (size_t)n;
    if (total + 1 >= SNAP_MAX) {
        s_over = 1;
        return;
    }
    s_buf[total] = '\n';
    s_buf[total + 1] = '\0';
    s_over = 0;
}

static void out_line(void) {
    if (s_over) {
        cali_log("console: output line longer than %u bytes dropped", (unsigned)SNAP_MAX);
        return;
    }
    fputs(s_buf, stdout);
    fflush(stdout);
}

/* 1 once the WiFi runtime booted (host --http): it never returns to UNPROVISIONED after boot. */
static int wifi_on(void) { return cali_wifi_run_state()->st != WIFI_UNPROVISIONED; }

static void ip_text(uint32_t ip, char out[16]) {
    snprintf(out, 16, "%u.%u.%u.%u", (unsigned)(ip >> 24), (unsigned)(ip >> 16 & 0xffu),
             (unsigned)(ip >> 8 & 0xffu), (unsigned)(ip & 0xffu));
}

/* Internal-heap stats for the STATE line (the kws-de voice-satellite feasibility check): the ESP
 * build overrides this (app_main.c, heap_caps of MALLOC_CAP_INTERNAL); the host/fake tier keeps
 * the 0s and STATE omits the members, so every pinned STATE string stays as it is. */
__attribute__((weak)) void cali_heap_stats(unsigned *free_bytes, unsigned *largest) {
    *free_bytes = 0;
    *largest = 0;
}

/* The STATE line; with_wifi adds the "wifi" member (console "status" once the WiFi runtime runs). */
static void state_line(const cali_pair_state_t *s, const char *address, int with_wifi) {
    unsigned hfree, hlfb;
    cali_json_t j;
    begin_line(&j, PREFIX_STATE);
    cali_snapshot_pairing(&j, s, address);
    cali_heap_stats(&hfree, &hlfb);
    if (hfree || hlfb) {
        cali_json_key(&j, "heap_int_free");
        cali_json_int(&j, (long long)hfree);
        cali_json_key(&j, "heap_int_lfb");
        cali_json_int(&j, (long long)hlfb);
    }
    if (with_wifi) {
        const char *ssid = cali_wifi_run_ssid();
        uint32_t ip = cali_wifi_run_ip();
        char a[16];
        cali_json_key(&j, "wifi");
        cali_json_obj_begin(&j);
        cali_json_key(&j, "mode");
        cali_json_str(&j, cali_wifi_mode_name(cali_wifi_mode(cali_wifi_run_state())));
        cali_json_key(&j, "ssid");
        if (ssid) cali_json_str(&j, ssid); else cali_json_null(&j);
        cali_json_key(&j, "ip");
        if (ip) {
            ip_text(ip, a);
            cali_json_str(&j, a);
        } else {
            cali_json_null(&j);
        }
        cali_json_obj_end(&j);
    }
    finish_line(&j, PREFIX_STATE);
    out_line();
}

void cali_console_state(const cali_pair_state_t *s, const char *address) { state_line(s, address, 0); }

void cali_console_snapshot(uint64_t t_ms) {
    cali_json_t j;
    begin_line(&j, PREFIX_SNAP);
    cali_json_key(&j, "t");
    cali_json_int(&j, (long long)t_ms);
    cali_json_key(&j, "fn");
    cali_json_obj_begin(&j);
    cali_snapshot_fn(&j);
    cali_json_obj_end(&j);
    finish_line(&j, PREFIX_SNAP);
    out_line();
}

/* calictl /api/pairing: with no pairing flow in this process it reports idle + the stored bond's
 * address (calictl/serve.py). The pairing SM is idle then; whether the link is up is the session's
 * business, not a pairing state. */
static void status(void) {
    state_line(cali_runner_state(), cali_snapshot_pair_address(s_t), wifi_on());
}

/* "wifi status": LOG wifi: <mode> ssid=<ssid|-> ip=<a.b.c.d|-> rssi=<dBm|-> scan=<n> */
static void wifi_status(void) {
    const char *ssid = cali_wifi_run_ssid();
    uint32_t ip = cali_wifi_run_ip();
    int rssi = cali_wifi_run_rssi();
    char a[16] = "-", r[12] = "-";
    if (ip) ip_text(ip, a);
    if (rssi) snprintf(r, sizeof r, "%d", rssi);
    cali_log("wifi: %s ssid=%s ip=%s rssi=%s scan=%d", cali_wifi_mode_name(cali_wifi_mode(cali_wifi_run_state())),
             ssid ? ssid : "-", a, r, cali_wifi_run_scan_list(NULL));
}

/* "wifi set <ssid> <psk>": both single tokens (an SSID with spaces cannot be typed here — use the
 * setup page), stored like POST /api/wifi (kv "wifi_ssid"/"wifi_psk", then the runner). args points
 * into the caller's line buffer, which the caller zeroes afterwards (it holds the passphrase). */
static void wifi_set(char *args) {
    char *ssid = strtok(args, " \t"), *psk = strtok(NULL, " \t");
    size_t ssid_len = ssid ? strlen(ssid) : 0, psk_len = psk ? strlen(psk) : 0;
    if (!ssid || !psk || strtok(NULL, " \t")) {
        cali_log("wifi: usage: wifi set <ssid> <psk>");
    } else if (ssid_len > NET_SSID_MAX) {
        cali_log("wifi: bad ssid");
    } else if (psk_len < NET_PSK_MIN || psk_len > NET_PSK_MAX) {
        cali_log("wifi: bad psk");
    } else if (cali_kv_set(CALI_WIFI_KEY_SSID, ssid, ssid_len) != 0 ||
               cali_kv_set(CALI_WIFI_KEY_PSK, psk, psk_len) != 0) {
        cali_log("wifi: storing credentials failed");
    } else {
        cali_wifi_run_set_creds(ssid, psk);
    }
}

/* Length of the first word of s (up to a space/tab): an unknown command is logged by its word(s)
 * only — the rest of a mistyped "wifi set" line is the passphrase. */
static int word_len(const char *s) {
    int n = 0;
    while (s[n] && s[n] != ' ' && s[n] != '\t') n++;
    return n;
}

static void wifi_cmd(char *sub) {
    if (!wifi_on()) {
        cali_log("wifi: not enabled");
    } else if (strcmp(sub, "status") == 0) {
        wifi_status();
    } else if (strcmp(sub, "scan") == 0) {
        cali_wifi_run_scan();
    } else if (strcmp(sub, "forget") == 0) {
        cali_wifi_run_forget();
    } else if (strcmp(sub, "set") == 0 || (strncmp(sub, "set", 3) == 0 && (sub[3] == ' ' || sub[3] == '\t'))) {
        wifi_set(sub + 3);
    } else {
        cali_log("unknown command: wifi %.*s", word_len(sub), sub);
    }
}

/* "set <fn> <what> [value]": the value is the rest of the line ("" = JSON null). Every outcome is
 * logged by the control module ("control: …"). */
static void set_cmd(char *args) {
    const char *reason;
    char *fn = strtok(args, " \t"), *what = fn ? strtok(NULL, " \t") : NULL;
    char *value = what ? strtok(NULL, "") : NULL;
    if (!fn || !what) {
        cali_log("control: usage: set <function> <what> [value]");
        return;
    }
    if (!value) value = "";
    while (*value == ' ' || *value == '\t') value++;
    (void)cali_ctl_submit(fn, what, value, -1, NULL, &reason);   /* no page, no clock */
}

static void on_state(const cali_pair_state_t *s, const char *address) {
    cali_console_state(s, address);
    if (s->st == PAIR_BONDED) {
        cali_session_on_bonded();
    } else {
        cali_session_stop();
        /* the flow ended without a new bond (cancel, error, reset): a kept bond reconnects as at
         * boot (calictl's poll resumes once the flow is idle/error); no bond -> nothing */
        if (s->st == PAIR_IDLE || s->st == PAIR_ERROR) cali_session_boot();
    }
}

void cali_console_init(const cali_transport_t *t) {
    s_t = t;
    cali_runner_init(t);
    cali_session_init(t);
    cali_runner_on_state = on_state;
}

/* memset that the compiler may not drop as a dead store (the buffer dies right after). */
static void wipe(void *p, size_t n) {
    volatile unsigned char *v = (volatile unsigned char *)p;
    while (n--) *v++ = 0;
}

/* "N" of "passkey N": 1-6 decimal digits and nothing else, else -1. */
static long parse_passkey(const char *p) {
    long v = 0;
    int n = 0;
    while (*p == ' ' || *p == '\t') p++;
    for (; isdigit((unsigned char)*p); p++, n++) {
        if (n == 6) return -1;
        v = v * 10 + (*p - '0');
    }
    return (n == 0 || *p) ? -1 : v;
}

void cali_console_line(const char *line) {
    char cmd[CONSOLE_LINE_MAX];
    size_t n = 0;
    while (*line && isspace((unsigned char)*line)) line++;
    while (line[n] && n < sizeof cmd - 1) {
        cmd[n] = line[n];
        n++;
    }
    while (n && isspace((unsigned char)cmd[n - 1])) n--;
    cmd[n] = 0;
    if (!n) return;

    if (strcmp(cmd, "pair") == 0) {
        cali_runner_start();
    } else if (strncmp(cmd, "passkey", 7) == 0 && (cmd[7] == ' ' || cmd[7] == '\t')) {
        long pk = parse_passkey(cmd + 7);
        if (pk < 0) cali_log("console: bad passkey");
        else cali_runner_passkey((uint32_t)pk);       /* ignored unless waiting_passkey */
    } else if (strcmp(cmd, "forget") == 0) {
        cali_runner_forget();
    } else if (strcmp(cmd, "status") == 0) {
        status();
    } else if (strcmp(cmd, "quit") == 0) {
        if (cali_console_on_quit) cali_console_on_quit();
    } else if (strncmp(cmd, "wifi", 4) == 0 && (cmd[4] == ' ' || cmd[4] == '\t')) {
        char *sub = cmd + 5;
        while (*sub == ' ' || *sub == '\t') sub++;
        wifi_cmd(sub);
    } else if (strcmp(cmd, "wifi") == 0) {
        wifi_cmd(cmd + 4);
    } else if (strncmp(cmd, "set", 3) == 0 && (cmd[3] == ' ' || cmd[3] == '\t' || cmd[3] == 0)) {
        set_cmd(cmd + 3);
    } else {
        cali_log("unknown command: %.*s", word_len(cmd), cmd);
    }
    wipe(cmd, sizeof cmd);   /* a "wifi set" line held a passphrase */
}
