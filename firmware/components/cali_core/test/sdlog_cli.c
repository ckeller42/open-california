/* sdlog_cli.c — line-protocol driver for tests/firmware/test_sdlog.py ONLY; not part of the ESP build.
 * Links sdlog.c + time.c + json.c + csrc/codec.c + the real host platform (the kv store under argv[1]
 * holds the boot counter), and fakes the session (link up, held frames) and the owner->SD queue.
 *
 * stdin, one line each:
 *   boot                    cali_time_init (the boot counter) -> "BOOT <n>"
 *   init [qcap]             cali_sdlog_init with a fake queue of qcap records (default 64); "init none" = NULL
 *   card ok|absent|error <total_mb> <free_mb>   cali_sdlog_card
 *   io <what> <char hex> [hex]   cali_sdlog_io (what = cali_session.h CALI_SESSION_IO_*)
 *   link 0|1                the fake cali_session_link_up
 *   frame <i> <hex>         the fake session's held frame for CODEC_CHARS[i]
 *   tick <ms>               cali_sdlog_tick
 *   drain                   SD side: cali_sdlog_write every queued record -> "WROTE <n> ROLLED <r>" | "ERR write"
 *   open <dir>              cali_sdlog_open -> "OPEN <rc> NEXT <seq>"
 *   fill <n>                write n notify records straight to the SD side -> "ROLLED <r>"
 *   sync | close            cali_sdlog_sync ("SYNC <rc>") / cali_sdlog_close
 *   deloldest               "DEL <rc>"
 *   json                    "JSON <the device.sd object>"
 *   queued                  "QUEUED <n>"
 *   page <since> <max> <cap> <boot> [dir]   cali_sdlog_page over dir (default: the last opened one)
 *                           -> "PAGE <rc>", the page bytes, then "END"
 * cali_log prints "LOG <text>".
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "cali_json.h"
#include "cali_platform.h"
#include "cali_sdlog.h"
#include "cali_session.h"
#include "cali_time.h"
#include "codec_chars.h"

#define QMAX 256

static cali_sdlog_rec_t s_q[QMAX];
static int s_qn, s_qcap = 64;
static int s_link;
static char s_dir[256];
static struct {
    uint8_t f[CODEC_FRAME_MAX];
    size_t n;
} s_fr[CODEC_NCHARS];

int cali_session_link_up(void) { return s_link; }
int cali_session_frame(size_t i, const uint8_t **frame, size_t *len) {
    if (i >= CODEC_NCHARS || !s_fr[i].n) return 0;
    *frame = s_fr[i].f;
    *len = s_fr[i].n;
    return 1;
}

static int put(const cali_sdlog_rec_t *r) {
    if (s_qn >= s_qcap) return -1;
    s_q[s_qn++] = *r;
    return 0;
}

static size_t unhex(const char *h, uint8_t *out, size_t cap) {
    size_t n = 0;
    unsigned b;
    while (n < cap && h[0] && h[1] && sscanf(h, "%2x", &b) == 1) {
        out[n++] = (uint8_t)b;
        h += 2;
    }
    return n;
}

int main(int argc, char **argv) {
    char line[512], buf[256];
    if (argc < 2 || cali_platform_init(argv[1]) != 0) return 2;
    setvbuf(stdout, NULL, _IOLBF, 0);
    while (fgets(line, sizeof line, stdin)) {
        line[strcspn(line, "\r\n")] = 0;
        if (strcmp(line, "boot") == 0) {
            printf("BOOT %lu\n", (unsigned long)cali_time_init());
        } else if (strncmp(line, "init", 4) == 0) {
            if (strcmp(line, "init none") == 0) {
                cali_sdlog_init(NULL);
            } else {
                if (line[4] == ' ') s_qcap = atoi(line + 5);
                cali_sdlog_init(put);
            }
        } else if (strncmp(line, "card ", 5) == 0) {
            char st[16];
            unsigned long t = 0, f = 0;
            if (sscanf(line + 5, "%15s %lu %lu", st, &t, &f) < 1) return 3;
            cali_sdlog_card(strcmp(st, "ok") == 0 ? CALI_SD_OK : strcmp(st, "error") == 0 ? CALI_SD_ERROR : CALI_SD_ABSENT,
                            (uint32_t)t, (uint32_t)f);
        } else if (strncmp(line, "io ", 3) == 0) {
            int what;
            unsigned c;
            char hex[128] = "";
            uint8_t d[CODEC_FRAME_MAX];
            if (sscanf(line + 3, "%d %x %127s", &what, &c, hex) < 2) return 3;
            size_t n = unhex(hex, d, sizeof d);
            cali_sdlog_io(what, (uint16_t)c, n ? d : NULL, n);
        } else if (strncmp(line, "link ", 5) == 0) {
            s_link = atoi(line + 5);
        } else if (strncmp(line, "frame ", 6) == 0) {
            int i;
            char hex[128];
            if (sscanf(line + 6, "%d %127s", &i, hex) != 2 || i < 0 || i >= CODEC_NCHARS) return 3;
            s_fr[i].n = unhex(hex, s_fr[i].f, sizeof s_fr[i].f);
        } else if (strncmp(line, "tick ", 5) == 0) {
            cali_sdlog_tick(strtoull(line + 5, NULL, 10));
        } else if (strcmp(line, "drain") == 0) {
            int rolled = 0, n = s_qn, rc = 0;
            for (int i = 0; i < s_qn && rc >= 0; i++) {
                rc = cali_sdlog_write(&s_q[i]);
                rolled += rc > 0;
            }
            s_qn = 0;
            if (rc < 0) printf("ERR write\n");
            else printf("WROTE %d ROLLED %d\n", n, rolled);
        } else if (strncmp(line, "page ", 5) == 0) {
            static char pg[1 << 20];
            static char dir[256];
            unsigned long since, max, cap, boot;
            int k = sscanf(line + 5, "%lu %lu %lu %lu %255s", &since, &max, &cap, &boot, dir);
            if (k < 4 || cap > sizeof pg) return 3;
            int rc = cali_sdlog_page(k == 5 ? dir : s_dir, (uint32_t)since, (uint32_t)max, (uint32_t)boot, pg, cap);
            printf("PAGE %d\n", rc);
            if (rc > 0) fwrite(pg, 1, (size_t)rc, stdout);
            printf("END\n");
        } else if (strncmp(line, "open ", 5) == 0) {
            snprintf(s_dir, sizeof s_dir, "%s", line + 5);
            int rc = cali_sdlog_open(line + 5);
            printf("OPEN %d NEXT %lu\n", rc, (unsigned long)cali_sdlog_next_seq());
        } else if (strncmp(line, "fill ", 5) == 0) {
            int n = atoi(line + 5), rolled = 0;
            cali_sdlog_rec_t r = {.up = 1000, .tsrc = CALI_TSRC_NTP, .ts = 1789639127412LL,
                                  .ev = CALI_SDLOG_NOTIFY, .chr = 0x1602, .len = 8};
            for (int i = 0; i < n; i++) {
                r.up++;
                r.ts++;
                int rc = cali_sdlog_write(&r);
                if (rc < 0) return 4;
                rolled += rc;
            }
            printf("ROLLED %d\n", rolled);
        } else if (strcmp(line, "sync") == 0) {
            printf("SYNC %d\n", cali_sdlog_sync());
        } else if (strcmp(line, "close") == 0) {
            cali_sdlog_close();
        } else if (strcmp(line, "deloldest") == 0) {
            printf("DEL %d\n", cali_sdlog_delete_oldest());
        } else if (strcmp(line, "queued") == 0) {
            printf("QUEUED %d\n", s_qn);
        } else if (strcmp(line, "json") == 0) {
            cali_json_t j;
            cali_json_begin(&j, buf, sizeof buf);
            cali_json_key(&j, "sd");
            cali_sdlog_json(&j);
            if (cali_json_end(&j) < 0) return 3;
            printf("JSON %s\n", buf);
        } else {
            printf("ERR %s\n", line);
        }
    }
    return 0;
}
