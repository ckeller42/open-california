/* json_cli.c — line-protocol driver for tests/firmware/test_json.py ONLY; not part of the ESP
 * build.
 *
 * argv[1] = the cali_json_t buffer capacity. Reads whitespace-separated tokens from stdin:
 *   K key        cali_json_key(key)
 *   S w1 w2 ...  cali_json_str() of the words up to (not including) the next reserved token,
 *                rejoined with single spaces; \n \r \t \\ within are unescaped first, so a
 *                script can embed control characters on one stdin line.
 *   I n          cali_json_int(n)
 *   B 0|1        cali_json_bool()
 *   N            cali_json_null()
 *   A ... ]      cali_json_arr_begin() ... cali_json_arr_end()
 *   { ... }      cali_json_obj_begin() ... cali_json_obj_end()
 *   END          stop reading; cali_json_end() and print the document, or "OVERFLOW"
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "cali_json.h"

#define MAX_IN 65536
#define MAX_TOK 8192

static char in[MAX_IN];
static size_t in_len, in_pos;
static char pending[MAX_TOK];
static int has_pending;

static int is_reserved(const char *w) {
    static const char *const words[] = {"K", "S", "I", "B", "N", "A", "]", "{", "}", "END"};
    for (size_t i = 0; i < sizeof words / sizeof *words; i++)
        if (strcmp(w, words[i]) == 0) return 1;
    return 0;
}

static int is_space(char c) {
    return c == ' ' || c == '\t' || c == '\n' || c == '\r';
}

/* The next whitespace-delimited token, or 0 at end of input. */
static int next_tok(char *out, size_t cap) {
    while (in_pos < in_len && is_space(in[in_pos])) in_pos++;
    if (in_pos >= in_len) return 0;
    size_t n = 0;
    while (in_pos < in_len && !is_space(in[in_pos])) {
        if (n + 1 < cap) out[n++] = in[in_pos];
        in_pos++;
    }
    out[n] = '\0';
    return 1;
}

static int get_tok(char *out, size_t cap) {
    if (has_pending) {
        strncpy(out, pending, cap - 1);
        out[cap - 1] = '\0';
        has_pending = 0;
        return 1;
    }
    return next_tok(out, cap);
}

static void unget_tok(const char *w) {
    strncpy(pending, w, sizeof pending - 1);
    pending[sizeof pending - 1] = '\0';
    has_pending = 1;
}

/* Unescapes \n \r \t \\ in place (the only way to get a control character or a literal backslash
 * onto the single stdin line a script is written as). */
static void unescape(char *s) {
    char *r = s, *w = s;
    while (*r) {
        if (r[0] == '\\' && r[1] == 'n') { *w++ = '\n'; r += 2; }
        else if (r[0] == '\\' && r[1] == 'r') { *w++ = '\r'; r += 2; }
        else if (r[0] == '\\' && r[1] == 't') { *w++ = '\t'; r += 2; }
        else if (r[0] == '\\' && r[1] == '\\') { *w++ = '\\'; r += 2; }
        else *w++ = *r++;
    }
    *w = '\0';
}

int main(int argc, char **argv) {
    if (argc < 2) {
        fprintf(stderr, "usage: json_cli <cap>\n");
        return 2;
    }
    size_t cap = (size_t)strtoul(argv[1], NULL, 10);
    char *buf = malloc(cap ? cap : 1);
    if (!buf) return 2;

    in_len = fread(in, 1, sizeof in - 1, stdin);
    in[in_len] = '\0';

    cali_json_t j;
    cali_json_begin(&j, buf, cap);

    char tok[MAX_TOK];
    while (get_tok(tok, sizeof tok)) {
        if (strcmp(tok, "K") == 0) {
            char key[MAX_TOK];
            if (get_tok(key, sizeof key)) cali_json_key(&j, key);
        } else if (strcmp(tok, "S") == 0) {
            char strval[MAX_TOK];
            strval[0] = '\0';
            char w[MAX_TOK];
            while (get_tok(w, sizeof w)) {
                if (is_reserved(w)) { unget_tok(w); break; }
                if (strval[0] != '\0')
                    strncat(strval, " ", sizeof strval - strlen(strval) - 1);
                strncat(strval, w, sizeof strval - strlen(strval) - 1);
            }
            unescape(strval);
            cali_json_str(&j, strval);
        } else if (strcmp(tok, "I") == 0) {
            char v[MAX_TOK];
            if (get_tok(v, sizeof v)) cali_json_int(&j, strtoll(v, NULL, 10));
        } else if (strcmp(tok, "B") == 0) {
            char v[MAX_TOK];
            if (get_tok(v, sizeof v)) cali_json_bool(&j, atoi(v));
        } else if (strcmp(tok, "N") == 0) {
            cali_json_null(&j);
        } else if (strcmp(tok, "A") == 0) {
            cali_json_arr_begin(&j);
        } else if (strcmp(tok, "]") == 0) {
            cali_json_arr_end(&j);
        } else if (strcmp(tok, "{") == 0) {
            cali_json_obj_begin(&j);
        } else if (strcmp(tok, "}") == 0) {
            cali_json_obj_end(&j);
        } else if (strcmp(tok, "END") == 0) {
            break;
        }
    }

    int n = cali_json_end(&j);
    if (n < 0) printf("OVERFLOW\n");
    else printf("%s\n", buf);
    free(buf);
    return 0;
}
