/* json.c — see include/cali_json.h. */
#include "cali_json.h"

#include <stdio.h>
#include <string.h>

/* Appends n raw bytes and keeps buf NUL-terminated. Reserves 1 byte for the terminator, so the
 * writable range is buf[0..cap-2]. A call that would not fit writes nothing (never a partial
 * token) and sets overflow, sticky from then on. */
static void raw(cali_json_t *j, const char *s, size_t n) {
    if (j->overflow) return;
    if (j->cap == 0 || j->pos + n > j->cap - 1) {
        j->overflow = 1;
        return;
    }
    memcpy(j->buf + j->pos, s, n);
    j->pos += n;
    j->buf[j->pos] = '\0';
}

static void ch(cali_json_t *j, char c) {
    raw(j, &c, 1);
}

/* A comma is needed before the next key or value unless the last byte written opened a container
 * ('{' or '[') or was the colon after a key — that's every case where "the next thing is the
 * first thing here". No separate per-level state is needed: the last byte written says it all. */
static void comma(cali_json_t *j) {
    if (j->overflow || j->pos == 0) return;
    char last = j->buf[j->pos - 1];
    if (last != '{' && last != '[' && last != ':') raw(j, ",", 1);
}

/* Appends s's escaped content, no surrounding quotes. */
static void escaped(cali_json_t *j, const char *s) {
    for (; *s; s++) {
        unsigned char c = (unsigned char)*s;
        switch (c) {
        case '"':  raw(j, "\\\"", 2); break;
        case '\\': raw(j, "\\\\", 2); break;
        case '\n': raw(j, "\\n", 2); break;
        case '\r': raw(j, "\\r", 2); break;
        case '\t': raw(j, "\\t", 2); break;
        default:
            if (c < 0x20) {
                char esc[7];
                snprintf(esc, sizeof esc, "\\u%04x", c);
                raw(j, esc, 6);
            } else {
                ch(j, (char)c);
            }
        }
    }
}

void cali_json_begin(cali_json_t *j, char *buf, size_t cap) {
    j->buf = buf;
    j->cap = cap;
    j->pos = 0;
    j->overflow = 0;
    ch(j, '{');
}

void cali_json_key(cali_json_t *j, const char *key) {
    if (j->overflow) return;
    comma(j);
    ch(j, '"');
    escaped(j, key);
    ch(j, '"');
    ch(j, ':');
}

void cali_json_str(cali_json_t *j, const char *s) {
    if (j->overflow) return;
    comma(j);
    ch(j, '"');
    escaped(j, s);
    ch(j, '"');
}

void cali_json_int(cali_json_t *j, long long v) {
    if (j->overflow) return;
    comma(j);
    char buf[32];
    int n = snprintf(buf, sizeof buf, "%lld", v);
    if (n < 0 || (size_t)n >= sizeof buf) {
        j->overflow = 1;
        return;
    }
    raw(j, buf, (size_t)n);
}

void cali_json_bool(cali_json_t *j, int v) {
    if (j->overflow) return;
    comma(j);
    if (v) raw(j, "true", 4);
    else raw(j, "false", 5);
}

void cali_json_null(cali_json_t *j) {
    if (j->overflow) return;
    comma(j);
    raw(j, "null", 4);
}

void cali_json_obj_begin(cali_json_t *j) {
    if (j->overflow) return;
    comma(j);
    ch(j, '{');
}

void cali_json_obj_end(cali_json_t *j) {
    if (j->overflow) return;
    ch(j, '}');
}

void cali_json_arr_begin(cali_json_t *j) {
    if (j->overflow) return;
    comma(j);
    ch(j, '[');
}

void cali_json_arr_end(cali_json_t *j) {
    if (j->overflow) return;
    ch(j, ']');
}

int cali_json_end(cali_json_t *j) {
    if (j->overflow) return -1;
    ch(j, '}');
    if (j->overflow) return -1;
    return (int)j->pos;
}
