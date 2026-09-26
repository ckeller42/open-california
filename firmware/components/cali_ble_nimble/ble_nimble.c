/* ble_nimble.c — cali_transport_t on NimBLE (#154): the Linux host build and ESP-IDF.
 *
 * The GAP/SM sequence is the proven spike's (firmware/host/spike_main.c): active scan matching the
 * advertised name, ble_gap_connect, ble_gap_security_initiate, BLE_GAP_EVENT_PASSKEY_ACTION
 * (INPUT) -> ble_sm_inject_io, BLE_GAP_EVENT_ENC_CHANGE, ble_gattc_read. Contract:
 * cali_core/include/cali_transport.h; init order: cali_ble_nimble.h.
 *
 * GATT: every read/write/subscribe/discovery is queued and run one at a time (ATT allows one
 * outstanding request per bearer). discover() walks ble_gattc_disc_all_chrs over the full handle
 * range and builds char_short -> (handles, properties); the short id is bytes 12-13 of the
 * little-endian 128-bit UUID, accepted only when the other 14 bytes equal CODEC_UUID_FMT's base.
 * connect_bonded() re-encrypts by itself once connected (ble_gap_security_initiate with the stored
 * LTK: ENC_OK / ENC_FAIL, never a passkey). Every heartbeat completion is reported (HEARTBEAT).
 * A read/heartbeat of a char not yet in the table (e.g. the 1004 verify read right after pairing)
 * first looks it up with ble_gattc_disc_chrs_by_uuid.
 */
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "host/ble_hs.h"
#include "host/ble_store.h"
#include "host/ble_uuid.h"

#include "cali_ble_nimble.h"
#include "cali_platform.h"
#include "cali_transport.h"
#include "codec_chars.h"

#define MAX_CHRS 40
#define CONNECT_TIMEOUT_MS 10000

struct chr {
    uint16_t short_id, def_handle, val_handle, end_handle; /* end_handle 0 = unknown */
    uint8_t props;
};

enum { OP_DISC, OP_READ, OP_WRITE_HB, OP_SUB };
struct op {
    uint8_t kind;
    uint16_t short_id;
    uint32_t counter;
};
#define OPQ 48

static cali_tsink_t s_sink;
static void *s_sink_ctx;
static void (*s_on_sync)(void);
static uint8_t s_own_addr_type = BLE_OWN_ADDR_PUBLIC;
static uint8_t s_base[16];                  /* CODEC_UUID_FMT base, little-endian, short = 0 */

static char s_name[32];
static int s_scanning, s_found;
static ble_addr_t s_found_addr;
static int s_connecting, s_connect_cancelled;
static int s_encrypt_on_connect;            /* connect_bonded(): re-encrypt once the link is up */
static uint16_t s_conn = BLE_HS_CONN_HANDLE_NONE;
static uintptr_t s_gen;                     /* bumped per link: stale GATT callbacks are dropped */

static struct chr s_chrs[MAX_CHRS];
static int s_nchrs, s_disc_done;
static struct op s_ops[OPQ];
static unsigned s_ophead, s_oplen;
static int s_op_busy;
static uint16_t s_cccd;                     /* OP_SUB: the CCCD handle found so far */
static int s_dsc_past;                      /* OP_SUB: walked past the char (a declaration seen) */

static uint8_t s_buf[512];
static char s_identity[18];

static int gap_cb(struct ble_gap_event *ev, void *arg);
static void op_kick(void);

/* ---- helpers ------------------------------------------------------------------------------- */

static void emit(cali_tev_t ev, int status, uint16_t short_id, const uint8_t *data, size_t len,
                 const ble_addr_t *addr) {
    cali_tevent_t e;
    memset(&e, 0, sizeof e);
    e.ev = ev;
    e.status = status;
    e.char_short = short_id;
    e.data = data;
    e.len = len;
    if (addr) {
        snprintf(e.addr, sizeof e.addr, "%02X:%02X:%02X:%02X:%02X:%02X", addr->val[5], addr->val[4],
                 addr->val[3], addr->val[2], addr->val[1], addr->val[0]);
    }
    if (s_sink) s_sink(&e, s_sink_ctx);
}

/* "0000xxxx-6c77-..." -> 16 bytes little-endian (the string is big-endian, NimBLE stores LE). */
static int uuid_le_from_str(const char *s, uint8_t le[16]) {
    uint8_t be[16] = {0};
    int n = 0;
    for (const char *p = s; *p && n < 32; p++) {
        int v;
        if (*p == '-') continue;
        if (*p >= '0' && *p <= '9') v = *p - '0';
        else if (*p >= 'a' && *p <= 'f') v = *p - 'a' + 10;
        else if (*p >= 'A' && *p <= 'F') v = *p - 'A' + 10;
        else return -1;
        if (n % 2 == 0) be[n / 2] = (uint8_t)(v << 4);
        else be[n / 2] |= (uint8_t)v;
        n++;
    }
    if (n != 32) return -1;
    for (int i = 0; i < 16; i++) le[i] = be[15 - i];
    return 0;
}

static void uuid_for_short(uint16_t short_id, ble_uuid128_t *out) {
    out->u.type = BLE_UUID_TYPE_128;
    memcpy(out->value, s_base, 16);
    out->value[12] = (uint8_t)(short_id & 0xff);
    out->value[13] = (uint8_t)(short_id >> 8);
}

/* The short id of one of the unit's 128-bit UUIDs, or -1 for any other UUID. */
static int short_of(const ble_uuid_any_t *u) {
    if (u->u.type != BLE_UUID_TYPE_128) return -1;
    for (int i = 0; i < 16; i++) {
        if (i != 12 && i != 13 && u->u128.value[i] != s_base[i]) return -1;
    }
    return (u->u128.value[13] << 8) | u->u128.value[12];
}

static struct chr *find_short(uint16_t short_id) {
    for (int i = 0; i < s_nchrs; i++) {
        if (s_chrs[i].short_id == short_id) return &s_chrs[i];
    }
    return NULL;
}

static struct chr *find_val(uint16_t val_handle) {
    for (int i = 0; i < s_nchrs; i++) {
        if (s_chrs[i].val_handle == val_handle) return &s_chrs[i];
    }
    return NULL;
}

static void add_chr(const struct ble_gatt_chr *c, uint16_t short_id) {
    struct chr *x = find_short(short_id);
    if (!x) {
        if (s_nchrs >= MAX_CHRS) {
            cali_log("ble: char table full, %04x dropped", short_id);
            return;
        }
        x = &s_chrs[s_nchrs++];
    }
    x->short_id = short_id;
    x->def_handle = c->def_handle;
    x->val_handle = c->val_handle;
    x->end_handle = 0;
    x->props = c->properties;
}

static uint16_t om_copy(struct os_mbuf *om) {
    uint16_t len = OS_MBUF_PKTLEN(om);
    if (len > sizeof s_buf) len = sizeof s_buf;
    os_mbuf_copydata(om, 0, len, s_buf);
    return len;
}

static void link_reset(void) {
    s_conn = BLE_HS_CONN_HANDLE_NONE;
    s_gen++;
    s_ophead = s_oplen = 0;
    s_op_busy = 0;
    s_nchrs = 0;
    s_disc_done = 0;
}

/* ---- GATT operation queue ------------------------------------------------------------------ */

static int op_push(uint8_t kind, uint16_t short_id, uint32_t counter) {
    if (s_conn == BLE_HS_CONN_HANDLE_NONE) return BLE_HS_ENOTCONN;
    if (s_oplen >= OPQ) return BLE_HS_ENOMEM;
    struct op *o = &s_ops[(s_ophead + s_oplen) % OPQ];
    o->kind = kind;
    o->short_id = short_id;
    o->counter = counter;
    s_oplen++;
    op_kick();
    return 0;
}

/* An operation ended with `status` (0 = ok). Pops it first (the sink may call back into the
 * transport, e.g. disconnect() clears the queue), reports what the contract promises, then runs
 * the next one. */
static void op_finish(int status, const uint8_t *data, size_t len) {
    struct op o = s_ops[s_ophead];
    s_ophead = (s_ophead + 1) % OPQ;
    s_oplen--;
    s_op_busy = 0;
    switch (o.kind) {
    case OP_DISC: emit(CALI_TEV_DISCOVERED, status, 0, NULL, 0, NULL); break;
    case OP_READ: emit(CALI_TEV_READ, status, o.short_id, status ? NULL : data, status ? 0 : len, NULL); break;
    case OP_WRITE_HB:
        if (status) cali_log("ble: heartbeat write failed %d", status);
        emit(CALI_TEV_HEARTBEAT, status, CODEC_CHAR_HEARTBEAT, NULL, 0, NULL);
        break;
    case OP_SUB:
        if (status) cali_log("ble: subscribe %04x failed %d", o.short_id, status);
        break;
    default: break;
    }
    op_kick();
}

static int on_read(uint16_t conn, const struct ble_gatt_error *err, struct ble_gatt_attr *attr, void *arg) {
    (void)conn;
    if ((uintptr_t)arg != s_gen) return 0;
    if (err->status == 0 && attr) {
        uint16_t len = om_copy(attr->om);
        op_finish(0, s_buf, len);
    } else {
        op_finish(err->status ? err->status : BLE_HS_EUNKNOWN, NULL, 0);
    }
    return 0;
}

static int on_written(uint16_t conn, const struct ble_gatt_error *err, struct ble_gatt_attr *attr, void *arg) {
    (void)conn; (void)attr;
    if ((uintptr_t)arg != s_gen) return 0;
    op_finish(err->status, NULL, 0);
    return 0;
}

/* Issue the op's ATT request on a known char. Returns 0 if started. */
static int op_issue(struct op *o, struct chr *c) {
    void *gen = (void *)s_gen;
    if (o->kind == OP_READ) return ble_gattc_read(s_conn, c->val_handle, on_read, gen);
    /* OP_WRITE_HB: 4-byte big-endian counter, with response (as calictl.device's heartbeat) */
    uint8_t v[4] = {(uint8_t)(o->counter >> 24), (uint8_t)(o->counter >> 16),
                    (uint8_t)(o->counter >> 8), (uint8_t)o->counter};
    return ble_gattc_write_flat(s_conn, c->val_handle, v, sizeof v, on_written, gen);
}

static int on_lookup(uint16_t conn, const struct ble_gatt_error *err, const struct ble_gatt_chr *chr, void *arg) {
    (void)conn;
    if ((uintptr_t)arg != s_gen) return 0;
    struct op *o = &s_ops[s_ophead];
    if (err->status == 0) {
        add_chr(chr, o->short_id);
        return 0;
    }
    if (err->status != BLE_HS_EDONE) {
        op_finish(err->status, NULL, 0);
        return 0;
    }
    struct chr *c = find_short(o->short_id);
    int rc = c ? op_issue(o, c) : BLE_HS_ENOENT;
    if (rc != 0) op_finish(rc, NULL, 0);
    return 0;
}

static int on_disc_chr(uint16_t conn, const struct ble_gatt_error *err, const struct ble_gatt_chr *chr, void *arg) {
    (void)conn;
    if ((uintptr_t)arg != s_gen) return 0;
    if (err->status == 0) {
        int sh = short_of(&chr->uuid);
        if (sh >= 0) add_chr(chr, (uint16_t)sh);
        return 0;
    }
    if (err->status != BLE_HS_EDONE) {
        op_finish(err->status, NULL, 0);
        return 0;
    }
    /* A char's descriptors end before the next declaration of ours (else 0xffff); a foreign
     * declaration in between is caught by on_dsc, which stops at the first declaration UUID. */
    for (int i = 0; i < s_nchrs; i++) {
        uint16_t end = 0xffff;
        for (int j = 0; j < s_nchrs; j++) {
            if (s_chrs[j].def_handle > s_chrs[i].val_handle && s_chrs[j].def_handle - 1 < end)
                end = (uint16_t)(s_chrs[j].def_handle - 1);
        }
        s_chrs[i].end_handle = end;
    }
    s_disc_done = 1;
    op_finish(0, NULL, 0);
    return 0;
}

static int on_dsc(uint16_t conn, const struct ble_gatt_error *err, uint16_t chr_val_handle,
                  const struct ble_gatt_dsc *dsc, void *arg) {
    (void)conn; (void)chr_val_handle;
    if ((uintptr_t)arg != s_gen) return 0;
    if (err->status == 0) {
        uint16_t u16 = dsc->uuid.u.type == BLE_UUID_TYPE_16 ? dsc->uuid.u16.value : 0;
        if (u16 == BLE_ATT_UUID_PRIMARY_SERVICE || u16 == BLE_ATT_UUID_SECONDARY_SERVICE ||
            u16 == BLE_ATT_UUID_CHARACTERISTIC)
            s_dsc_past = 1;
        else if (!s_dsc_past && !s_cccd && u16 == BLE_GATT_DSC_CLT_CFG_UUID16)
            s_cccd = dsc->handle;
        return 0;
    }
    if (err->status != BLE_HS_EDONE) {
        op_finish(err->status, NULL, 0);
        return 0;
    }
    if (!s_cccd) {
        op_finish(BLE_HS_ENOENT, NULL, 0);
        return 0;
    }
    static const uint8_t enable[2] = {0x01, 0x00};           /* notifications on */
    int rc = ble_gattc_write_flat(s_conn, s_cccd, enable, sizeof enable, on_written, (void *)s_gen);
    if (rc != 0) op_finish(rc, NULL, 0);
    return 0;
}

static int op_begin(struct op *o) {
    void *gen = (void *)s_gen;
    struct chr *c;
    switch (o->kind) {
    case OP_DISC:
        s_nchrs = 0;
        s_disc_done = 0;
        return ble_gattc_disc_all_chrs(s_conn, 1, 0xffff, on_disc_chr, gen);
    case OP_READ:
    case OP_WRITE_HB:
        c = find_short(o->short_id);
        if (c) return op_issue(o, c);
        if (s_disc_done) return BLE_HS_ENOENT;
        {
            ble_uuid128_t u;
            uuid_for_short(o->short_id, &u);
            return ble_gattc_disc_chrs_by_uuid(s_conn, 1, 0xffff, &u.u, on_lookup, gen);
        }
    case OP_SUB:
        c = find_short(o->short_id);
        if (!c || !c->end_handle) return BLE_HS_ENOENT;
        if (c->end_handle <= c->val_handle) return BLE_HS_ENOENT;   /* no descriptors */
        s_cccd = 0;
        s_dsc_past = 0;
        return ble_gattc_disc_all_dscs(s_conn, c->val_handle, c->end_handle, on_dsc, gen);
    default:
        return BLE_HS_EINVAL;
    }
}

static void op_kick(void) {
    if (s_op_busy || !s_oplen) return;
    int rc = op_begin(&s_ops[s_ophead]);
    if (rc == 0) s_op_busy = 1;
    else op_finish(rc, NULL, 0);         /* reports the failure, pops, and kicks the next */
}

/* ---- GAP ----------------------------------------------------------------------------------- */

static int name_matches(const struct ble_gap_disc_desc *d) {
    struct ble_hs_adv_fields f;
    size_t n = strlen(s_name);
    if (ble_hs_adv_parse_fields(&f, d->data, d->length_data) != 0) return 0;
    return f.name != NULL && f.name_len == n && memcmp(f.name, s_name, n) == 0;
}

static int gap_cb(struct ble_gap_event *ev, void *arg) {
    (void)arg;
    switch (ev->type) {
    case BLE_GAP_EVENT_DISC:
        if (s_scanning && !s_found && name_matches(&ev->disc)) {
            s_found = 1;
            s_found_addr = ev->disc.addr;
            emit(CALI_TEV_FOUND, 0, 0, NULL, 0, &s_found_addr);
        }
        return 0;
    case BLE_GAP_EVENT_DISC_COMPLETE:
        s_scanning = 0;
        return 0;
    case BLE_GAP_EVENT_CONNECT:
        s_connecting = 0;
        if (ev->connect.status == 0) {
            link_reset();
            s_conn = ev->connect.conn_handle;
            emit(CALI_TEV_CONNECTED, 0, 0, NULL, 0, NULL);
            if (s_encrypt_on_connect && s_conn == ev->connect.conn_handle) {
                /* With the peer's LTK stored this starts the encryption procedure, no pairing. */
                s_encrypt_on_connect = 0;
                int rc = ble_gap_security_initiate(s_conn);
                if (rc != 0) emit(CALI_TEV_ENC_FAIL, rc, 0, NULL, 0, NULL);
            }
        } else if (s_connect_cancelled) {
            s_connect_cancelled = 0;                        /* our own disconnect(): silent */
        } else {
            emit(CALI_TEV_CONNECT_FAIL, ev->connect.status, 0, NULL, 0, NULL);
        }
        return 0;
    case BLE_GAP_EVENT_DISCONNECT:
        if (ev->disconnect.conn.conn_handle != s_conn) return 0;  /* a link we already dropped */
        link_reset();
        emit(CALI_TEV_DISCONNECTED, ev->disconnect.reason, 0, NULL, 0, NULL);
        return 0;
    case BLE_GAP_EVENT_PASSKEY_ACTION:
        if (ev->passkey.conn_handle != s_conn) return 0;
        if (ev->passkey.params.action == BLE_SM_IOACT_INPUT) {
            emit(CALI_TEV_PASSKEY_REQ, 0, 0, NULL, 0, NULL);
        } else {
            /* KEYBOARD_ONLY never expects another method: end the link, the runner retries */
            cali_log("ble: unexpected passkey action %d", ev->passkey.params.action);
            ble_gap_terminate(s_conn, BLE_ERR_AUTH_FAIL);
        }
        return 0;
    case BLE_GAP_EVENT_ENC_CHANGE:
        if (ev->enc_change.conn_handle != s_conn) return 0;
        if (ev->enc_change.status == 0) emit(CALI_TEV_ENC_OK, 0, 0, NULL, 0, NULL);
        else emit(CALI_TEV_ENC_FAIL, ev->enc_change.status, 0, NULL, 0, NULL);
        return 0;
    case BLE_GAP_EVENT_NOTIFY_RX: {
        if (ev->notify_rx.conn_handle != s_conn) return 0;
        struct chr *c = find_val(ev->notify_rx.attr_handle);
        if (!c) return 0;
        uint16_t len = om_copy(ev->notify_rx.om);
        emit(CALI_TEV_NOTIFY, 0, c->short_id, s_buf, len, NULL);
        return 0;
    }
    case BLE_GAP_EVENT_REPEAT_PAIRING: {
        /* A stale bond on our side (the unit forgot us): drop it and pair afresh. */
        struct ble_gap_conn_desc desc;
        if (ble_gap_conn_find(ev->repeat_pairing.conn_handle, &desc) == 0)
            ble_store_util_delete_peer(&desc.peer_id_addr);
        return BLE_GAP_REPEAT_PAIRING_RETRY;
    }
    default:
        return 0;
    }
}

/* ---- the transport ------------------------------------------------------------------------- */

static void t_set_sink(cali_tsink_t sink, void *ctx) {
    s_sink = sink;
    s_sink_ctx = ctx;
}

static int t_start_scan(const char *name) {
    struct ble_gap_disc_params p;
    memset(&p, 0, sizeof p);
    p.passive = 0;                  /* active: the name may be in the scan response */
    p.filter_duplicates = 1;
    snprintf(s_name, sizeof s_name, "%s", name);
    s_found = 0;
    if (s_scanning) ble_gap_disc_cancel();
    int rc = ble_gap_disc(s_own_addr_type, BLE_HS_FOREVER, &p, gap_cb, NULL);
    s_scanning = rc == 0;
    return rc;
}

static int t_stop_scan(void) {
    if (!s_scanning) return 0;
    s_scanning = 0;
    int rc = ble_gap_disc_cancel();
    return rc == BLE_HS_EALREADY ? 0 : rc;
}

static int connect_to(const ble_addr_t *addr) {
    if (s_conn != BLE_HS_CONN_HANDLE_NONE || s_connecting) return BLE_HS_EALREADY;
    s_connect_cancelled = 0;
    s_encrypt_on_connect = 0;
    int rc = ble_gap_connect(s_own_addr_type, addr, CONNECT_TIMEOUT_MS, NULL, gap_cb, NULL);
    s_connecting = rc == 0;
    return rc;
}

static int t_connect_found(void) {
    if (!s_found) return BLE_HS_ENOENT;
    return connect_to(&s_found_addr);
}

static int first_bond(ble_addr_t *out) {
    ble_addr_t peers[MYNEWT_VAL(BLE_STORE_MAX_BONDS)];
    int n = 0;
    int rc = ble_store_util_bonded_peers(peers, &n, MYNEWT_VAL(BLE_STORE_MAX_BONDS));
    if (rc != 0) return rc;
    if (n == 0) return BLE_HS_ENOENT;
    *out = peers[0];
    return 0;
}

/* The stored identity address; NimBLE resolves the unit's rotating RPA through the bond's IRK. */
static int t_connect_bonded(void) {
    ble_addr_t id;
    int rc = first_bond(&id);
    if (rc == 0) rc = connect_to(&id);
    if (rc == 0) s_encrypt_on_connect = 1;
    return rc;
}

/* An explicit pair always runs a fresh SMP pairing. With a bond stored for this peer,
 * ble_gap_security_initiate would only re-encrypt with the stored LTK. If the unit forgot us
 * ("Bluetooth zurücksetzen"), that fails on every retry and never heals. So pair() replaces a
 * stored bond for the connected peer first, as calictl does on AlreadyExists (#200). The
 * reconnect-by-bond path (connect_bonded + the session) never calls pair(). */
static int t_pair(void) {
    struct ble_gap_conn_desc desc;
    struct ble_store_key_sec key;
    struct ble_store_value_sec val;
    if (s_conn == BLE_HS_CONN_HANDLE_NONE) return BLE_HS_ENOTCONN;
    if (ble_gap_conn_find(s_conn, &desc) == 0) {
        memset(&key, 0, sizeof key);
        key.peer_addr = desc.peer_id_addr;
        if (ble_store_read_peer_sec(&key, &val) == 0) {
            cali_log("pair: replacing stored bond");
            int rc = ble_store_util_delete_peer(&desc.peer_id_addr);
            if (rc != 0) return rc;
        }
    }
    return ble_gap_security_initiate(s_conn);
}

static int t_inject_passkey(uint32_t pk) {
    struct ble_sm_io io;
    memset(&io, 0, sizeof io);
    io.action = BLE_SM_IOACT_INPUT;
    io.passkey = pk;
    return ble_sm_inject_io(s_conn, &io);
}

static int t_discover(void) { return op_push(OP_DISC, 0, 0); }

static int t_read(uint16_t short_id) { return op_push(OP_READ, short_id, 0); }

static int t_subscribe(uint16_t short_id) {
    struct chr *c = find_short(short_id);
    if (s_disc_done && (!c || !(c->props & (BLE_GATT_CHR_PROP_NOTIFY | BLE_GATT_CHR_PROP_INDICATE))))
        return BLE_HS_ENOTSUP;      /* after discovery: not a char of the unit that notifies */
    return op_push(OP_SUB, short_id, 0);
}

static int t_write_heartbeat(uint32_t counter) { return op_push(OP_WRITE_HB, CODEC_CHAR_HEARTBEAT, counter); }

static int t_disconnect(void) {
    if (s_connecting) {
        s_connecting = 0;
        s_connect_cancelled = 1;
        int rc = ble_gap_conn_cancel();
        return rc == BLE_HS_EALREADY ? 0 : rc;
    }
    if (s_conn == BLE_HS_CONN_HANDLE_NONE) return 0;
    uint16_t h = s_conn;
    link_reset();                   /* our own drop: its DISCONNECT event is not reported */
    int rc = ble_gap_terminate(h, BLE_ERR_REM_USER_CONN_TERM);
    return rc == BLE_HS_ENOTCONN ? 0 : rc;
}

static int t_remove_bond(void) {
    t_disconnect();
    ble_addr_t id;
    int rc;
    while ((rc = first_bond(&id)) == 0) {
        rc = ble_store_util_delete_peer(&id);
        if (rc != 0) return rc;
    }
    return rc == BLE_HS_ENOENT ? 0 : rc;
}

static int t_has_bond(void) {
    ble_addr_t id;
    return first_bond(&id) == 0;
}

static const char *t_identity(void) {
    ble_addr_t id;
    if (first_bond(&id) != 0) return NULL;
    snprintf(s_identity, sizeof s_identity, "%02X:%02X:%02X:%02X:%02X:%02X", id.val[5], id.val[4],
             id.val[3], id.val[2], id.val[1], id.val[0]);
    return s_identity;
}

static const cali_transport_t TRANSPORT = {
    t_set_sink, t_start_scan, t_stop_scan, t_connect_found, t_connect_bonded, t_pair,
    t_inject_passkey, t_discover, t_read, t_subscribe, t_write_heartbeat, t_disconnect,
    t_remove_bond, t_has_bond, t_identity,
};

const cali_transport_t *cali_ble_nimble_transport(void) { return &TRANSPORT; }

/* ---- init ---------------------------------------------------------------------------------- */

static void on_sync(void) {
    if (ble_hs_id_infer_auto(0, &s_own_addr_type) != 0) s_own_addr_type = BLE_OWN_ADDR_PUBLIC;
    if (s_on_sync) s_on_sync();
}

static void on_reset(int reason) {
    cali_log("ble: host reset %d", reason);
    s_scanning = s_connecting = 0;
    link_reset();
}

void cali_ble_nimble_init(void (*sync_cb)(void)) {
    char base[40];
    snprintf(base, sizeof base, CODEC_UUID_FMT, 0);
    uuid_le_from_str(base, s_base);           /* a fixed generated format: cannot fail */
    s_on_sync = sync_cb;

    ble_hs_cfg.sync_cb = on_sync;
    ble_hs_cfg.reset_cb = on_reset;
    /* Pairing: LE Secure Connections passkey entry — the unit displays, we type (KEYBOARD_ONLY +
     * MITM), bonded. Set before the host syncs, i.e. before any link exists: SMP reads these when
     * it answers/sends the first Pairing Request, and a link opened with other values pairs Just
     * Works, which the unit refuses (the 2026-09-26 calictl bug). */
    ble_hs_cfg.sm_io_cap = BLE_HS_IO_KEYBOARD_ONLY;
    ble_hs_cfg.sm_bonding = 1;
    ble_hs_cfg.sm_mitm = 1;
    ble_hs_cfg.sm_sc = 1;
    ble_hs_cfg.sm_our_key_dist = BLE_SM_PAIR_KEY_DIST_ENC | BLE_SM_PAIR_KEY_DIST_ID;
    ble_hs_cfg.sm_their_key_dist = BLE_SM_PAIR_KEY_DIST_ENC | BLE_SM_PAIR_KEY_DIST_ID;
    ble_hs_cfg.store_status_cb = ble_store_util_status_rr;

    cali_ble_store_init();                    /* persistent bonds (not RAM-only ble_store_config) */
}
