/* codec.h — Camper Unit BLE frame codec, C port of calictl/protocol.py's slicer.
 *
 * C99, no malloc, no platform deps: compiles on a host for the parity tests
 * (tests/test_codec_parity.py) and under ESP-IDF for the #154 satellite. The
 * field tables come from the GENERATED csrc/codec_dict.h (single source:
 * protocol/dictionary.yaml + calictl/overrides.py) — codec.c is its only
 * includer; consumers see codec_func_t as opaque.
 *
 * Bit convention (MSB-first, identical to calictl.protocol):
 *     bit i = byte[i/8] >> (7 - i%8) & 1
 */
#ifndef CODEC_H
#define CODEC_H
#include <stddef.h>
#include <stdint.h>

typedef struct codec_func codec_func_t;  /* full definition: codec_dict.h (codec.c only) */

typedef struct {
    const char *name;
    uint32_t value;
    uint8_t supplied;    /* encode input: 1 = caller-supplied, else dictionary default */
} codec_kv_t;

#define CODEC_KV_MAX    64   /* >= CODEC_MAX_FIELDS (checked at compile time in codec.c) */
#define CODEC_FRAME_MAX 32   /* bytes; largest real frame is lighting (16) */

enum {
    CODEC_OK            = 0,
    CODEC_ERR_NOFUNC    = -1,  /* unknown function name */
    CODEC_ERR_WIDTH     = -2,  /* value does not fit the field width      (Python: ValueError "out of range") */
    CODEC_ERR_RANGE     = -3,  /* value outside the curated valid set     (Python: ValueError "not an allowed value") */
    CODEC_ERR_NODEFAULT = -4,  /* field not supplied and no default       (Python: ValueError "no value/default") */
    CODEC_ERR_FRAME     = -5   /* field exceeds the pinned frame length   (Python: ValueError "exceeds the ... frame") */
};

const codec_func_t *codec_func_by_name(const char *name);

/* Decode a state frame: fills out[] with every placed state field that fully fits
 * len bytes (fields past the end are skipped — mirrors protocol.decode). Returns
 * the field count. */
int codec_decode(const codec_func_t *f, const uint8_t *raw, size_t len,
                 codec_kv_t out[CODEC_KV_MAX]);

/* Encode a control frame: every control field is written from vals[] (matched by
 * name) or its dictionary default, validated exactly like protocol.encode
 * (width, valid set, default presence, frame bound). Returns CODEC_OK and sets
 * *out_len, or a CODEC_ERR_* code. */
int codec_encode(const codec_func_t *f, const codec_kv_t *vals, size_t nvals,
                 size_t frame_bytes, uint8_t out[CODEC_FRAME_MAX], size_t *out_len);

#endif /* CODEC_H */
