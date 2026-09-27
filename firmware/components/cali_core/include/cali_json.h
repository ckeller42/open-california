/* cali_json.h — a bounded JSON writer; every call is a no-op after overflow, and cali_json_end()
 * reports it, so a caller never emits a torn document (#154).
 *
 * Shared by console.c (STATE/SNAP) and, from Task 2 on, the web setup page's JSON endpoints — one
 * writer, one escaping rule, instead of console.c's own ad hoc vsnprintf building. C99, no malloc,
 * writes straight into a caller-owned buffer.
 *
 * Usage: cali_json_begin() writes "{"; call cali_json_key() before each object member's value
 * (comma handled automatically — it looks at the last byte written, so no state stack is needed);
 * array elements just call the value functions back to back, no key. cali_json_end() writes the
 * matching "}" and returns the document length, or -1 if any call along the way overflowed (the
 * buffer's content is then undefined and must not be used).
 */
#ifndef CALI_JSON_H
#define CALI_JSON_H

#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    char *buf;
    size_t cap;
    size_t pos;
    int overflow;
} cali_json_t;

void cali_json_begin(cali_json_t *j, char *buf, size_t cap);  /* writes "{" */
void cali_json_key(cali_json_t *j, const char *key);          /* ,"key": (comma handled) */
void cali_json_str(cali_json_t *j, const char *s);             /* "escaped" (\" \\ \n \r \t, <0x20 as \u00XX) */
void cali_json_int(cali_json_t *j, long long v);
void cali_json_bool(cali_json_t *j, int v);
void cali_json_null(cali_json_t *j);
void cali_json_obj_begin(cali_json_t *j);
void cali_json_obj_end(cali_json_t *j);
void cali_json_arr_begin(cali_json_t *j);
void cali_json_arr_end(cali_json_t *j);
int  cali_json_end(cali_json_t *j);   /* writes "}" ; returns length, or -1 on overflow */

#ifdef __cplusplus
}
#endif

#endif /* CALI_JSON_H */
