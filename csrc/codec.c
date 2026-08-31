/* codec.c — C port of calictl/protocol.py's dictionary-driven bit slicer.
 * See codec.h for the contract; codec_dict.h (GENERATED) for the field tables.
 */
#include "codec.h"

#include <string.h>

#include "codec_dict.h"

/* compile-time guard: the public KV buffer bound covers the largest table */
typedef char codec_kv_max_covers_tables[(CODEC_MAX_FIELDS <= CODEC_KV_MAX) ? 1 : -1];

/* MSB-first field extraction: bit i = raw[i/8] >> (7 - i%8) & 1 (protocol.get_field) */
static uint32_t get_bits(const uint8_t *raw, unsigned offset, unsigned width)
{
    uint32_t v = 0;
    for (unsigned i = offset; i < offset + width; i++)
        v = (v << 1) | ((uint32_t)(raw[i >> 3] >> (7 - (i & 7))) & 1u);
    return v;
}

/* MSB-first field placement (protocol._bits_of + pack; buffer pre-zeroed) */
static void put_bits(uint8_t *buf, unsigned offset, unsigned width, uint32_t value)
{
    for (unsigned k = 0; k < width; k++)
        if ((value >> (width - 1 - k)) & 1u) {
            unsigned i = offset + k;
            buf[i >> 3] |= (uint8_t)(1u << (7 - (i & 7)));
        }
}

const codec_func_t *codec_func_by_name(const char *name)
{
    for (unsigned i = 0; i < CODEC_NFUNCS; i++)          /* 15 functions: linear scan */
        if (strcmp(CODEC_FUNCS[i].name, name) == 0)
            return &CODEC_FUNCS[i];
    return 0;
}

int codec_decode(const codec_func_t *f, const uint8_t *raw, size_t len,
                 codec_kv_t out[CODEC_KV_MAX])
{
    int n = 0;
    for (unsigned i = 0; i < f->n_state; i++) {
        const struct codec_field *fl = &f->state_fields[i];
        if ((size_t)fl->offset + fl->width <= len * 8u) {  /* skip fields past the end */
            out[n].name = fl->name;
            out[n].value = get_bits(raw, fl->offset, fl->width);
            out[n].supplied = 1;
            n++;
        }
    }
    return n;
}

/* Mirrors protocol.encode's validation order per field: frame bound, value/default
 * presence, width fit, curated valid set — then places the bits. All control-table
 * fields are placed by construction (gen_c_dict refuses half-frame tables). */
int codec_encode(const codec_func_t *f, const codec_kv_t *vals, size_t nvals,
                 size_t frame_bytes, uint8_t out[CODEC_FRAME_MAX], size_t *out_len)
{
    if (f->n_ctrl == 0)
        return CODEC_ERR_NOFUNC;
    size_t total_bits = frame_bytes * 8u;
    if (frame_bytes == 0 || frame_bytes > CODEC_FRAME_MAX)
        return CODEC_ERR_FRAME;
    memset(out, 0, frame_bytes);
    for (unsigned i = 0; i < f->n_ctrl; i++) {
        const struct codec_field *fl = &f->ctrl_fields[i];
        if ((size_t)fl->offset + fl->width > total_bits)
            return CODEC_ERR_FRAME;          /* would misplace bits — fail loudly */
        uint32_t v;
        uint8_t have = 0;
        for (size_t j = 0; j < nvals; j++)
            if (strcmp(vals[j].name, fl->name) == 0) {
                v = vals[j].value;
                have = 1;
                break;
            }
        if (!have) {
            if (!(fl->flags & CODEC_F_HAS_DEFAULT))
                return CODEC_ERR_NODEFAULT;
            v = fl->def;
        }
        if (fl->width < 32 && v > ((1u << fl->width) - 1u))
            return CODEC_ERR_WIDTH;          /* value wider than the field */
        if ((fl->flags & CODEC_F_HAS_VALID) &&
            (v >= 32 || !((fl->valid_mask >> v) & 1u)))
            return CODEC_ERR_RANGE;          /* within width but not an allowed value */
        put_bits(out, fl->offset, fl->width, v);
    }
    *out_len = frame_bytes;
    return CODEC_OK;
}
