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
 * @typedef {{ pairing: { address: string|null }, wifi: Wifi }} Device  the /api/state fields this page reads
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
