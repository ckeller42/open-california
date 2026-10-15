/* page.js — the ESP32 firmware's "Satellite settings" page script (/device; #154 setup page): the WiFi
 * box (first setup over the hotspot, Change network… online), the camper unit's pairing state + a link
 * to the calictl UI (whose Device status screen shows the satellite's status, #267), the switches. SOURCE: tools/gen_c_dict.py inlines
 * it into index_gen.html (index.html's PAGE_JS placeholder), right after the script that defines
 * CFG (generated constants) and STR (the strings.json table) — their types: globals.d.ts.
 * Un-built JS: `tools/ci.sh webcheck` type-checks it (tsc --checkJs, jsconfig.json, 0 errors) and
 * tests/firmware/test_web_e2e.py drives it in Chromium. */
// @ts-check
"use strict";

/* The firmware API shapes (firmware/components/cali_core/include/cali_web.h). */
/**
 * @typedef {{ mode: string, ssid: string|null, ip: string|null, rssi: number|null }} Wifi
 * @typedef {{ ssid: string, rssi: number, secure: boolean }} Ap
 * @typedef {Wifi & { last_error: string|null, reverted: boolean, scan: Ap[] }} WifiGet
 * @typedef {{ state: string, available_sha: string|null, progress_pct: number, error: string|null, channel: string }} Ota
 * @typedef {{ source: string, synced: boolean, boot: number }} Time
 * @typedef {{ state: string, total_mb: number|null, free_mb: number|null, last_seq: number|null, dropped: number }} Sd
 * @typedef {{ heap_free_kb: number|null, heap_min_kb: number|null, psram_free_kb: number|null, cpu_pct: number|null }} Res
 * @typedef {{ pct: number|null, mv: number|null, usb: boolean|null, charging: boolean|null, runtime_min: number|null }} Battery
 * @typedef {{ enabled: boolean, state: string }} AutoCamper
 * @typedef {{ state: string|null, lux: number|null, motion: number|null, brightness_pct: number|null, sensor: string|null, auto: boolean, prox_wake: boolean, motion_wake: boolean, min: number, max: number, offset: number }} Display
 * @typedef {{ pairing: { address: string|null }, wifi: Wifi, fw: string, ota: Ota, time: Time, sd: Sd, res: Res, battery: Battery, auto_camper: AutoCamper, display: Display, ux?: { enabled: boolean }, experimental?: { sensors: boolean } }} Device  the /api/state fields this page reads
 * @typedef {{ device: Device }} State
 * @typedef {{ ok: boolean, error?: string }} PostResult
 * @typedef {{ ssid: string, polls: number, left: boolean, away: boolean }} Join  away: seen off station or unreachable since the POST
 * @typedef {{ id: number, type: string, alias: string, last_seen_s: number|null, value: Record<string, number|null>|null }} SensCfg
 * @typedef {{ id: number, type: string, model: string, rssi: number }} SensSeen
 * @typedef {{ configured: SensCfg[], seen: SensSeen[], scan: string, error?: string }} Sensors  GET/POST /api/sensors
 */

const LANG = /^de\b/i.test(navigator.language || "") ? "de" : "en";
/**
 * @param {string} key a strings.json key
 * @param {Record<string, string|number>} [vars] "{name}" substitutions
 * @returns {string}
 */
function t(key, vars) {
  let s = STR[key] ? STR[key][LANG] : key;
  for (const k in vars || {}) s = s.split("{" + k + "}").join(String((vars || {})[k]));
  return s;
}
/* /api/wifi last_error -> the text that says why a join failed */
/** @type {Record<string, string>} */
const JOIN_FAILED = {not_found: "join_failed_not_found", auth: "join_failed_auth", other: "join_failed_other"};
/* ... and the same reason as a phrase, for "The new network didn't work (<why>); back on <ssid>." */
/** @type {Record<string, string>} */
const WHY = {not_found: "why_not_found", auth: "why_auth", other: "why_other"};
/** @type {Record<string, string>} */
const POST_ERROR = {ssid: "err_ssid", psk: "err_psk", store: "err_store"};
/* device.ota.state -> its text (idle: none) */
/** @type {Record<string, string>} */
const OTA_TEXT = {checking: "ota_checking", up_to_date: "ota_up_to_date", available: "ota_available",
  downloading: "ota_downloading", verifying: "ota_verifying", rebooting: "ota_rebooting", error: "ota_error"};

/**
 * @param {string} id
 * @returns {HTMLElement} the element (index.html has every id this script uses)
 */
function $(id) {
  const e = document.getElementById(id);
  if (!e) throw new Error("missing #" + id);
  return e;
}
/** @param {string} id @returns {HTMLInputElement} */
const input = (id) => /** @type {HTMLInputElement} */ ($(id));
/** @param {string} id @returns {HTMLSelectElement} */
const select = (id) => /** @type {HTMLSelectElement} */ ($(id));
/**
 * @template {keyof HTMLElementTagNameMap} K
 * @param {K} tag
 * @param {string} [text]
 * @param {string} [cls]
 * @returns {HTMLElementTagNameMap[K]}
 */
function el(tag, text, cls) {
  const e = document.createElement(tag);
  if (text !== undefined) e.textContent = text;
  if (cls) e.className = cls;
  return e;
}
/**
 * @template T
 * @param {string} url
 * @param {RequestInit} [opts]
 * @returns {Promise<{status: number, body: T}>}
 */
async function fetchJson(url, opts) {
  const r = await fetch(url, Object.assign({cache: "no-store"}, opts));
  return {status: r.status, body: await r.json()};
}
/** @returns {Promise<WifiGet>} */
async function getWifi() {
  return (/** @type {{body: WifiGet}} */ (await fetchJson("/api/wifi"))).body;
}

document.title = $("title").textContent = t("title");
$("setup-hint").textContent = t("setup_hint");
$("ssid-label").textContent = t("network");
$("scan-empty").textContent = t("no_networks");
input("ssid-name").placeholder = t("ssid_name");
$("ssid-name").setAttribute("aria-label", t("ssid_name"));
$("psk-label").textContent = t("password");
$("psk-show-l").textContent = t("show_psk");
$("connect").textContent = t("connect");
$("rescan").textContent = t("rescan");
$("wifi-change").textContent = t("wifi_change");
$("pair-title").textContent = t("pair_title");
$("adv-title").textContent = t("adv_title");
$("fw-title").textContent = t("fw_title");
$("ac-title").textContent = t("ac_title");
$("fw-check").textContent = t("fw_check");
$("fw-channel-l").textContent = t("fw_channel");
select("fw-channel").options[0].textContent = t("fw_ch_stable");
select("fw-channel").options[1].textContent = t("fw_ch_latest");
$("scr-title").textContent = t("scr_title");
$("exp-title").textContent = t("exp_title");
$("exp-hint").textContent = t("exp_hint");
$("sens-title").textContent = t("sens_title");
$("sens-hint").textContent = t("sens_hint");
$("loading").textContent = t("loading");
$("scr-dn").setAttribute("aria-label", t("scr_less"));
$("scr-up").setAttribute("aria-label", t("scr_more"));
document.documentElement.lang = LANG;

/** @type {Join|null} a submitted join being watched */
let joining = null;
/** @type {Wifi|null} the last polled device.wifi */
let wifi = null;

/** A link. @param {string} text @param {string} href @returns {HTMLAnchorElement} */
function link(text, href) {
  const a = el("a", text);
  a.href = href;
  return a;
}

/** The back link + the Camper unit box. @param {Device} d */
function renderLink(d) {
  /* the calictl web UI: GET / in station mode; elsewhere (the setup hotspot) GET /app, where its
   * pairing wizard pairs the unit without home WiFi */
  const href = d.wifi.mode === "station" ? "/" : "/app";
  $("device").replaceChildren(link(t("app_link"), href));
  /* state-dependent (owner 2026-10-15): paired -> the camper UI's Device status (Unpair… lives there);
   * not paired -> the camper UI with the pairing wizard open (app.js openFromHash) */
  const paired = !!d.pairing.address;
  $("pair-status").textContent = t(paired ? "pair_on" : "pair_off");
  $("pair-link").textContent = t(paired ? "pair_manage" : "pair_now");
  /** @type {HTMLAnchorElement} */ ($("pair-link")).href = href + (paired ? "#devicestatus" : "#pair");
}

/** @type {boolean} Change network… was tapped (online, the form shows only then) */
let wifiOpen = false, formShown = false;
/** The WiFi box: online "Connected to <ssid> · <ip> · <dBm>" + Change network…; the join form over
 * the hotspot (first setup), after Change network…, or while a setup-flow join runs.
 * @param {Wifi} w */
function renderWifi(w) {
  const st = w.mode === "station", form = !st || wifiOpen;
  $("setup-title").textContent = t(st ? "wifi_title" : "setup_title");
  $("wifi-now").textContent = st && $("wifi-switch").hidden ? [t("wifi_now", {ssid: w.ssid || ""}), w.ip, w.rssi + " dBm"].join(" · ") : "";
  $("wifi-change").hidden = form || joining !== null || !$("wifi-switch").hidden;
  $("wifi-form").hidden = !form || !$("wifi-switch").hidden;
  if (form && !formShown && !joining) scan();
  formShown = form;
}

/* The clock the SD log stamps with (R_FW_TIME): SNTP synced or not, and the boot number. */
/** @param {Time} c */
function renderTime(c) {
  const key = c.source === "none" ? "time_none" : c.source === "unit" ? "time_unit" : c.synced ? "time_synced" : "time_waiting";
  $("time").textContent = t(key, {boot: c.boot});
}

/* The SD log's card (R_FW_SD_LOG): "Storage: SD 28.1 of 29.7 GB free · log #1042 · 3 records dropped". */
/** @param {Sd} sd */
function renderSd(sd) {
  /** @param {number|null} mb */
  const gb = (mb) => ((mb || 0) / 1024).toFixed(1);
  const parts = [sd.state === "ok" ? t("sd_ok", {free: gb(sd.free_mb), total: gb(sd.total_mb)})
    : t(sd.state === "error" ? "sd_error" : "sd_absent")];
  if (sd.state === "ok" && sd.last_seq !== null) parts.push(t("sd_log", {seq: sd.last_seq}));
  if (sd.dropped > 0) parts.push(t("sd_dropped", {n: sd.dropped}));
  $("sd").textContent = t("sd_title") + ": " + parts.join(" · ");
}

/* The satellite's own resources: internal RAM free (lowest since boot), PSRAM free, CPU busy. */
/** @param {Res} r */
function renderRes(r) {
  const parts = [];
  if (r.heap_free_kb !== null) parts.push(t("res_ram", {free: r.heap_free_kb, min: r.heap_min_kb ?? "?"}));
  if (r.psram_free_kb !== null) parts.push(t("res_psram", {free: r.psram_free_kb}));
  if (r.cpu_pct !== null) parts.push(t("res_cpu", {pct: r.cpu_pct}));
  $("res").textContent = parts.length ? t("res_title") + ": " + parts.join(" · ") : "";
}

/* The CoreS3's own battery (R_FW_BATTERY): "Battery: 64 % · 3.89 V · on battery · ~2 h 10 min left";
 * empty while the percent is unknown (no power chip). */
/** @param {Battery} b */
function renderBattery(b) {
  if (b.pct === null) { $("battery").textContent = ""; return; }
  const parts = [b.pct + " %"];
  if (b.mv !== null) parts.push((b.mv / 1000).toFixed(2) + " V");
  if (b.usb !== null) parts.push(t(b.usb ? (b.charging ? "batt_charging" : "batt_usb") : "batt_on"));
  if (!b.usb && b.runtime_min !== null) {
    const h = Math.floor(b.runtime_min / 60), m = b.runtime_min % 60;
    parts.push(t("batt_left", {time: h ? h + " h " + m + " min" : m + " min"}));
  }
  $("battery").textContent = t("batt_title") + ": " + parts.join(" · ");
}

/* The firmware row (R_FW_OTA): the running build + its release channel, the update's progress,
 * Check / Install <sha7> and the channel select (station mode only, like the firmware's POST /api/ota). */
/** @param {Device} d */
function renderFw(d) {
  const o = d.ota, sha = (o.available_sha || "").slice(0, 7), key = OTA_TEXT[o.state];
  const station = d.wifi.mode === "station", busy = ["checking", "downloading", "verifying", "rebooting"].includes(o.state);
  let text = t("fw_running", {sha: d.fw}) + " · " + t(o.channel === "latest" ? "fw_ch_latest" : "fw_ch_stable");
  if (key) text += " · " + t(key, {sha: sha, pct: o.progress_pct, error: o.error || ""});
  if (!station) text += " · " + t("station_only");
  $("fw-status").textContent = text;
  $("fw-check").hidden = !station || busy;
  const install = $("fw-install");
  install.hidden = !station || busy || !sha;
  install.textContent = t("fw_install", {sha: sha});
  const sel = select("fw-channel");
  sel.hidden = $("fw-channel-l").hidden = !station || busy;
  sel.dataset.v = o.channel;
  if (document.activeElement !== sel) sel.value = o.channel;
}

/** The one on/off row pattern (owner 2026-10-15): one compact line, "<id>-status" left and the
 * "<id>-toggle" switch right (role=switch + aria-checked, named by the status: index.html). Every
 * switch is station only (the firmware's 403 setup_mode): over the hotspot the status ends in "needs
 * the home WiFi" and the switch is hidden.
 * @param {string} id @param {string[]} parts the status (empty parts dropped) @param {boolean} on @param {boolean} st station mode */
function row(id, parts, on, st) {
  const b = /** @type {HTMLButtonElement} */ ($(id + "-toggle"));
  $(id + "-status").textContent = [...parts, st ? "" : t("station_only")].filter(Boolean).join(" · ");
  b.hidden = !st;
  b.disabled = false;
  b.setAttribute("aria-checked", String(on));
}

/** Auto camper (R_FW_AUTO_CAMPER), "Send usage data" (R_UX_TELEMETRY), Experimental (R_FW_EXPERIMENTAL). @param {Device} d */
function renderRows(d) {
  const a = d.auto_camper, st = d.wifi.mode === "station", ux = !!(d.ux && d.ux.enabled);
  const exp = !!(d.experimental && d.experimental.sensors);
  row("ac", [t(a.enabled ? "ac_on" : "ac_off"), a.state === "armed" ? t("ac_armed") : "",
    a.state === "restoring" ? t("ac_restoring") : ""], a.enabled, st);
  row("ux", [t(ux ? "ux_on" : "ux_off")], ux, st);
  row("exp", [t(exp ? "exp_on" : "exp_off")], exp, st);
}

/** POST {<key, default "enabled">: <the opposite>} to a toggle endpoint.
 * @param {string} path @param {string} id the row's id prefix @param {string} err @param {string} [key] @param {string} [msgId] default id + "-msg" */
async function toggle(path, id, err, key, msgId) {
  const b = /** @type {HTMLButtonElement} */ ($(id + "-toggle")), msg = $(msgId || id + "-msg");
  msg.textContent = "";
  b.disabled = true;   /* one POST per tap: the next poll enables it again */
  let ok = false;
  try {
    ok = (/** @type {{body: PostResult}} */ (await fetchJson(path, {method: "POST",
      headers: {"Content-Type": "application/json"}, body: JSON.stringify({[key || "enabled"]: b.getAttribute("aria-checked") !== "true"})}))).body.ok;
  } catch (e) { /* ok stays false */ }
  if (!ok) msg.textContent = t(err);
}

/* External sensors (R_FW_SENSORS): shown while the flag is on; its own 10 s poll (a GET kicks a window) */
/** type -> its name's key @type {Record<string, string>} */
const SENS_T = {govee: "sens_t_govee", victron_mppt: "sens_t_victron_mppt", victron_battery: "sens_t_victron_battery"};
/** @type {Record<string, string>} */
const SENS_ERR = {bad_alias: "sens_err_bad_alias", bad_key: "sens_err_bad_key", key_mismatch: "sens_err_key_mismatch", full: "sens_err_full"};
/* a field name -> its unit, in display order (the rest has none and is not shown here) */
const SENS_U = [[/_c$/, "°C"], [/humidity/, "%"], [/voltage/, "V"], [/power/, "W"], [/kwh/, "kWh"], [/current/, "A"]];
let sensOn = false, sensBusy = false;
/** @param {Device} d */
function renderSensors(d) {
  $("sens").hidden = !(d.experimental && d.experimental.sensors);
  if (!$("sens").hidden && !sensOn) { sensOn = true; pollSensors(); }
}
async function pollSensors() {
  if (sensOn = !$("sens").hidden) {
    try { renderSens((/** @type {{body: Sensors}} */ (await fetchJson("/api/sensors"))).body); } catch (e) { /* next poll */ }
    setTimeout(pollSensors, 10000);
  }
}
/** Up to 3 readings with their units ("16.5 °C"). @param {Record<string, number|null>|null} v @returns {string[]} */
function sensVals(v) {
  /** @type {[number, string][]} */
  const out = [];
  for (const [k, x] of Object.entries(v || {})) {
    const i = SENS_U.findIndex(([re]) => /** @type {RegExp} */ (re).test(k));
    if (i >= 0 && x !== null) out.push([i, x + "\u00a0" + SENS_U[i][1]]);
  }
  return out.sort((a, b) => a[0] - b[0]).slice(0, 3).map((e) => e[1]);
}
/** A name field. @param {string} value @param {string} ph @returns {HTMLInputElement} */
function sensName(value, ph) {
  const n = el("input");
  n.value = value;
  n.placeholder = ph;
  n.autocapitalize = "none";
  n.spellcheck = false;
  n.setAttribute("aria-label", t("sens_alias"));
  return n;
}
/** The name typed, lowercased; null (and the error shown) when invalid or missing but needed.
 * @param {HTMLInputElement} n @param {boolean} need @returns {string|null} */
function sensAlias(n, need) {
  const a = n.value = n.value.trim().toLowerCase();
  if ((a || need) && !/^[a-z0-9_-]{1,16}$/.test(a)) { sensErr("sens_err_name"); return null; }
  return a;
}
/** Show an error where the owner looks (announced: aria-live). @param {string} key */
function sensErr(key) {
  const m = $("sens-msg");
  m.textContent = t(key);
  m.scrollIntoView({block: "nearest"});
}
/** Swap a row's buttons for an inline step (a question or fields) + OK and Cancel; Cancel puts them back
 * (and clears msgId). The Sensors poll leaves its box alone while one is open (.open). Focus: the first
 * field, else Cancel.
 * @param {HTMLElement} acts @param {Node[]} nodes @param {string} okKey @param {string} okCls
 * @param {(close: () => void) => Promise<void>|void} ok OK is disabled while it runs (a double tap posts once)
 * @param {string} [msgId] default "sens-msg" */
function ask(acts, nodes, okKey, okCls, ok, msgId) {
  const old = Array.from(acts.childNodes);
  acts.className = "acts open";
  acts.replaceChildren(...nodes);
  const b = acts.appendChild(el("button", t(okKey), okCls)), c = acts.appendChild(el("button", t("sens_cancel"), "secondary"));
  const close = () => {
    acts.className = "acts";
    acts.replaceChildren(...old);
    $(msgId || "sens-msg").textContent = "";
  };
  b.onclick = async () => { b.disabled = true; await ok(close); b.disabled = false; };
  c.onclick = close;
  (nodes[0] instanceof HTMLInputElement ? nodes[0] : c).focus();
}
/** @param {Sensors} s */
function renderSens(s) {
  const cfg = $("sens-cfg"), seen = $("sens-seen");
  if ($("sens").querySelector(".open")) return;   /* a confirmation or a name/key being typed */
  cfg.textContent = seen.textContent = "";
  for (const c of s.configured) {
    const vals = c.value ? sensVals(c.value) : ["—"];
    const p = cfg.appendChild(el("p", [t(SENS_T[c.type] || c.type), c.alias, ...vals,
      c.last_seen_s === null ? "—" : c.last_seen_s + " s"].join(" · "), "row"));
    const acts = p.appendChild(el("span", undefined, "acts"));
    acts.appendChild(el("button", t("sens_rename"), "secondary")).onclick = () => {
      const n = sensName(c.alias, "");
      ask(acts, [n], "sens_save", "", () => { const a = sensAlias(n, true); if (a) return sensPost({id: c.id, alias: a}); });
    };
    acts.appendChild(el("button", t("sens_remove"), "secondary")).onclick = () =>
      ask(acts, [el("span", t("sens_confirm_remove", {alias: c.alias}))], "sens_remove", "danger", () => sensPost({remove: c.id}));
  }
  /* an empty scan under configured sensors means no OTHER ones nearby (live check 2026-10-15) */
  if (s.scan === "paused" || !s.seen.length)
    seen.appendChild(el("p", t(s.scan === "paused" ? "sens_paused" : s.configured.length ? "sens_none_more" : "sens_none"), "muted"));
  for (const r of s.seen) {
    const p = seen.appendChild(el("p", [t(SENS_T[r.type] || r.type), r.model, r.rssi + " dBm"].join(" · "), "row"));
    const acts = p.appendChild(el("span", undefined, "acts"));
    /* a name (optional for the first of a type: its default alias), a key type's key; then Add posts */
    acts.appendChild(el("button", t("sens_add"))).onclick = () => {
      const need = s.configured.some((c) => c.type === r.type);
      const n = sensName("", need ? t("sens_alias") : r.type), nodes = [n];
      const k = r.type.startsWith("victron") ? el("input") : null;
      if (k) { k.autocomplete = "off"; k.spellcheck = false; k.placeholder = t("sens_key"); k.setAttribute("aria-label", t("sens_key")); nodes.push(k); }
      ask(acts, nodes, "sens_add", "", () => {
        const a = sensAlias(n, need);
        if (a === null) return;
        /** @type {Record<string, string|number>} */
        const o = {add: r.id};
        if (a) o.alias = a;
        if (k) o.key = k.value.trim();
        return sensPost(o);
      });
    };
  }
}
/** @param {Record<string, string|number>} o */
async function sensPost(o) {
  if (sensBusy) return;   /* one POST at a time */
  sensBusy = true;
  $("sens-msg").textContent = "";
  try {
    const r = /** @type {{status: number, body: Sensors}} */ (await fetchJson("/api/sensors", {method: "POST",
      headers: {"Content-Type": "application/json"}, body: JSON.stringify(o)}));
    if (r.status !== 200) return sensErr(SENS_ERR[r.body.error || ""] || "sens_error");
    $("sens-cfg").textContent = $("sens-seen").textContent = "";   /* closes the open step */
    renderSens(r.body);
  } catch (e) { sensErr("sens_error"); } finally { sensBusy = false; }
}

/* Screen (R_FW_DISPLAY_WAKE) */
/** @type {Record<string, string>} */
const SCR = {on: "scr_on", dim: "scr_dim", off: "scr_off"};
/* its on/off rows: [id suffix, device.display member = the POST key, label key] */
const SCR_ROWS = [["auto", "auto", "scr_auto"], ["prox", "prox_wake", "scr_prox"], ["motion", "motion_wake", "scr_motion"]];
/** @param {Device} d */
function renderScreen(d) {
  const p = d.display, st = d.wifi.mode === "station";
  $("scr").hidden = p.state === null;
  if (p.state === null) return;
  $("scr-status").textContent = [t(SCR[p.state]), p.brightness_pct + " % (" + p.min + "–" + p.max + ")",
    p.lux === null ? "" : p.lux + " lx", p.sensor === "ok" ? "" : t("scr_nolux"), st ? "" : t("station_only")].filter(Boolean).join(" · ");
  for (const [id, k, l] of SCR_ROWS) {
    const on = !!(/** @type {Record<string, unknown>} */ (p))[k];
    row("scr-" + id, [t(l) + ": " + t(on ? "scr_on" : "scr_off")], on, st);
  }
  scrOff = p.offset;
  $("scr-off").textContent = t("scr_offset", {n: (p.offset > 0 ? "+" : "") + p.offset});
  $("scr-dn").hidden = $("scr-up").hidden = !st;
}

let scrOff = 0;
/** @param {object} o */
const scrPost = (o) => fetchJson("/api/display", {method: "POST", headers: {"Content-Type": "application/json"},
  body: JSON.stringify(o)}).catch(() => null);
/** @param {number} step */
const scrStep = (step) => scrPost({offset: scrOff = Math.max(-50, Math.min(50, scrOff + step))});

/** @param {"check"|"install"} action */
async function ota(action) {
  const msg = $("fw-msg");
  msg.textContent = "";
  /* no second click while this one is in flight: the next poll shows the buttons again (busy -> not) */
  $("fw-check").hidden = $("fw-install").hidden = true;
  try {
    const r = /** @type {{body: PostResult}} */ (await fetchJson("/api/ota", {method: "POST",
      headers: {"Content-Type": "application/json"}, body: JSON.stringify({action: action})}));
    if (!r.body.ok) msg.textContent = t("ota_error", {error: r.body.error || ""});
  } catch (e) { msg.textContent = t("err_net"); }
}

/** @param {string} ch */
async function otaChannel(ch) {
  const msg = $("fw-msg");
  msg.textContent = "";
  try {
    const r = /** @type {{body: PostResult}} */ (await fetchJson("/api/ota", {method: "POST",
      headers: {"Content-Type": "application/json"}, body: JSON.stringify({channel: ch})}));
    if (r.body.ok) return;
    msg.textContent = t("fw_ch_error");
  } catch (e) { msg.textContent = t("err_net"); }
  select("fw-channel").value = $("fw-channel").dataset.v || "";
}

/** The scan list + "Other network…" (value "": the typed name; the only choice while the list is empty,
 * e.g. before the first scan landed). @param {Ap[]} scan */
function renderNetworks(scan) {
  const sel = select("ssid"), keep = sel.value, had = sel.options.length;
  sel.textContent = "";
  for (const ap of scan) {
    const o = el("option", ap.ssid + "  (" + ap.rssi + " dBm" + (ap.secure ? "" : ", " + t("open_unsupported")) + ")");
    o.value = ap.ssid;
    o.disabled = !ap.secure;  /* the device requires a NET_PSK_MIN..NET_PSK_MAX passphrase */
    sel.appendChild(o);
  }
  sel.appendChild(el("option", t("other_network"))).value = "";
  if (scan.some((ap) => ap.ssid === keep) || (had && !keep)) sel.value = keep;   /* else the first network */
  $("scan-empty").hidden = scan.length > 0;
  other();
}
/* the name field shows while "Other network…" is chosen */
const other = () => { $("ssid-name").hidden = select("ssid").value !== ""; };

/** @type {ReturnType<typeof setTimeout>|null} the pending delayed re-read from the last scan() */
let scanTimer = null;

async function scan() {
  const msg = $("setup-msg");
  if (scanTimer !== null) { clearTimeout(scanTimer); scanTimer = null; }
  msg.className = "";
  msg.textContent = t("scanning");
  const still = () => msg.textContent === t("scanning");  /* nothing newer (join result, error) landed */
  try {
    renderNetworks((await getWifi()).scan);
    /* each GET starts a fresh scan for the next one: read its result a moment later */
    scanTimer = setTimeout(async () => {
      scanTimer = null;
      try { renderNetworks((await getWifi()).scan); if (still()) msg.textContent = ""; }
      catch (e) { if (still()) msg.textContent = t("err_net"); }
    }, 2 * CFG.pollMs);
  } catch (e) { if (still()) msg.textContent = t("err_net"); }
}

/* Connect: over the hotspot it posts at once; online it asks first (the satellite leaves its network,
 * and on a wrong password it comes back by itself: the honest warning), then shows the Switching panel BEFORE posting —
 * the phone loses the page once the satellite leaves, so the panel needs no server. */
function connect() {
  const ssid = select("ssid").value || input("ssid-name").value.trim(), psk = input("psk").value, msg = $("setup-msg");
  if (scanTimer !== null) { clearTimeout(scanTimer); scanTimer = null; }
  msg.className = "bad";
  if (!ssid || ssid.length > CFG.ssidMax) { msg.textContent = t("err_ssid", {max: CFG.ssidMax}); return; }
  if (psk.length < CFG.pskMin || psk.length > CFG.pskMax) {
    msg.textContent = t("err_psk", {min: CFG.pskMin, max: CFG.pskMax}); return;
  }
  msg.textContent = "";
  const w = wifi;
  if (!w || w.mode !== "station") return join(ssid, psk, true);
  const vars = {ssid: w.ssid || "", old: w.ssid || "", ap: CFG.ap, appsk: CFG.apPsk};
  ask($("wifi-acts"), [el("span", t("wifi_warn", vars))], "wifi_switch", "danger", (close) => {
    close();
    const p = $("wifi-switch"), url = "http://" + CFG.host + ".local", ap = "http://" + CFG.apAddr;
    /** @param {string} key @param {string} href */
    const line = (key, href) => { const e = el("p", t(key, Object.assign({}, vars, {ssid: ssid})) + " "); e.appendChild(link(href, href)); return e; };
    p.replaceChildren(el("h2", t("switch_title")), line(ssid === w.ssid ? "switch_same" : "switch_other", url), line("switch_lost", ap));
    p.hidden = false;
    $("wifi-now").textContent = "";   /* it is leaving that network */
    $("wifi-form").hidden = true;
    wifiOpen = false;
    return join(ssid, psk, false);
  }, "setup-msg");
}

/** POST /api/wifi and watch the join. @param {string} ssid @param {string} psk
 * @param {boolean} hotspot over the hotspot a lost answer is retried once; online it means the satellite already left */
async function join(ssid, psk, hotspot) {
  const msg = $("setup-msg");
  /* a hanging POST (answers lost, connection still open) ends after CFG.connectTimeoutMs: the retry path */
  const send = () => {
    const ac = new AbortController(), timer = setTimeout(() => ac.abort(), CFG.connectTimeoutMs);
    return fetch("/api/wifi", {cache: "no-store", method: "POST", signal: ac.signal,
      headers: {"Content-Type": "application/json"}, body: JSON.stringify({ssid: ssid, psk: psk})})
      .finally(() => clearTimeout(timer));
  };
  try {
    let res = null;
    try { res = await send(); }
    catch (e) {
      /* no answer: the phone can drop off the setup hotspot for a moment (the shared radio scans or
       * switches channel) — say so, wait, and try once more. Online the satellite already left. */
      if (hotspot) {
        msg.className = "";
        msg.textContent = t("retrying");
        await new Promise((done) => setTimeout(done, CFG.connectRetryMs));
        msg.className = "bad";
        res = await send();
      }
    }
    const r = {body: /** @type {PostResult} */ (res ? await res.json() : {ok: true})};
    if (!r.body.ok) {
      const e = POST_ERROR[r.body.error || ""] || "err_json";
      msg.textContent = t(e, {max: e === "err_ssid" ? CFG.ssidMax : CFG.pskMax, min: CFG.pskMin});
      $("wifi-switch").hidden = true;
      return;
    }
    input("psk").value = "";
    msg.className = "";
    msg.textContent = t("joining", {ssid: ssid});
    joining = {ssid: ssid, polls: 0, left: false, away: hotspot};
  } catch (e) { msg.textContent = t("err_net"); $("wifi-switch").hidden = true; }
}

/** @param {Join} j the join being watched */
async function watchJoin(j) {
  const msg = $("setup-msg");
  const w = await getWifi();
  j.polls++;
  if (w.mode !== "setup") j.left = true;
  if (w.mode !== "station") j.away = true;
  if (w.mode === "station" && w.ip && j.away && w.reverted) {   /* the new network failed: back on the old one */
    msg.className = "bad";
    msg.textContent = t("wifi_reverted", {why: t(WHY[w.last_error || ""] || "why_other"), ssid: w.ssid || ""});
    $("wifi-switch").hidden = true;
    joining = null;
  } else if (w.mode === "station" && w.ip && j.away) {   /* online: only once it left and came back */
    msg.className = "";
    msg.textContent = t("joined", {ip: w.ip, ssid: j.ssid}) + " ";
    const url = "http://" + CFG.host + ".local";
    msg.appendChild(link(url, url));
    $("wifi-switch").hidden = true;
    joining = null;
  } else if (w.mode === "setup" && (j.left || j.polls > 3)) {
    msg.className = "bad";
    /* why it failed (/api/wifi last_error): out of range, a typo, or anything else */
    msg.textContent = t(JOIN_FAILED[w.last_error || ""] || "join_failed_other");
    joining = null;
  }
}

/* the boxes stay hidden until the first answer (no empty join form or headers flash on load) */
const BOXES = ["setup", "pair", "ac", "fw", "adv-title", "ux", "exp"];
async function poll() {
  try {
    if (joining) await watchJoin(joining);
    const s = (/** @type {{body: State}} */ (await fetchJson("/api/state"))).body;
    $("banner").style.display = "none";
    if (!$("loading").hidden) { $("loading").hidden = true; for (const id of BOXES) $(id).hidden = false; }
    wifi = s.device.wifi;
    renderLink(s.device);
    renderWifi(wifi);
    renderFw(s.device);
    renderRows(s.device);
    renderSensors(s.device);
    renderScreen(s.device);
    renderTime(s.device.time);
    renderSd(s.device.sd);
    renderRes(s.device.res);
    renderBattery(s.device.battery);
  } catch (e) {
    if (joining) joining.away = true;
    $("banner").textContent = t("offline");
    $("banner").style.display = "block";
  }
  setTimeout(poll, CFG.pollMs);
}

$("connect").addEventListener("click", connect);
$("rescan").addEventListener("click", scan);
$("ssid").addEventListener("change", other);
$("psk-show").addEventListener("change", () => { input("psk").type = input("psk-show").checked ? "text" : "password"; });
$("wifi-change").addEventListener("click", () => {
  wifiOpen = true;
  if (wifi) renderWifi(wifi);
  select("ssid").focus();
});
$("ac-toggle").addEventListener("click", () => toggle("/api/auto_camper", "ac", "ac_error"));
$("ux-toggle").addEventListener("click", () => toggle("/api/ux_setting", "ux", "ux_error"));
$("exp-toggle").addEventListener("click", () => toggle("/api/experimental", "exp", "exp_error", "sensors"));
for (const [id, k] of SCR_ROWS) $("scr-" + id + "-toggle").addEventListener("click", () => toggle("/api/display", "scr-" + id, "scr_error", k, "scr-msg"));
$("scr-dn").addEventListener("click", () => scrStep(-5));
$("scr-up").addEventListener("click", () => scrStep(5));
$("fw-check").addEventListener("click", () => ota("check"));
$("fw-install").addEventListener("click", () => ota("install"));
$("fw-channel").addEventListener("change", () => otaChannel(select("fw-channel").value));
poll();
