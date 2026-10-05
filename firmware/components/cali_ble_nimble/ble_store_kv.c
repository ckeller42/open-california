/* ble_store_kv.c — NimBLE bond store on the platform kv-store (#154).
 *
 * The same RAM tables + matching rules as NimBLE's ble_store_config.c (nimble/host/store/config),
 * but persisted through cali_kv_* instead of Mynewt conf/NVS, so the host build and the ESP32 build
 * share one store path. Every record is a raw NimBLE value struct in its own slot key:
 *   sec_our_<n>, sec_peer_<n>   struct ble_store_value_sec,  n < BLE_STORE_MAX_BONDS
 *   cccd_<n>                    struct ble_store_value_cccd, n < BLE_STORE_MAX_CCCDS
 * Slots 0..count-1 hold the table in order; a zero-length value marks a free slot. A record that
 * reads back corrupt (-2: torn/CRC-mismatched, or the wrong size for the struct) is "no record",
 * logged once at load, and overwritten on the next persist — a damaged store boots unpaired. A
 * second record with the same key (a delete-compaction cut short by power loss) is dropped at load
 * the same way (table_load).
 */
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "syscfg/syscfg.h"
#include "host/ble_hs.h"
#include "host/ble_store.h"

#include "cali_ble_nimble.h"
#include "cali_platform.h"

#define MAX_BONDS MYNEWT_VAL(BLE_STORE_MAX_BONDS)
#define MAX_CCCDS MYNEWT_VAL(BLE_STORE_MAX_CCCDS)

#if MAX_BONDS < 1 || MAX_BONDS > 32 || MAX_CCCDS < 1 || MAX_CCCDS > 32
#error "ble_store_kv needs 1..32 bond and CCCD slots (the on-store slot mask is a uint32_t)"
#endif

static struct ble_store_value_sec s_our_secs[MAX_BONDS];
static struct ble_store_value_sec s_peer_secs[MAX_BONDS];
static struct ble_store_value_cccd s_cccds[MAX_CCCDS];

/* One persisted table: `num` records at `vals`, mirrored to slots "<prefix><n>". */
struct kv_table {
    const char *prefix;
    void *vals;
    size_t size;
    int max;
    int num;
    uint32_t used_slots; /* slots holding a non-empty (possibly corrupt) record in the kv-store */
    int (*same_key)(const void *a, const void *b); /* two records NimBLE would treat as one */
};

static int sec_same_key(const void *a, const void *b) {
    return !ble_addr_cmp(&((const struct ble_store_value_sec *)a)->peer_addr,
                         &((const struct ble_store_value_sec *)b)->peer_addr);
}

static int cccd_same_key(const void *a, const void *b) {
    const struct ble_store_value_cccd *x = a, *y = b;
    return !ble_addr_cmp(&x->peer_addr, &y->peer_addr) && x->chr_val_handle == y->chr_val_handle;
}

static struct kv_table s_our = {"sec_our_", s_our_secs, sizeof s_our_secs[0], MAX_BONDS, 0, 0,
                                sec_same_key};
static struct kv_table s_peer = {"sec_peer_", s_peer_secs, sizeof s_peer_secs[0], MAX_BONDS, 0, 0,
                                 sec_same_key};
static struct kv_table s_cccd = {"cccd_", s_cccds, sizeof s_cccds[0], MAX_CCCDS, 0, 0,
                                 cccd_same_key};

static void slot_key(char *out, size_t cap, const struct kv_table *t, int n) {
    snprintf(out, cap, "%s%d", t->prefix, n);
}

static uint8_t *table_at(const struct kv_table *t, int idx) {
    return (uint8_t *)t->vals + (size_t)idx * t->size;
}

/* Index of a loaded record with the same key as `rec`, or -1. */
static int loaded_dup(const struct kv_table *t, const void *rec) {
    for (int i = 0; i < t->num; i++) {
        if (t->same_key(table_at(t, i), rec)) return i;
    }
    return -1;
}

/* Load slots 0..max-1 into RAM, compacting over free/corrupt/duplicate slots.
 *
 * Duplicates: a delete compacts the table by rewriting slots one at a time (each write is atomic,
 * the sequence is not), so a power loss mid-delete can leave the moved record in two slots — e.g.
 * deleting A from [A, B] writes slot0 = B, then clears slot1; a crash between leaves [B, B]. Loading
 * both would waste a slot and, worse, a later delete of B would drop only the first copy and B
 * would come back on the next boot. So the first copy wins and later ones are dropped (their slot
 * stays marked used, so the next persist clears it). Moved records are identical copies, so which
 * one wins does not matter. */
static void table_load(struct kv_table *t) {
    char key[CALI_KV_KEY_MAX + 1];
    t->num = 0;
    t->used_slots = 0;
    for (int n = 0; n < t->max; n++) {
        size_t len = t->size;
        int rc;
        slot_key(key, sizeof key, t, n);
        rc = cali_kv_get(key, table_at(t, t->num), &len);
        if (rc == CALI_KV_MISSING || (rc == CALI_KV_OK && len == 0)) continue;
        t->used_slots |= 1u << n;
        if (rc != CALI_KV_OK || len != t->size) {
            cali_log("store: corrupt record %s ignored", key);
        } else if (loaded_dup(t, table_at(t, t->num)) >= 0) {
            cali_log("store: duplicate record %s ignored", key);
        } else {
            t->num++;
        }
    }
}

/* Write the RAM table back: records to slots 0..num-1, clear every other slot that holds data. */
static int table_persist(struct kv_table *t) {
    char key[CALI_KV_KEY_MAX + 1];
    int rc = 0;
    for (int n = 0; n < t->max; n++) {
        slot_key(key, sizeof key, t, n);
        if (n < t->num) {
            if (cali_kv_set(key, table_at(t, n), t->size) != 0) rc = BLE_HS_ESTORE_FAIL;
            else t->used_slots |= 1u << n;
        } else if (t->used_slots & (1u << n)) {
            if (cali_kv_set(key, NULL, 0) != 0) rc = BLE_HS_ESTORE_FAIL;
            else t->used_slots &= ~(1u << n);
        }
    }
    return rc;
}

static void table_delete_at(struct kv_table *t, int idx) {
    t->num--;
    if (idx < t->num) {
        memmove(table_at(t, idx), table_at(t, idx + 1), (size_t)(t->num - idx) * t->size);
    }
}

/* Store `val` at `idx`, or append it when idx == -1 (BLE_HS_ESTORE_CAP when full); then persist. */
static int table_put(struct kv_table *t, int idx, const void *val) {
    if (idx == -1) {
        if (t->num >= t->max) return BLE_HS_ESTORE_CAP;
        idx = t->num++;
    }
    memcpy(table_at(t, idx), val, t->size);
    return table_persist(t);
}

/* ---- sec: ble_store_config_find_sec's rules ---- */

/* peer_addr ANY: the idx'th record; else only idx 0 is defined, matched by peer_addr. */
static int find_sec(const struct ble_store_key_sec *key, const struct kv_table *t) {
    const struct ble_store_value_sec *secs = t->vals;
    if (!ble_addr_cmp(&key->peer_addr, BLE_ADDR_ANY)) {
        if (key->idx < t->num) return key->idx;
    } else if (key->idx == 0) {
        for (int i = 0; i < t->num; i++) {
            if (!ble_addr_cmp(&secs[i].peer_addr, &key->peer_addr)) return i;
        }
    }
    return -1;
}

static int read_sec(struct kv_table *t, const struct ble_store_key_sec *key,
                    struct ble_store_value_sec *out) {
    int idx = find_sec(key, t);
    if (idx == -1) return BLE_HS_ENOENT;
    *out = ((struct ble_store_value_sec *)t->vals)[idx];
    return 0;
}

static int write_sec(struct kv_table *t, const struct ble_store_value_sec *val) {
    struct ble_store_key_sec key;
    ble_store_key_from_value_sec(&key, val);
    return table_put(t, find_sec(&key, t), val);
}

static int delete_sec(struct kv_table *t, const struct ble_store_key_sec *key) {
    int idx = find_sec(key, t);
    if (idx == -1) return BLE_HS_ENOENT;
    table_delete_at(t, idx);
    return table_persist(t);
}

/* ---- cccd: ble_store_config_find_cccd's rules ---- */

/* peer_addr ANY matches every peer, chr_val_handle 0 every handle; the idx'th match wins. */
static int find_cccd(const struct ble_store_key_cccd *key) {
    int skipped = 0;
    for (int i = 0; i < s_cccd.num; i++) {
        const struct ble_store_value_cccd *c = &s_cccds[i];
        if (ble_addr_cmp(&key->peer_addr, BLE_ADDR_ANY) &&
            ble_addr_cmp(&c->peer_addr, &key->peer_addr)) {
            continue;
        }
        if (key->chr_val_handle != 0 && c->chr_val_handle != key->chr_val_handle) continue;
        if (key->idx > skipped) {
            skipped++;
            continue;
        }
        return i;
    }
    return -1;
}

/* ---- NimBLE store callbacks ---- */

static int store_read(int obj_type, const union ble_store_key *key, union ble_store_value *value) {
    int idx;
    switch (obj_type) {
    case BLE_STORE_OBJ_TYPE_PEER_SEC:
        return read_sec(&s_peer, &key->sec, &value->sec);
    case BLE_STORE_OBJ_TYPE_OUR_SEC:
        return read_sec(&s_our, &key->sec, &value->sec);
    case BLE_STORE_OBJ_TYPE_CCCD:
        idx = find_cccd(&key->cccd);
        if (idx == -1) return BLE_HS_ENOENT;
        value->cccd = s_cccds[idx];
        return 0;
    default:
        return BLE_HS_ENOTSUP;
    }
}

static int store_write(int obj_type, const union ble_store_value *val) {
    struct ble_store_key_cccd key_cccd;
    switch (obj_type) {
    case BLE_STORE_OBJ_TYPE_PEER_SEC:
        return write_sec(&s_peer, &val->sec);
    case BLE_STORE_OBJ_TYPE_OUR_SEC:
        return write_sec(&s_our, &val->sec);
    case BLE_STORE_OBJ_TYPE_CCCD:
        ble_store_key_from_value_cccd(&key_cccd, &val->cccd);
        return table_put(&s_cccd, find_cccd(&key_cccd), &val->cccd);
    default:
        return BLE_HS_ENOTSUP;
    }
}

static int store_delete(int obj_type, const union ble_store_key *key) {
    int idx;
    switch (obj_type) {
    case BLE_STORE_OBJ_TYPE_PEER_SEC:
        return delete_sec(&s_peer, &key->sec);
    case BLE_STORE_OBJ_TYPE_OUR_SEC:
        return delete_sec(&s_our, &key->sec);
    case BLE_STORE_OBJ_TYPE_CCCD:
        idx = find_cccd(&key->cccd);
        if (idx == -1) return BLE_HS_ENOENT;
        table_delete_at(&s_cccd, idx);
        return table_persist(&s_cccd);
    default:
        /* A type this store never keeps: nothing to delete. esp-nimble's ble_store_util_delete_peer
         * deletes PEER_ADDR/CSFC/... too and fails on anything but ENOENT (#225: forget timed out). */
        return BLE_HS_ENOENT;
    }
}

void cali_ble_store_init(void) {
    table_load(&s_our);
    table_load(&s_peer);
    table_load(&s_cccd);
    ble_hs_cfg.store_read_cb = store_read;
    ble_hs_cfg.store_write_cb = store_write;
    ble_hs_cfg.store_delete_cb = store_delete;
}

int cali_ble_store_ensure(void) {
    if (ble_hs_cfg.store_read_cb == store_read && ble_hs_cfg.store_write_cb == store_write &&
        ble_hs_cfg.store_delete_cb == store_delete)
        return 0;
    cali_log("store: ERROR bond store callbacks were replaced by the BLE stack, re-installed");
    ble_hs_cfg.store_read_cb = store_read;
    ble_hs_cfg.store_write_cb = store_write;
    ble_hs_cfg.store_delete_cb = store_delete;
    return 1;
}
