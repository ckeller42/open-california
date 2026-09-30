/* snapshot.c — the shared "fn" emitter + pairing address (#154). Contract: include/cali_snapshot.h. */
#include "cali_snapshot.h"

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
        cali_json_obj_end(j);
    }
}

const char *cali_snapshot_pair_address(const cali_transport_t *t) {
    const cali_pair_state_t *s = cali_runner_state();
    if (!t) return NULL;
    if (s->st == PAIR_BONDED || (s->st == PAIR_IDLE && t->has_bond())) return t->identity();
    return NULL;
}
