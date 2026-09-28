/* globals.d.ts — the globals page.js gets from index.html's first inline script (rendered by
 * tools/gen_c_dict.py): for `tsc --checkJs` only (firmware/web/jsconfig.json); never served. */

/** Generated constants (tools/wifi_consts.py). */
declare const CFG: { pollMs: number; host: string; ssidMax: number; pskMin: number; pskMax: number };

/** The strings.json table: key -> EN/DE text. */
declare const STR: Record<string, { en: string; de: string }>;
