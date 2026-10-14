/* page.js — the ESP32 firmware's WiFi setup page script (#154): the provisioning form + a link to the
 * calictl UI, whose Device status screen shows the satellite's status (#267). SOURCE: tools/gen_c_dict.py inlines
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
 * @typedef {Wifi & { last_error: string|null, scan: Ap[] }} WifiGet
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
 * @typedef {{ ssid: string, polls: number, left: boolean }} Join
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

$("title").textContent = t("title");
$("setup-title").textContent = t("setup_title");
$("setup-hint").textContent = t("setup_hint");
$("ssid-label").textContent = t("network");
$("psk-label").textContent = t("password");
$("connect").textContent = t("connect");
$("rescan").textContent = t("rescan");
$("fw-title").textContent = t("fw_title");
$("ac-title").textContent = t("ac_title");
$("fw-check").textContent = t("fw_check");
$("fw-channel-l").textContent = t("fw_channel");
select("fw-channel").options[0].textContent = t("fw_ch_stable");
select("fw-channel").options[1].textContent = t("fw_ch_latest");
$("scr-title").textContent = t("scr_title");
$("exp-title").textContent = t("exp_title");
$("exp-hint").textContent = t("exp_hint");
$("scr-auto-l").textContent = t("scr_auto");
$("scr-prox-l").textContent = t("scr_prox");
$("scr-motion-l").textContent = t("scr_motion");
document.documentElement.lang = LANG;

/** @type {Join|null} a submitted join being watched */
let joining = null;

/** @param {Device} d */
function renderLink(d) {
  /* the calictl web UI: GET / in station mode; elsewhere (the setup hotspot) GET /app, where its
   * pairing wizard pairs the unit without home WiFi */
  const box = $("device"), station = d.wifi.mode === "station";
  box.textContent = "";
  const a = el("a", t(station || d.pairing.address ? "app_link" : "app_link_pair"));
  a.href = station ? "/" : "/app";
  box.appendChild(a);
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
  if (!station) text += " · " + t("fw_station_only");
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

/* Auto camper (R_FW_AUTO_CAMPER): on/off, a restore owed, the toggle (station mode only) */
/** @param {Device} d */
function renderAutoCamper(d) {
  const a = d.auto_camper, st = d.wifi.mode === "station", b = $("ac-toggle");
  $("ac-status").textContent = [t(a.enabled ? "ac_on" : "ac_off"), a.state === "armed" ? t("ac_armed") : "",
    a.state === "restoring" ? t("ac_restoring") : "", st ? "" : t("ac_station_only")].filter(Boolean).join(" · ");
  b.hidden = !st;
  b.textContent = t(a.enabled ? "ac_turn_off" : "ac_turn_on");
  b.dataset.on = a.enabled ? "1" : "";
}

/* "Send usage data" (R_UX_TELEMETRY): the web UI's usage beacons on/off (station mode only) */
/** @param {Device} d */
function renderUx(d) {
  const on = !!(d.ux && d.ux.enabled), b = $("ux-toggle");
  $("ux-status").textContent = t(on ? "ux_on" : "ux_off");
  b.hidden = d.wifi.mode !== "station";
  b.textContent = t(on ? "ac_turn_off" : "ac_turn_on");
  b.dataset.on = on ? "1" : "";
}

/* Experimental sensor track (R_FW_EXPERIMENTAL): on/off, station mode only */
/** @param {Device} d */
function renderExp(d) {
  const on = !!(d.experimental && d.experimental.sensors), b = $("exp-toggle");
  $("exp-status").textContent = t(on ? "exp_on" : "exp_off");
  b.hidden = d.wifi.mode !== "station";
  b.textContent = t(on ? "ac_turn_off" : "ac_turn_on");
  b.dataset.on = on ? "1" : "";
}

/** POST {<key, default "enabled">: <the opposite>} to a toggle endpoint. @param {string} path @param {string} id the row's id prefix @param {string} err @param {string} [key] */
async function toggle(path, id, err, key) {
  const b = $(id + "-toggle"), msg = $(id + "-msg");
  msg.textContent = "";
  b.hidden = true;   /* the next poll shows it again */
  let ok = false;
  try {
    ok = (/** @type {{body: PostResult}} */ (await fetchJson(path, {method: "POST",
      headers: {"Content-Type": "application/json"}, body: JSON.stringify({[key || "enabled"]: !b.dataset.on})}))).body.ok;
  } catch (e) { /* ok stays false */ }
  if (!ok) msg.textContent = t(err);
}

/* Screen (R_FW_DISPLAY_WAKE) */
/** @type {Record<string, string>} */
const SCR = {on: "scr_on", dim: "scr_dim", off: "scr_off"};
/** @param {Device} d */
function renderScreen(d) {
  const p = d.display;
  $("scr").hidden = p.state === null;
  if (p.state === null) return;
  $("scr-status").textContent = [t(SCR[p.state]), p.brightness_pct + " % (" + p.min + "–" + p.max + ")",
    p.lux === null ? "" : p.lux + " lx", p.sensor === "ok" ? "" : t("scr_nolux")].filter(Boolean).join(" · ");
  input("scr-auto").checked = p.auto;
  input("scr-prox").checked = p.prox_wake;
  input("scr-motion").checked = p.motion_wake;
  scrOff = p.offset;
  $("scr-off").textContent = t("scr_offset", {n: (p.offset > 0 ? "+" : "") + p.offset});
  for (const id of ["scr-auto", "scr-prox", "scr-motion", "scr-dn", "scr-up"]) input(id).disabled = d.wifi.mode !== "station";
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

/** @param {Ap[]} scan */
function renderNetworks(scan) {
  const sel = select("ssid"), keep = sel.value;
  sel.textContent = "";
  if (!scan.length) { sel.appendChild(el("option", t("no_networks"))).value = ""; return; }
  for (const ap of scan) {
    const o = el("option", ap.ssid + "  (" + ap.rssi + " dBm" + (ap.secure ? "" : ", " + t("open_unsupported")) + ")");
    o.value = ap.ssid;
    o.disabled = !ap.secure;  /* the device requires a NET_PSK_MIN..NET_PSK_MAX passphrase */
    sel.appendChild(o);
  }
  if (scan.some((ap) => ap.ssid === keep)) sel.value = keep;
}

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

async function connect() {
  const ssid = select("ssid").value, psk = input("psk").value, msg = $("setup-msg");
  if (scanTimer !== null) { clearTimeout(scanTimer); scanTimer = null; }
  msg.className = "bad";
  if (!ssid || ssid.length > CFG.ssidMax) { msg.textContent = t("err_ssid", {max: CFG.ssidMax}); return; }
  if (psk.length < CFG.pskMin || psk.length > CFG.pskMax) {
    msg.textContent = t("err_psk", {min: CFG.pskMin, max: CFG.pskMax}); return;
  }
  /* a hanging POST (answers lost, connection still open) ends after CFG.connectTimeoutMs: the retry path */
  const send = () => {
    const ac = new AbortController(), timer = setTimeout(() => ac.abort(), CFG.connectTimeoutMs);
    return fetch("/api/wifi", {cache: "no-store", method: "POST", signal: ac.signal,
      headers: {"Content-Type": "application/json"}, body: JSON.stringify({ssid: ssid, psk: psk})})
      .finally(() => clearTimeout(timer));
  };
  try {
    let res;
    try { res = await send(); }
    catch (e) {
      /* no answer: the phone can drop off the setup hotspot for a moment (the shared radio scans or
       * switches channel) — say so, wait, and try once more */
      msg.className = "";
      msg.textContent = t("retrying");
      await new Promise((done) => setTimeout(done, CFG.connectRetryMs));
      msg.className = "bad";
      res = await send();
    }
    const r = {body: /** @type {PostResult} */ (await res.json())};
    if (!r.body.ok) {
      const e = POST_ERROR[r.body.error || ""] || "err_json";
      msg.textContent = t(e, {max: e === "err_ssid" ? CFG.ssidMax : CFG.pskMax, min: CFG.pskMin});
      return;
    }
    input("psk").value = "";
    msg.className = "";
    msg.textContent = t("joining", {ssid: ssid});
    joining = {ssid: ssid, polls: 0, left: false};
  } catch (e) { msg.textContent = t("err_net"); }
}

/** @param {Join} j the join being watched */
async function watchJoin(j) {
  const msg = $("setup-msg");
  const w = await getWifi();
  j.polls++;
  if (w.mode !== "setup") j.left = true;
  if (w.mode === "station" && w.ip) {
    msg.className = "";
    msg.textContent = t("joined", {ip: w.ip, ssid: j.ssid}) + " ";
    const url = "http://" + CFG.host + ".local";
    const a = el("a", url);
    a.href = url;
    msg.appendChild(a);
    joining = null;
  } else if (w.mode === "setup" && (j.left || j.polls > 3)) {
    msg.className = "bad";
    /* why it failed (/api/wifi last_error): out of range, a typo, or anything else */
    msg.textContent = t(JOIN_FAILED[w.last_error || ""] || "join_failed_other");
    joining = null;
  }
}

let setupShown = false;
async function poll() {
  try {
    if (joining) await watchJoin(joining);
    const s = (/** @type {{body: State}} */ (await fetchJson("/api/state"))).body;
    $("banner").style.display = "none";
    renderLink(s.device);
    renderFw(s.device);
    renderAutoCamper(s.device);
    renderUx(s.device);
    renderExp(s.device);
    renderScreen(s.device);
    renderTime(s.device.time);
    renderSd(s.device.sd);
    renderRes(s.device.res);
    renderBattery(s.device.battery);
    const setup = s.device.wifi.mode === "setup" || joining !== null || $("setup-msg").querySelector("a") !== null;
    $("setup").hidden = !setup;
    if (setup && !setupShown && !joining) scan();
    setupShown = setup;
  } catch (e) {
    $("banner").textContent = t("offline");
    $("banner").style.display = "block";
  }
  setTimeout(poll, CFG.pollMs);
}

$("connect").addEventListener("click", connect);
$("rescan").addEventListener("click", scan);
$("ac-toggle").addEventListener("click", () => toggle("/api/auto_camper", "ac", "ac_error"));
$("ux-toggle").addEventListener("click", () => toggle("/api/ux_setting", "ux", "ux_error"));
$("exp-toggle").addEventListener("click", () => toggle("/api/experimental", "exp", "exp_error", "sensors"));
for (const [id, k] of [["scr-auto", "auto"], ["scr-prox", "prox_wake"], ["scr-motion", "motion_wake"]]) $(id).addEventListener("change", () => scrPost({[k]: input(id).checked}));
$("scr-dn").addEventListener("click", () => scrStep(-5));
$("scr-up").addEventListener("click", () => scrStep(5));
$("fw-check").addEventListener("click", () => ota("check"));
$("fw-install").addEventListener("click", () => ota("install"));
$("fw-channel").addEventListener("change", () => otaChannel(select("fw-channel").value));
poll();
