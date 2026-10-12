/* ota.c — the firmware-update state machine (R_FW_OTA). Contract: include/cali_ota.h. */
#include "cali_ota.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "cali_diag.h"
#include "cali_platform.h"

static const char *const NAMES[] = {"idle", "checking", "up_to_date", "available",
                                    "downloading", "verifying", "rebooting", "error"};

static const cali_ota_ops_t *s_ops;
/* Shared with the OTA task: written there, read on the owner task (__atomic: two cores). s_sha is
 * written only while the state is CHECKING, which only the owner task enters (cali_ota_request),
 * so a reader that sees any other state reads a settled string. */
static int s_state, s_pending, s_pct;
static const char *s_error;
static char s_sha[41];
static uint64_t s_started;   /* uptime of the request (written before the OTA task is woken) */

/* Every transition is an ota diagnostics event (R_FW_DIAG_LOG); either task. */
static void set_state(int st) {
    const char *err = __atomic_load_n(&s_error, __ATOMIC_RELAXED);
    __atomic_store_n(&s_state, st, __ATOMIC_RELEASE);
    cali_diag_evt("ota", "\"state\":\"%s\",\"dur_ms\":%llu%s%s%s", NAMES[st],
                  (unsigned long long)(cali_uptime_ms() - s_started), st == CALI_OTA_ERROR && err ? ",\"error\":\"" : "",
                  st == CALI_OTA_ERROR && err ? err : "", st == CALI_OTA_ERROR && err ? "\"" : "");
}
int cali_ota_state(void) { return __atomic_load_n(&s_state, __ATOMIC_ACQUIRE); }

const char *cali_ota_state_name(int st) {
    return st >= 0 && st < (int)(sizeof NAMES / sizeof *NAMES) ? NAMES[st] : "error";
}

void cali_ota_init(const cali_ota_ops_t *ops) { s_ops = ops; }

int cali_ota_request(int install) {
    int st = cali_ota_state();
    if (!s_ops) return -1;
    if (st == CALI_OTA_CHECKING || st == CALI_OTA_DOWNLOADING || st == CALI_OTA_VERIFYING ||
        st == CALI_OTA_REBOOTING)
        return -2;
    s_sha[0] = 0;
    __atomic_store_n(&s_error, NULL, __ATOMIC_RELAXED);
    __atomic_store_n(&s_pct, 0, __ATOMIC_RELAXED);
    __atomic_store_n(&s_pending, install ? 2 : 1, __ATOMIC_RELAXED);
    s_started = cali_uptime_ms();
    set_state(CALI_OTA_CHECKING);
    s_ops->wake();
    return 0;
}

void cali_ota_progress(size_t done, size_t total) {
    int pct = total ? (int)(done >= total ? 100 : (uint64_t)done * 100 / total) : 0;
    __atomic_store_n(&s_pct, pct, __ATOMIC_RELAXED);
}

/* ---- manifest.json: {"sha":"<40 hex>","size":N,"sha256":"<64 hex>",...} ---- */

/* The value after "key" + optional whitespace + ':' + whitespace, or NULL. */
static const char *value_of(const char *body, const char *key) {
    size_t klen = strlen(key);
    for (const char *p = body; (p = strchr(p, '"')) != NULL; p++) {
        if (strncmp(p + 1, key, klen) != 0 || p[1 + klen] != '"') continue;
        const char *v = p + 2 + klen;
        while (*v == ' ' || *v == '\t' || *v == '\r' || *v == '\n') v++;
        if (*v++ != ':') continue;
        while (*v == ' ' || *v == '\t' || *v == '\r' || *v == '\n') v++;
        return v;
    }
    return NULL;
}

/* A quoted string of exactly n lowercase hex digits into out (n + 1 bytes): 0 ok, -1 not that. */
static int hex_str(const char *v, size_t n, char *out) {
    if (!v || *v != '"') return -1;
    for (size_t i = 0; i < n; i++) {
        char c = v[1 + i];
        if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'))) return -1;
        out[i] = c;
    }
    if (v[1 + n] != '"') return -1;
    out[n] = 0;
    return 0;
}

static int parse_manifest(const char *body, char sha[41], char sha256[65], size_t *size) {
    const char *v = value_of(body, "size");
    char *end;
    unsigned long n;
    if (hex_str(value_of(body, "sha"), 40, sha) != 0 || hex_str(value_of(body, "sha256"), 64, sha256) != 0 ||
        !v || *v < '1' || *v > '9')
        return -1;
    n = strtoul(v, &end, 10);
    if (n > CALI_OTA_SLOT_SIZE) return -1;
    *size = (size_t)n;
    return 0;
}

/* The running build is the manifest's commit: its version is a sha prefix (`git describe --always`,
 * >= 7 hex digits, no "-dirty"). Anything else (a dirty or tagged local build) is never current. */
static int is_running(const char *sha) {
    const char *fw = cali_fw_version();
    size_t n = strspn(fw, "0123456789abcdef");
    return n >= 7 && fw[n] == 0 && strncmp(fw, sha, n) == 0;
}

static void fail(const char *code) {
    __atomic_store_n(&s_error, code, __ATOMIC_RELAXED);
    set_state(CALI_OTA_ERROR);
    cali_log("ota: error %s", code);
}

void cali_ota_work(void) {
    static char body[CALI_OTA_MANIFEST_MAX];
    char url[sizeof CALI_OTA_MANIFEST_URL + 24], sha[41], want[65], got_hex[65];
    uint8_t got[32];
    size_t size;
    int req = __atomic_exchange_n(&s_pending, 0, __ATOMIC_ACQUIRE);
    if (!req || !s_ops) return;
    /* a fresh query per check: GitHub's edge served one client the old manifest's redirect for ~2 min
     * after CI replaced it (2026-10-11). The image itself is SHA-256 checked, so it needs none. */
    snprintf(url, sizeof url, CALI_OTA_MANIFEST_URL "?t=%llu", (unsigned long long)cali_uptime_ms());
    if (s_ops->get(url, body, sizeof body) < 0 || parse_manifest(body, sha, want, &size) != 0) {
        fail("manifest");
        return;
    }
    if (is_running(sha)) {
        cali_log("ota: up to date (%s)", cali_fw_version());
        set_state(CALI_OTA_UP_TO_DATE);
        return;
    }
    memcpy(s_sha, sha, sizeof s_sha);
    cali_log("ota: available %.7s (running %s)", sha, cali_fw_version());
    /* an install goes straight on: never a non-busy state in between, where the owner could start a
     * second request while this one still runs */
    set_state(req == 2 ? CALI_OTA_DOWNLOADING : CALI_OTA_AVAILABLE);
    if (req != 2) return;

    if (s_ops->download(CALI_OTA_BIN_URL, size) != 0) {
        s_ops->abort();
        fail("download");
        return;
    }
    set_state(CALI_OTA_VERIFYING);
    if (s_ops->slot_sha256(size, got) == 0)
        for (int i = 0; i < 32; i++) {
            static const char HEX[] = "0123456789abcdef";
            got_hex[2 * i] = HEX[got[i] >> 4];
            got_hex[2 * i + 1] = HEX[got[i] & 15];
        }
    else
        got_hex[0] = 0;
    got_hex[64] = 0;
    if (strcmp(got_hex, want) != 0) {   /* a release replaced mid-download, a torn write */
        s_ops->abort();
        fail("checksum");
        return;
    }
    if (s_ops->commit() != 0) {
        fail("commit");
        return;
    }
    cali_log("ota: installed %.7s, restarting", sha);
    set_state(CALI_OTA_REBOOTING);
    s_ops->restart();
}

void cali_ota_json(cali_json_t *j) {
    int st = cali_ota_state();
    const char *err = __atomic_load_n(&s_error, __ATOMIC_RELAXED);
    cali_json_obj_begin(j);
    cali_json_key(j, "state");
    cali_json_str(j, cali_ota_state_name(st));
    cali_json_key(j, "available_sha");
    if (st != CALI_OTA_CHECKING && s_sha[0]) cali_json_str(j, s_sha); else cali_json_null(j);
    cali_json_key(j, "progress_pct");
    cali_json_int(j, __atomic_load_n(&s_pct, __ATOMIC_RELAXED));
    cali_json_key(j, "error");
    if (st == CALI_OTA_ERROR && err) cali_json_str(j, err); else cali_json_null(j);
    cali_json_obj_end(j);
}
