/* store_cli.c — line-protocol driver for tests/firmware/test_ble_store_kv.py ONLY; not part of the
 * ESP build. Links against the host NimBLE objects (make -C firmware/host store-cli) and calls the
 * store callbacks cali_ble_store_init() installs in ble_hs_cfg, exactly as the NimBLE host would.
 *
 * Usage: store-cli STORE_DIR (runs cali_platform_init + cali_ble_store_init; the store's own LOG
 * lines appear first), then lines on stdin. T = our|peer; A = a hex byte X (public address
 * XX:XX:XX:XX:XX:XX) or "any" (BLE_ADDR_ANY). Every command prints "rc=<n>" and, for a successful
 * read, the record:
 *   wsec T A K        write a sec record for A with ltk[0] = hex byte K
 *   rsec T A I        read (key peer_addr A, idx I)          -> rc=0 addr=XX ltk=KK
 *   dsec T A I        delete (key peer_addr A, idx I)
 *   wcccd A H F       write a CCCD for A, chr_val_handle H, flags F (decimal)
 *   rcccd A H I       read (key peer_addr A, chr_val_handle H, idx I) -> rc=0 addr=XX h=H f=F
 *   dcccd A H I       delete (key peer_addr A, chr_val_handle H, idx I)
 */
#include <stdio.h>
#include <string.h>

#include "host/ble_hs.h"
#include "host/ble_store.h"

#include "cali_ble_nimble.h"
#include "cali_platform.h"

static int parse_addr(const char *s, ble_addr_t *out) {
    unsigned b;
    if (strcmp(s, "any") == 0) {
        *out = *BLE_ADDR_ANY;
        return 0;
    }
    if (sscanf(s, "%x", &b) != 1 || b > 0xff) return -1;
    out->type = BLE_ADDR_PUBLIC;
    memset(out->val, (int)b, sizeof out->val);
    return 0;
}

static int parse_type(const char *s) {
    if (strcmp(s, "our") == 0) return BLE_STORE_OBJ_TYPE_OUR_SEC;
    if (strcmp(s, "peer") == 0) return BLE_STORE_OBJ_TYPE_PEER_SEC;
    return -1;
}

int main(int argc, char **argv) {
    char line[256];

    if (argc != 2 || cali_platform_init(argv[1]) != 0) {
        fprintf(stderr, "usage: store-cli STORE_DIR (a usable directory)\n");
        return 2;
    }
    cali_ble_store_init();

    while (fgets(line, sizeof line, stdin)) {
        char cmd[16], a1[16], a2[16];
        unsigned x = 0, y = 0;
        union ble_store_key key;
        union ble_store_value val;
        int n = sscanf(line, "%15s %15s %15s %x %u", cmd, a1, a2, &x, &y);
        int rc, type;

        if (n < 1) continue;
        memset(&key, 0, sizeof key);
        memset(&val, 0, sizeof val);

        if (strcmp(cmd, "wsec") == 0 && n >= 4 && (type = parse_type(a1)) > 0 &&
            parse_addr(a2, &val.sec.peer_addr) == 0) {
            val.sec.ltk_present = 1;
            val.sec.ltk[0] = (uint8_t)x;
            printf("rc=%d\n", ble_hs_cfg.store_write_cb(type, &val));
        } else if ((strcmp(cmd, "rsec") == 0 || strcmp(cmd, "dsec") == 0) && n >= 4 &&
                   (type = parse_type(a1)) > 0 && parse_addr(a2, &key.sec.peer_addr) == 0) {
            key.sec.idx = (uint8_t)x;
            if (cmd[0] == 'd') {
                printf("rc=%d\n", ble_hs_cfg.store_delete_cb(type, &key));
            } else if ((rc = ble_hs_cfg.store_read_cb(type, &key, &val)) == 0) {
                printf("rc=0 addr=%02x ltk=%02x\n", val.sec.peer_addr.val[0], val.sec.ltk[0]);
            } else {
                printf("rc=%d\n", rc);
            }
        } else if (strcmp(cmd, "wcccd") == 0 && n >= 4 &&
                   parse_addr(a1, &val.cccd.peer_addr) == 0) {
            unsigned h = 0, f = 0;
            sscanf(line, "%*s %*s %u %u", &h, &f);
            val.cccd.chr_val_handle = (uint16_t)h;
            val.cccd.flags = (uint16_t)f;
            printf("rc=%d\n", ble_hs_cfg.store_write_cb(BLE_STORE_OBJ_TYPE_CCCD, &val));
        } else if ((strcmp(cmd, "rcccd") == 0 || strcmp(cmd, "dcccd") == 0) && n >= 3 &&
                   parse_addr(a1, &key.cccd.peer_addr) == 0) {
            unsigned h = 0, i = 0;
            sscanf(line, "%*s %*s %u %u", &h, &i);
            key.cccd.chr_val_handle = (uint16_t)h;
            key.cccd.idx = (uint8_t)i;
            if (cmd[0] == 'd') {
                printf("rc=%d\n", ble_hs_cfg.store_delete_cb(BLE_STORE_OBJ_TYPE_CCCD, &key));
            } else if ((rc = ble_hs_cfg.store_read_cb(BLE_STORE_OBJ_TYPE_CCCD, &key, &val)) == 0) {
                printf("rc=0 addr=%02x h=%u f=%u\n", val.cccd.peer_addr.val[0],
                       (unsigned)val.cccd.chr_val_handle, (unsigned)val.cccd.flags);
            } else {
                printf("rc=%d\n", rc);
            }
        } else {
            printf("ERR\n");
        }
        fflush(stdout);
    }
    return 0;
}
