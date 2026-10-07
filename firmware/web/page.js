/* page.js — the ESP32 firmware's status/setup page script (#154). SOURCE: tools/gen_c_dict.py inlines
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
 * @typedef {{ pairing: { state: string, address: string|null },
 *             link: { up: boolean, last_snap_age_ms: number|null },
 *             wifi: Wifi, control: { writes: boolean }, uptime_ms: number, fw: string }} Device
 * @typedef {{ t: number, fn: Record<string, Record<string, unknown>>, device: Device }} State
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
/* the unit's own function titles (calictl web UI vocabulary); other functions show their raw name */
/** @type {Record<string, string>} */
const FN = {vehicle: "fn_vehicle", cooler: "fn_cooler", campingmode: "fn_campingmode", lighting: "fn_lighting",
            airheater: "fn_airheater", water: "fn_water", energy: "fn_energy", roof: "fn_roof"};
/** @type {Record<string, string>} */
const MODE = {setup: "mode_setup", station: "mode_station", off: "mode_off"};
/* /api/wifi last_error -> the text that says why a join failed */
/** @type {Record<string, string>} */
const JOIN_FAILED = {not_found: "join_failed_not_found", auth: "join_failed_auth", other: "join_failed_other"};
/** @type {Record<string, string>} */
const POST_ERROR = {ssid: "err_ssid", psk: "err_psk", store: "err_store"};

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
/** @param {[string, string][]} rows @returns {HTMLDListElement} */
function list(rows) {
  const dl = el("dl");
  for (const [k, v] of rows) { dl.appendChild(el("dt", k)); dl.appendChild(el("dd", v)); }
  return dl;
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
$("functions-title").textContent = t("functions");
document.documentElement.lang = LANG;

/** @type {Join|null} a submitted join being watched */
let joining = null;

/* uptime like the device's own screen (display_model.c): "45 s", "N min", "H h M min", "D d H h" */
/** @param {number} ms @returns {string} */
function fmtUptime(ms) {
  const s = Math.floor(ms / 1000), min = Math.floor(s / 60), h = Math.floor(min / 60), d = Math.floor(h / 24);
  if (!min) return s + " s";
  if (!h) return min + " min";
  return d ? d + " d " + (h % 24) + " h" : h + " h " + (min % 60) + " min";
}

/** @param {Device} d */
function renderDevice(d) {
  const box = $("device");
  box.textContent = "";
  box.appendChild(el("h2", t("device")));
  const w = d.wifi, age = d.link.last_snap_age_ms;
  box.appendChild(list([
    /* a bonded address with a live link is paired: "idle" (reconnected by the stored bond, the
     * pairing flow never ran) or "bonded" (fresh from a pair) would show a raw, untranslated word */
    [t("pairing"), d.pairing.address && d.link.up && (d.pairing.state === "idle" || d.pairing.state === "bonded")
      ? t("paired") : d.pairing.state],
    [t("address"), d.pairing.address || t("none")],
    [t("link"), t(d.link.up ? "link_up" : "link_down")],
    [t("last_update"), age === null ? t("none") : t("seconds_ago", {n: Math.round(age / 1000)})],
    [t("wifi"), t(MODE[w.mode] || "mode_off") + (w.ssid ? " · " + w.ssid : "")],
    [t("ip"), w.ip || t("none")],
    [t("signal"), w.rssi === null ? t("none") : w.rssi + " dBm"],
    [t("uptime"), fmtUptime(d.uptime_ms)],
    [t("firmware"), d.fw],
  ]));
  if (w.mode === "station") {   // GET / is the calictl web UI in station mode
    const a = el("a", t("app_link"));
    a.href = "/";
    box.appendChild(a);
  }
}

/** @param {State["fn"]} fn */
function renderFunctions(fn) {
  const box = $("functions");
  box.textContent = "";
  const names = Object.keys(fn);
  if (!names.length) { box.appendChild(el("p", t("no_data"), "muted")); return; }
  for (const name of names) {
    const card = el("div", undefined, "box");
    card.appendChild(el("h2", FN[name] ? t(FN[name]) : name));
    card.appendChild(list(Object.keys(fn[name]).map((k) => /** @type {[string, string]} */ ([k, String(fn[name][k])]))));
    box.appendChild(card);
  }
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
    renderDevice(s.device);
    renderFunctions(s.fn);
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
poll();
