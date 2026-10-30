/**
 * @file cali_json.h
 * @brief A bounded JSON writer. Every call is a no-op after overflow, and cali_json_end() reports
 *        it, so a caller never emits a torn document.
 *
 * Shared by console.c (STATE and SNAP) and the web handlers' JSON endpoints: one writer, one
 * escaping rule. C99, no malloc, writes straight into a caller-owned buffer.
 *
 * Usage: cali_json_begin() writes `{`. Call cali_json_key() before each object member's value (the
 * comma is handled automatically: the writer looks at the last byte written, so no state stack is
 * needed). Array elements call the value functions back to back, without a key. cali_json_end()
 * writes the matching `}` and returns the document length, or -1 if any call along the way
 * overflowed (the buffer's content is then undefined and must not be used).
 */
#ifndef CALI_JSON_H
#define CALI_JSON_H

#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

/** @brief The writer state; set up by cali_json_begin(). */
typedef struct cali_json {
    char *buf;     /**< The caller-owned output buffer. */
    size_t cap;    /**< Its capacity in bytes. */
    size_t pos;    /**< Bytes written so far. */
    int overflow;  /**< Set once any write did not fit. */
} cali_json_t;

/**
 * @brief Starts a document and writes `{`.
 * @param j   The writer.
 * @param buf The output buffer.
 * @param cap Its capacity in bytes.
 */
void cali_json_begin(cali_json_t *j, char *buf, size_t cap);

/**
 * @brief Writes `,"key":` (the comma is handled).
 * @param j   The writer.
 * @param key The member name, escaped like a string.
 */
void cali_json_key(cali_json_t *j, const char *key);

/**
 * @brief Writes a string value, escaped (`\"`, `\\`, `\n`, `\r`, `\t`; other bytes below 0x20 as `\u00XX`).
 * @param j The writer.
 * @param s The string.
 */
void cali_json_str(cali_json_t *j, const char *s);

/**
 * @brief Writes an integer value.
 * @param j The writer.
 * @param v The value.
 */
void cali_json_int(cali_json_t *j, long long v);

/**
 * @brief Writes a number with `%.2f`, trailing zeros and `.` dropped (23.96, 90, -0.5).
 * @param j The writer.
 * @param v The value; NaN and infinity write null.
 */
void cali_json_num(cali_json_t *j, double v);

/**
 * @brief Formats a number like cali_json_num() into a buffer.
 *
 * The ext record formatter shares it, so a record leaves out exactly what JSON turns into null.
 *
 * @param out The output buffer (NUL-terminated on success).
 * @param cap Its capacity in bytes.
 * @param v   The value.
 * @return The text length, or -1 for NaN or infinity or a value that does not fit cap.
 */
int cali_json_fmt_num(char *out, size_t cap, double v);

/**
 * @brief Writes `true` or `false`.
 * @param j The writer.
 * @param v Non-zero = true.
 */
void cali_json_bool(cali_json_t *j, int v);

/**
 * @brief Writes `null`.
 * @param j The writer.
 */
void cali_json_null(cali_json_t *j);

/**
 * @brief Writes `{` for a nested object.
 * @param j The writer.
 */
void cali_json_obj_begin(cali_json_t *j);

/**
 * @brief Writes `}` for a nested object.
 * @param j The writer.
 */
void cali_json_obj_end(cali_json_t *j);

/**
 * @brief Writes `[`.
 * @param j The writer.
 */
void cali_json_arr_begin(cali_json_t *j);

/**
 * @brief Writes `]`.
 * @param j The writer.
 */
void cali_json_arr_end(cali_json_t *j);

/**
 * @brief Writes the closing `}` of the document.
 * @param j The writer.
 * @return The document length, or -1 on overflow.
 */
int cali_json_end(cali_json_t *j);

#ifdef __cplusplus
}
#endif

#endif /* CALI_JSON_H */
