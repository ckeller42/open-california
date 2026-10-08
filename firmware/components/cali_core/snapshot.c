/* snapshot.c — the shared "fn" emitter + pairing address (#154). Contract: include/cali_snapshot.h. */
#include "cali_snapshot.h"

#include <string.h>

#include "cali_runner.h"
#include "cali_session.h"
#include "codec.h"
#include "codec_chars.h"
#include "pairing_consts.h"

void cali_snapshot_fn(cali_json_t *j) {
    codec_kv_t kv[CODEC_KV_MAX];
    for (size_t i = 0; i < CODEC_NCHARS; i++) {
        const uint8_t *frame;
        size_t len;
        const codec_func_t *f = codec_func_by_name(CODEC_CHARS[i].function);
        if (!f || !cali_session_frame(i, &frame, &len)) continue;
        int n = codec_decode(f, frame, len, kv);
        cali_json_key(j, CODEC_CHARS[i].function);
        cali_json_obj_begin(j);
        for (int k = 0; k < n; k++) {
            cali_json_key(j, kv[k].name);
            cali_json_int(j, (long long)(unsigned long)kv[k].value);
        }
        if (strcmp(CODEC_CHARS[i].function, "lighting") == 0) {   /* + serve's config latch */
            codec_kv_t cfg[CALI_LCFG_N];
            int nc = cali_session_light_cfg(0, cfg);
            for (int k = 0; k < nc; k++) {
                cali_json_key(j, cfg[k].name);
                cali_json_int(j, (long long)(unsigned long)cfg[k].value);
            }
        }
        cali_json_obj_end(j);
    }
}

const char *cali_snapshot_pair_address(const cali_transport_t *t) {
    const cali_pair_state_t *s = cali_runner_state();
    if (!t) return NULL;
    if (s->st == PAIR_BONDED || ((s->st == PAIR_IDLE || s->st == PAIR_ERROR) && t->has_bond())) return t->identity();
    return NULL;
}

#define N_OF(a) (sizeof (a) / sizeof *(a))

void cali_snapshot_pairing(cali_json_t *j, const cali_pair_state_t *s, const char *address) {
    /* bounds = the generated tables' own lengths (pairing_consts.h), never hand-typed counts */
    const char *st = (size_t)s->st < N_OF(PAIR_STATE_NAMES) ? PAIR_STATE_NAMES[s->st] : "unknown";
    const char *err = (size_t)s->error < N_OF(PAIR_ERR_NAMES) ? PAIR_ERR_NAMES[s->error] : NULL;
    cali_json_key(j, "state");
    cali_json_str(j, st);
    cali_json_key(j, "attempts");
    cali_json_int(j, (long long)(unsigned)s->attempts);
    cali_json_key(j, "error");
    if (err) cali_json_str(j, err); else cali_json_null(j);
    cali_json_key(j, "address");
    if (address) cali_json_str(j, address); else cali_json_null(j);
}
