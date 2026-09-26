/*
 * Spike (deleted in Task 6): the upstream NimBLE Linux host, over HCI-over-TCP to a Bumble virtual
 * controller, finds the fake unit by name, pairs with LE Secure Connections passkey entry (the unit
 * displays, we type: KEYBOARD_ONLY + MITM), then reads the auth-gated 1004 characteristic.
 *
 * stdout protocol (one line each, consumed by tests/firmware):
 *   PASSKEY?          the unit displays a code; type it on stdin as 6 digits
 *   ENCRYPTED         the link is encrypted (pairing done)
 *   READ1004 <hex>    the 1004 value; exit 0
 *   FAIL <where> <rc> anything else; exit 1
 * stdin: a passkey line, or "quit".
 *
 * Usage: cali-spike [--hci-port] <tcp-port>
 */
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "host/ble_hs.h"
#include "host/ble_uuid.h"
#include "nimble/nimble_npl.h"
#include "nimble/nimble_port.h"

void ble_hci_sock_ack_handler(void *param);
void ble_hci_sock_set_device(int dev);
void ble_store_config_init(void);

static const char UNIT_NAME[] = "VWCAMPER";
static const char UUID_FMT[] = "0000%s-6c77-4b7d-bbf6-a5e587701f3d";

static uint16_t s_conn = BLE_HS_CONN_HANDLE_NONE;
static uint32_t s_passkey;
static struct ble_npl_event s_passkey_ev;

static void fail(const char *where, int rc)
{
    printf("FAIL %s %d\n", where, rc);
    fflush(stdout);
    exit(1);
}

/* "0000xxxx-6c77-..." -> a 128-bit NimBLE UUID (the string is big-endian, NimBLE stores LE). */
static int uuid_from_str(const char *s, ble_uuid_any_t *out)
{
    uint8_t be[16], le[16];
    int n = 0;
    for (const char *p = s; *p && n < 32; p++) {
        int v;
        if (*p == '-') {
            continue;
        }
        if (*p >= '0' && *p <= '9') v = *p - '0';
        else if (*p >= 'a' && *p <= 'f') v = *p - 'a' + 10;
        else if (*p >= 'A' && *p <= 'F') v = *p - 'A' + 10;
        else return -1;
        if (n % 2 == 0) be[n / 2] = (uint8_t)(v << 4);
        else be[n / 2] |= (uint8_t)v;
        n++;
    }
    if (n != 32) {
        return -1;
    }
    for (int i = 0; i < 16; i++) {
        le[i] = be[15 - i];
    }
    return ble_uuid_init_from_buf(out, le, sizeof le);
}

static int on_read(uint16_t conn, const struct ble_gatt_error *err, struct ble_gatt_attr *attr, void *arg)
{
    (void)conn; (void)arg;
    if (err->status != 0) {
        fail("read1004", err->status);
    }
    uint16_t len = OS_MBUF_PKTLEN(attr->om);
    uint8_t buf[512];
    if (len > sizeof buf) {
        len = sizeof buf;
    }
    os_mbuf_copydata(attr->om, 0, len, buf);
    printf("READ1004 ");
    for (int i = 0; i < len; i++) {
        printf("%02x", buf[i]);
    }
    printf("\n");
    fflush(stdout);
    exit(0);
}

static int on_chr(uint16_t conn, const struct ble_gatt_error *err, const struct ble_gatt_chr *chr, void *arg)
{
    int *found = arg;
    if (err->status == 0) {
        if (!*found) {
            *found = 1;
            int rc = ble_gattc_read(conn, chr->val_handle, on_read, NULL);
            if (rc != 0) {
                fail("ble_gattc_read", rc);
            }
        }
        return 0;
    }
    if (err->status == BLE_HS_EDONE) {
        if (!*found) {
            fail("disc_1004_not_found", 0);
        }
        return 0;
    }
    fail("disc_chrs_by_uuid", err->status);
    return 0;
}

static void read_1004(uint16_t conn)
{
    static ble_uuid_any_t uuid;
    static int found;
    char s[40];
    snprintf(s, sizeof s, UUID_FMT, "1004");
    if (uuid_from_str(s, &uuid) != 0) {
        fail("uuid", 0);
    }
    int rc = ble_gattc_disc_chrs_by_uuid(conn, 1, 0xffff, &uuid.u, on_chr, &found);
    if (rc != 0) {
        fail("ble_gattc_disc_chrs_by_uuid", rc);
    }
}

static int gap_cb(struct ble_gap_event *ev, void *arg);

static void start_scan(void)
{
    struct ble_gap_disc_params p = {0};
    p.passive = 0;
    p.filter_duplicates = 1;
    int rc = ble_gap_disc(BLE_OWN_ADDR_PUBLIC, 10000, &p, gap_cb, NULL);
    if (rc != 0) {
        fail("ble_gap_disc", rc);
    }
}

static int is_unit(const struct ble_gap_disc_desc *d)
{
    struct ble_hs_adv_fields f;
    if (ble_hs_adv_parse_fields(&f, d->data, d->length_data) != 0) {
        return 0;
    }
    return f.name != NULL && f.name_len == sizeof UNIT_NAME - 1 &&
           memcmp(f.name, UNIT_NAME, f.name_len) == 0;
}

static int gap_cb(struct ble_gap_event *ev, void *arg)
{
    (void)arg;
    int rc;
    switch (ev->type) {
    case BLE_GAP_EVENT_DISC:
        if (!is_unit(&ev->disc)) {
            return 0;
        }
        ble_gap_disc_cancel();
        rc = ble_gap_connect(BLE_OWN_ADDR_PUBLIC, &ev->disc.addr, 10000, NULL, gap_cb, NULL);
        if (rc != 0) {
            fail("ble_gap_connect", rc);
        }
        return 0;
    case BLE_GAP_EVENT_DISC_COMPLETE:
        if (s_conn == BLE_HS_CONN_HANDLE_NONE) {
            fail("scan_timeout", ev->disc_complete.reason);
        }
        return 0;
    case BLE_GAP_EVENT_CONNECT:
        if (ev->connect.status != 0) {
            fail("connect", ev->connect.status);
        }
        s_conn = ev->connect.conn_handle;
        rc = ble_gap_security_initiate(s_conn);
        if (rc != 0) {
            fail("ble_gap_security_initiate", rc);
        }
        return 0;
    case BLE_GAP_EVENT_PASSKEY_ACTION:
        if (ev->passkey.params.action != BLE_SM_IOACT_INPUT) {
            fail("passkey_action", ev->passkey.params.action);
        }
        printf("PASSKEY?\n");
        fflush(stdout);
        return 0;
    case BLE_GAP_EVENT_ENC_CHANGE:
        if (ev->enc_change.status != 0) {
            fail("enc_change", ev->enc_change.status);
        }
        printf("ENCRYPTED\n");
        fflush(stdout);
        read_1004(ev->enc_change.conn_handle);
        return 0;
    case BLE_GAP_EVENT_DISCONNECT:
        fail("disconnect", ev->disconnect.reason);
        return 0;
    case BLE_GAP_EVENT_REPEAT_PAIRING:
        /* A stale bond on our side: drop it and pair afresh. */
        {
            struct ble_gap_conn_desc desc;
            if (ble_gap_conn_find(ev->repeat_pairing.conn_handle, &desc) == 0) {
                ble_store_util_delete_peer(&desc.peer_id_addr);
            }
        }
        return BLE_GAP_REPEAT_PAIRING_RETRY;
    default:
        return 0;
    }
}

/* Runs on the NimBLE thread: hand the typed code to the SM. */
static void passkey_ev_cb(struct ble_npl_event *ev)
{
    (void)ev;
    struct ble_sm_io io = {0};
    io.action = BLE_SM_IOACT_INPUT;
    io.passkey = s_passkey;
    int rc = ble_sm_inject_io(s_conn, &io);
    if (rc != 0) {
        fail("ble_sm_inject_io", rc);
    }
}

static void *stdin_thread(void *arg)
{
    (void)arg;
    char line[64];
    while (fgets(line, sizeof line, stdin) != NULL) {
        if (strncmp(line, "quit", 4) == 0) {
            exit(0);
        }
        s_passkey = (uint32_t)strtoul(line, NULL, 10);
        ble_npl_eventq_put(nimble_port_get_dflt_eventq(), &s_passkey_ev);
    }
    exit(0);
    return NULL;
}

static void *hci_thread(void *arg)
{
    ble_hci_sock_ack_handler(arg);
    return NULL;
}

static void on_sync(void)
{
    start_scan();
}

static void on_reset(int reason)
{
    fail("host_reset", reason);
}

int main(int argc, char **argv)
{
    int port = 0;
    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--hci-port") == 0 && i + 1 < argc) {
            port = atoi(argv[++i]);
        } else {
            port = atoi(argv[i]);
        }
    }
    if (port <= 0) {
        fprintf(stderr, "usage: %s [--hci-port] <tcp-port>\n", argv[0]);
        return 2;
    }
    setvbuf(stdout, NULL, _IOLBF, 0);
    ble_hci_sock_set_device(port);

    nimble_port_init();       /* also connects the TCP HCI socket (ble_transport_ll_init) */
    ble_store_config_init();  /* RAM-only bond store (BLE_STORE_CONFIG_PERSIST=0) */

    ble_hs_cfg.sync_cb = on_sync;
    ble_hs_cfg.reset_cb = on_reset;
    ble_hs_cfg.sm_io_cap = BLE_HS_IO_KEYBOARD_ONLY;
    ble_hs_cfg.sm_bonding = 1;
    ble_hs_cfg.sm_mitm = 1;
    ble_hs_cfg.sm_sc = 1;
    ble_hs_cfg.sm_our_key_dist = BLE_SM_PAIR_KEY_DIST_ENC | BLE_SM_PAIR_KEY_DIST_ID;
    ble_hs_cfg.sm_their_key_dist = BLE_SM_PAIR_KEY_DIST_ENC | BLE_SM_PAIR_KEY_DIST_ID;
    ble_hs_cfg.store_status_cb = ble_store_util_status_rr;

    ble_npl_event_init(&s_passkey_ev, passkey_ev_cb, NULL);

    pthread_t hci, in;
    if (pthread_create(&hci, NULL, hci_thread, NULL) != 0 ||
        pthread_create(&in, NULL, stdin_thread, NULL) != 0) {
        fail("pthread_create", 0);
    }
    nimble_port_run();
    return 0;
}
