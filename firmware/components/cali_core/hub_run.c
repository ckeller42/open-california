/* hub_run.c — the remote hub client's owner-task logic (cali_hub.h: cali_hub_tick) over the port. Platform-free;
 * tests/firmware/test_hub_run.py drives it with a fake port. The data key and the replay ring live here, on the
 * owner task only (ruling R16); the port (hub_esp.c) only moves frames. */
#include <stdlib.h>
#include <string.h>

#include "cali_http.h"
#include "cali_hub.h"
#include "cali_lora.h" /* cali_lora_cmds_drop */
#include "cali_lora_setup.h" /* cali_wipe */
#include "cali_platform.h"
#include "cali_web.h"

#ifdef ESP_PLATFORM
#include "esp_heap_caps.h"
#define RUN_ALLOC(n) heap_caps_malloc_prefer((n), 2, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT)
#else
#define RUN_ALLOC malloc
#endif

#define FRAME_MAX (CALI_HUB_RES_MAX + CALI_HUB_OVERHEAD)

static cali_hub_crypto_t s_c;
static cali_hub_rpc_t s_rpc;            /* s_rpc.buf != NULL: a request is pending */
static char *s_pt, *s_res;              /* request / answer plaintexts (PSRAM first), allocated once */
static uint8_t *s_frame;                /* one sealed frame in or out */
static int s_restart = 1, s_have_cfg, s_key_ok, s_key_err, s_started, s_was_online, s_pub_due;
static uint64_t s_next_try, s_next_pub;
static volatile uint32_t s_last_pub_s; /* written by the port's task (cali_hub_published); one aligned word */

static int bufs(void) {
    if (!s_pt) s_pt = RUN_ALLOC(CALI_HUB_REQ_MAX + 1);
    if (!s_res) s_res = RUN_ALLOC(CALI_HUB_RES_MAX);
    if (!s_frame) s_frame = RUN_ALLOC(FRAME_MAX);
    return s_pt && s_res && s_frame;
}

/* load the config; make or load the key; start the port. Tried again after CALI_HUB_PUB_MS while it fails. */
static void try_start(uint64_t now_ms) {
    cali_hub_cfg_t cfg;
    s_next_try = now_ms + CALI_HUB_PUB_MS;
    s_have_cfg = cali_hub_cfg_load(&cfg) == 0;
    if (s_have_cfg && !s_key_ok) {
        s_key_ok = cali_hub_key_load_or_make(&s_c) == 0; /* -1: the context is not used */
        s_key_err = !s_key_ok;
        if (!s_key_ok) cali_log("hub: data key cannot be stored: retry in 30 s");
    }
    if (s_have_cfg && s_key_ok && bufs()) s_started = cali_hub_port_start(&cfg) == 0;
    cali_wipe(&cfg, sizeof cfg);
}

static void post(int topic, const char *pt, size_t n) {
    size_t m = cali_hub_seal(&s_c, CALI_HUB_DIR_ESP, (const uint8_t *)pt, n, s_frame, FRAME_MAX);
    if (!m || cali_hub_port_post(topic, s_frame, m) != 0)
        cali_log("hub: %s not sent (%u bytes)", topic == CALI_HUB_T_STATE ? "state" : "answer", (unsigned)n);
}

/* the /api/state body; "?hub=1": no viewer mark */
static void publish_state(uint64_t now_ms) {
    cali_http_req_t rq = {"GET", "/api/state", "hub=1", "", 0, 0, NULL, NULL, NULL};
    cali_http_resp_t rs = {200, "text/plain", NULL, 0, NULL, NULL, NULL, NULL};
    s_pub_due = 0;
    s_next_pub = now_ms + CALI_HUB_PUB_MS;
    if (cali_web_handle(&rq, &rs, NULL) == 1 && rs.status == 200 && rs.body) post(CALI_HUB_T_STATE, rs.body, rs.body_len);
}

static void answered(uint64_t now_ms) {
    if (cali_hub_port_online()) {
        post(CALI_HUB_T_RES, s_res, strlen(s_res));
        publish_state(now_ms);
    }
    cali_wipe(s_res, CALI_HUB_RES_MAX);
}

void cali_hub_tick(uint64_t now_ms) {
    int online;
    if (s_restart) {
        s_restart = 0;
        if (s_started) cali_hub_port_stop();
        s_started = s_was_online = 0;
        try_start(now_ms);
    } else if (s_have_cfg && !s_started && now_ms >= s_next_try) {
        try_start(now_ms);
    }
    if (!s_started) return;
    cali_hub_port_tick();
    online = cali_hub_port_online();
    if (online && !s_was_online) s_pub_due = 1; /* on connect: the retained state at once */
    s_was_online = online;
    if (cali_http_busy()) return; /* its body may be in the router's static buffer: no router call now */
    if (s_rpc.buf) {
        if (cali_hub_rpc_poll(&s_rpc, s_res, CALI_HUB_RES_MAX)) answered(now_ms);
    } else {
        int retained = 0;
        size_t n = cali_hub_port_take(s_frame, FRAME_MAX, &retained);
        if (n && retained) {
            /* the app never retains a request: a retained one (valid tag, fresh t) would run again at every
             * subscribe, and after a reboot the replay ring is empty */
            cali_log("hub: retained request dropped");
        } else if (n) {
            int k = cali_hub_open(&s_c, s_frame, n, (uint8_t *)s_pt, CALI_HUB_REQ_MAX, 1u << CALI_HUB_DIR_APP, NULL);
            if (k < 0) {
                cali_log("hub: request rejected (%u bytes)", (unsigned)n); /* bad tag, replay, wrong dir */
            } else {
                s_pt[k] = 0;
                if (cali_hub_rpc_start(&s_rpc, s_pt, s_res, CALI_HUB_RES_MAX)) answered(now_ms);
                cali_wipe(s_pt, (size_t)k);
            }
        }
    }
    if (online && (s_pub_due || now_ms >= s_next_pub)) publish_state(now_ms);
}

void cali_hub_cfg_changed(void) { s_restart = 1; }

void cali_hub_published(void) {
    uint32_t s = (uint32_t)(cali_uptime_ms() / 1000u);
    s_last_pub_s = s ? s : 1u;
}

void cali_hub_status(cali_hub_status_t *st) {
    st->online = s_started && cali_hub_port_online();
    st->last_pub_s = s_last_pub_s;
    st->key_error = s_key_err;
    st->reason = s_started && !st->online ? cali_hub_port_why() : CALI_HUB_WHY_NONE;
}

size_t cali_hub_qr(char *out, size_t max) {
    cali_hub_cfg_t cfg;
    size_t n = 0;
    if (s_key_ok && cali_hub_cfg_load(&cfg) == 0) n = cali_hub_qr_text(out, max, &cfg, s_c.key);
    cali_wipe(&cfg, sizeof cfg);
    return n;
}

int cali_hub_new_key(void) {
    if (!s_key_ok) s_key_ok = cali_hub_key_load_or_make(&s_c) == 0;   /* before the first tick ran */
    if (!s_key_ok || cali_hub_rekey(&s_c) != 0) return -1;
    s_pub_due = 1; /* the state under the new key at once */
    cali_log("hub: new data key");
    cali_lora_cmds_drop("new data key");   /* a held command arrived under the old key */
    return 0;
}
