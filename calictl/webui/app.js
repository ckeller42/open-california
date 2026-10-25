// @ts-check
// OpenCalifornia web UI — curated camper Vehicle-tab controls, driven by /api/state.
//
// Rather than mechanically render the reverse-engineered screen spec, each feature is a
// small hand-authored spec: the actual controls (mapped to the /api/command `what` tokens)
// plus the data readouts pulled from the interpreted /api/state. Labels are English source
// strings; German comes from strings.de.js via t(). Actuation is slow (BLE), so every command
// shows a spinner on the tapped control and a result toast; idle polls don't re-render (no flicker).
// ---------------------------------------------------------------------------------------------
// Type model (editor-only; JSDoc `@typedef`s — no runtime effect). These mirror the REAL shapes
// produced by calictl/semantics.py (per-function interpret) + serve.py::state() (`_meta`) and the
// /api/command contract in serve.py::command() + web.py. Field types match the Python: a key that
// is `d.get(...)`-nullable in semantics is typed `<T> | null`. `FnState` is one broad union of
// every function's interpreted leaves (all optional) so the FEATURES lambdas that read `s.<leaf>`
// are type-checked against the actual key names — a renamed/typo'd state key surfaces as an error.
//
/**
 * A water tank, from `semantics.water`'s inner `tank()` (fresh / waste). All three numbers are
 * `None`-tolerant in Python. `stale` is stamped on by `serve.state()` when the held reading is old.
 * @typedef {{ liters: number|null, capacity_l: number|null, percent: number|null, stale?: boolean }} Tank
 */
/**
 * The satellite's SD log card (firmware device.sd, R_FW_SD_LOG), its own resources (device.res) and
 * its own battery (device.battery, R_FW_BATTERY; each member null when unknown).
 * @typedef {{state: string, total_mb: number|null, free_mb: number|null, last_seq: number|null, dropped: number}} SatSd
 * @typedef {{heap_free_kb: number|null, heap_min_kb: number|null, psram_free_kb: number|null, cpu_pct: number|null}} SatRes
 * @typedef {{pct: number|null, mv: number|null, usb: boolean|null, charging: boolean|null, runtime_min: number|null}} SatBattery
 * @typedef {{source: string, synced: boolean, boot: number}} SatTime
 * @typedef {{state: string, available_sha: string|null, progress_pct: number, error: string|null, channel: string}} SatOta
 * @typedef {{state: string|null, lux: number|null, brightness_pct: number|null, sensor: string|null, auto: boolean, prox_wake: boolean, motion_wake: boolean, min: number, max: number, offset: number}} SatDisplay
 * @typedef {{mode?: string, ssid?: string|null, ip?: string|null, rssi?: number|null}} SatWifi
 * @typedef {SatWifi & {last_error?: string|null, reverted?: boolean, scan?: {ssid: string, rssi: number, secure: boolean}[]}} SatWifiGet  GET /api/wifi
 * @typedef {{id: number, type: string, alias: string, last_seen_s: number|null, value: Record<string, number|null>|null}} SatSensCfg
 * @typedef {{id: number, type: string, model: string, rssi: number}} SatSensSeen
 * @typedef {{configured: SatSensCfg[], seen: SatSensSeen[], scan: string, error?: string}} SatSensors  GET/POST /api/sensors
 * @typedef {{enabled: boolean, last_s: number|null, reason: string|null, halt_after_min?: number, halt_soc_pct?: number,
 *   halt_in_s?: number|null, halted?: boolean, halt_last_s?: number|null, halt_reason?: string|null, batt_low?: boolean}} SatPiWake
 *   device.pi_wake (R_FW_PI_WAKE; the halt_* members R_FW_PI_HALT)
 */
/**
 * `serve.state()::_meta`. `session`/`session_mode` are `"off"` unless the persistent session is on.
 * @typedef {Object} Meta
 * @property {number|null} last_seen        epoch seconds of the last successful poll (null = none yet)
 * @property {number|null} age_s            seconds since last_seen (null = none yet)
 * @property {boolean} online               van currently reachable
 * @property {boolean} [paired]             false when no unit is bonded yet (Task 5's `_meta.paired`)
 * @property {boolean} read_only            daemon rejects control writes
 * @property {'off'|'connecting'|'up'|'degraded'|'asleep'|'satellite'} session   persistent BLE session state ('satellite' = serve --no-ble)
 * @property {'off'|'auto'|'release'} session_mode                    activity-scoped vs user-disconnected
 * @property {{enabled:boolean, armed:boolean, notice:?{ts:number,msg:string}}} [auto_camper] auto-camper toggle + notice
 * @property {Record<string, any>} [firmware]   firmware baseline/drift snapshot (calictl.firmware)
 * @property {Record<string, any>} [anchors]    plausibility-check results (calictl.anchors)
 * @property {boolean} [restored]           set only by restoreLastState(): the saved offline snapshot shown until a live poll answers
 * @property {boolean} [ux]                 send usage beacons (POST /api/ux, R_UX_TELEMETRY); calictl: CALICTL_UX, satellite: device.ux.enabled
 * @property {boolean} [satellite]          set only by semantics.js adaptSatellite(): the ESP32 satellite (raw /api/state; controls live when the firmware reports device.control.writes, except the roof)
 * @property {{fw: string|null, uptime_ms: number|null, wifi: ?SatWifi, link_up: boolean, link_ready: boolean, link_rest?: boolean, rest_next_s?: number|null, link_redial?: boolean, sd?: ?SatSd, res?: ?SatRes, battery?: ?SatBattery, time?: ?SatTime, ota?: ?SatOta, display?: ?SatDisplay, auto_camper?: ?{enabled: boolean, state: string}, pi_wake?: ?SatPiWake, ble?: ?{mode: string, idle_min?: number, rest_poll_min?: number}, experimental?: boolean}} [sat]  satellite-only device rows (adaptSatellite passthrough) for Device status + Satellite settings
 */
/**
 * The broad union of every function's interpreted leaves (semantics.py). A given `STATE[fn]` only
 * carries its own function's keys, hence every field is optional. Grouped by source interpreter.
 * @typedef {Object} FnState
 * @property {boolean} [installed]
 * // cooler (semantics.cooler)
 * @property {boolean} [on]
 * @property {number|null} [level]
 * @property {number|null} [mode]
 * @property {boolean} [timer_active]
 * @property {number|null} [quiet_from]
 * @property {number|null} [quiet_to]
 * @property {boolean} [quiet_scheduled]
 * @property {number|null} [timer_hour]
 * @property {number|null} [timer_min]
 * @property {boolean} [timer_armed]
 * @property {string|null} [fault]
 * @property {boolean} [door_open]
 * @property {boolean|string|null} [error]   // cooler: any-fault boolean; airheater: named ErrorCode
 * // campingmode (semantics.campingmode)
 * @property {boolean} [master_on]
 * @property {boolean} [usb_charger]
 * @property {boolean} [usb_powered]
 * @property {boolean} [lights_on]
 * @property {boolean} [outputs_controllable]
 * @property {boolean} [enable]
 * // lighting (semantics.lighting): brightness_zone_1..16 read dynamically -> see note at renderLighting
 * @property {number|null} [profile]
 * @property {boolean} [any_on]
 * @property {{time: string, enabled: boolean, ramp: number, brightness: number, areas: number[], colour: number}|null} [wakeup]
 * @property {boolean|null} [door_contact]
 * @property {number[]|null} [favourites_stored]
 * // airheater (semantics.airheater)
 * @property {boolean} [running]
 * @property {boolean} [permanent]
 * @property {boolean} [normal]   // NormalOperation alone: the "Immediate heating" switch
 * @property {number|null} [error_code]   // raw ErrorCode; `error` (above) carries its NAME here
 * @property {number|null} [air_distribution]
 * @property {number|null} [running_time]
 * @property {number|null} [running_time_remaining]
 * // water (semantics.water) + serve.state() stale-hold
 * @property {Tank} [fresh]
 * @property {Tank} [waste]
 * @property {string|null} [fresh_alert]
 * @property {string|null} [waste_alert]
 * @property {number|null} [stale_since]
 * @property {number} [measured_at]   // epoch s of the HELD reading's measurement (absent = unknown)
 * // energy (semantics.energy)
 * @property {boolean} [stale]
 * @property {number|null} [age_min]
 * @property {number|null} [batt1_v]
 * @property {number|null} [batt1_current]
 * @property {number|null} [soc1_level]
 * @property {number|null} [soc1_pct]
 * @property {number|null} [batt2_v]
 * @property {number|null} [batt2_current]
 * @property {number|null} [soc2_level]
 * @property {number|null} [soc2_pct]
 * @property {number|null} [batt2_remaining_h]
 * @property {number|null} [batt2_remaining_min]
 * @property {boolean} [dcdc_charging]
 * @property {string|null} [dcdc_state]
 * @property {string|null} [shore_state]
 * @property {string|null} [solar_state]
 * @property {number} [dcdc_power]
 * @property {number} [shore_power]
 * @property {number} [solar_power]
 * @property {number|null} [dcdc_current]
 * @property {number|null} [shore_current]
 * @property {number|null} [solar_current]
 * @property {boolean} [dcdc_installed]
 * @property {boolean} [shore_installed]
 * @property {boolean} [solar_installed]
 * @property {number|null} [energy_mode]
 * @property {boolean} [energy_mode_locked]
 * @property {number|null} [warning_level]
 * @property {boolean} [warning_active]
 * @property {boolean} [derating_temp_active]
 * @property {boolean} [sleep_warning]
 * @property {string[]} [faults]
 * // roof (semantics.roof)
 * @property {number|null} [position]
 * @property {string|null} [position_name]
 * @property {string|null} [alert]
 * @property {boolean} [safety_valid]
 * // vehicle (semantics.vehicle)
 * @property {boolean} [ignition_on]
 * @property {number|null} [car_variant]
 * @property {string|null} [car_model]
 * @property {number|null} [level_popup]
 * @property {number|null} [level_roll]
 * @property {number|null} [level_pitch]
 * @property {string|null} [car_clock]
 */
/**
 * Full `/api/state` payload: one `FnState` per function, plus the non-function `_meta` block.
 * @typedef {{ [fn: string]: FnState } & { _meta?: Meta, ext?: Record<string, SensorReading> }} State
 */
/**
 * One external BLE sensor's last reading, the ESP's `/api/state` `ext.<alias>` (R_FW_SENSORS): its
 * `type` (govee | victron_mppt | victron_battery | a future one), its fields (`null` = not read
 * yet) and the reading's age in s (`null` before the first one).
 * @typedef {Record<string, number|string|null>} SensorReading
 */
/**
 * A queued/in-flight control command (`command()` -> POST /api/command). `value` is the token or
 * numeric level; `key` = `qKey(fn, what)`.
 * @typedef {{ fn: string, what: string, value: string|number|null, key: string }} CommandItem
 */
/**
 * `/api/command` success response (serve.py::command()). Error paths (web.py) return `{ error }`
 * only, so every field is optional here. `applied`: true=verified, false=not applied, null=sent
 * but not remotely verifiable.
 * @typedef {{ ok?: boolean, applied?: boolean|null, refused?: string, state?: FnState|null, error?: string|null, function?: string, unconfirmed?: boolean }} CommandResponse
 */
//
// --- FEATURES config shapes -------------------------------------------------------------------
/**
 * A control's per-control confirm gate: a function returning the prompt (or null/false to skip),
 * or a bare string. The callback arg varies by kind (value for most, the action object for buttons).
 * @typedef {((v?: any, s?: FnState) => (string|null|false|undefined)) | string} ConfirmSpec
 * @typedef {{ value: string, label: string }} SelectOption
 * @typedef {{ what: string, label: string, value?: string|number|null }} ButtonAction
 * `disabled(s)` returns true/a-reason-string when the current state forbids the control
 * (greyed + blocked, mirroring the app), a falsy value when it's available.
 * @typedef {{ what: string, label: string, confirm?: ConfirmSpec, disabled?: (s: FnState) => (boolean|string|undefined) }} ControlBase
 * @typedef {ControlBase & { kind: 'toggle', state: keyof FnState }} ToggleControl
 * @typedef {ControlBase & { kind: 'slider', state: keyof FnState, min: number, max: number, step?: number, unit?: string }} SliderControl
 * @typedef {ControlBase & { kind: 'select', options: SelectOption[], current?: (s: FnState) => (string|null|undefined) }} SelectControl
 * @typedef {ControlBase & { kind: 'hour', current?: (s: FnState) => number }} HourControl
 * @typedef {ControlBase & { kind: 'time', current?: (s: FnState) => (string|null) }} TimeControl
 * @typedef {ControlBase & { kind: 'buttons', actions: ButtonAction[] }} ButtonsControl
 * @typedef {ToggleControl|SliderControl|SelectControl|HourControl|TimeControl|ButtonsControl} Control
 */
/**
 * A live readout row. `get` formats the value; `bar` (0-100) draws a fill; `widget` returns a
 * richer node (the leveling bubble); `tick` live-advances the value (vehicle clock); `show` false
 * drops the row (equipment the unit reports as not installed: no "not installed" rows).
 * @typedef {{ label: string, get: (s: FnState) => (string|number|null|undefined), bar?: (s: FnState) => (number|null|undefined), widget?: (s: FnState) => (Node|null), tick?: boolean, show?: (s: FnState) => boolean }} Readout
 */
/**
 * Where the ESP's external sensors of one type join a screen's readouts: one EXT_TYPES row set per
 * `STATE.ext` entry of that type (renderFeature expands it), none without one.
 * @typedef {{ ext: string }} ExtReadouts
 */
/**
 * A curated feature card. `lighting`/`roof` swap in a custom renderer; `warn`/`note` are dynamic
 * banners; `confirm` is a feature-level actuation gate.
 * @typedef {Object} Feature
 * @property {string} title
 * @property {string} [icon]
 * @property {Control[]} [controls]
 * @property {(Readout|ExtReadouts)[]} [readouts]
 * @property {(s: FnState) => (string|null|undefined)} [summary]
 * @property {(s: FnState) => (string|null|undefined)} [warn]
 * @property {(s: FnState) => (string|null|undefined)} [note]
 * @property {(w: string, v?: string|number|null) => (string|null|undefined)} [confirm]   // null = no prompt for this control
 * @property {boolean} [lighting]
 * @property {boolean} [roof]
 */

const app = /** @type {HTMLElement} */ (document.getElementById("app"));
const titleEl = /** @type {HTMLElement} */ (document.getElementById("title"));
const backEl = /** @type {HTMLElement} */ (document.getElementById("back"));
const statusEl = /** @type {HTMLElement} */ (document.getElementById("status"));
const menuEl = /** @type {HTMLElement} */ (document.getElementById("menu"));

// --- i18n: EN (source strings are the keys) + a DE override map (webui/strings.de.js) --------
// A missing DE key falls back to its English source, so partial coverage never breaks the UI.
let LANG = "en";
try {
  const stored = localStorage.getItem("calictl.lang");
  LANG = stored || (navigator.language && navigator.language.toLowerCase().startsWith("de") ? "de" : "en");
} catch (e) { /* private mode / blocked storage -> English default */ }
document.documentElement.lang = LANG;   // keep <html lang> in sync for screen readers / hyphenation

/** Translate an English source string. @param {string|null|undefined} s @returns {string|null|undefined} */
function t(s) {
  if (LANG !== "de" || !s) return s;
  const m = /** @type {Record<string,string>|undefined} */ (/** @type {any} */ (window).STRINGS_DE);
  return (m && m[s]) || s;
}
/** Translate a template with {name} placeholders. @param {string} s @param {Record<string,any>} subs */
function tf(s, subs) {
  let r = /** @type {string} */ (t(s));
  for (const k in subs) r = r.split("{" + k + "}").join(String(subs[k]));
  return r;
}
/** @param {string} l */
function setLang(l) {
  LANG = l;
  document.documentElement.lang = l;
  try { localStorage.setItem("calictl.lang", l); } catch (e) { /* storage blocked */ }
  closeMenu();
  render();
}

// --- topbar context menu (UX: pairing chrome lives here, not in the main flow) ---------------
let menuPop = /** @type {?HTMLElement} */ (null);

/** WAI-ARIA menu keys: arrows/Home/End move focus (with wrap), Tab closes, Escape closes + refocuses ⋮.
 * @param {KeyboardEvent} ev */
function onMenuKey(ev) {
  if (ev.key === "Escape") { closeMenu(); menuEl.focus(); return; }
  if (ev.key === "Tab") { closeMenu(); menuEl.focus(); return; }   // default Tab then moves on from ⋮
  if (!menuPop) return;
  const items = /** @type {HTMLElement[]} */ (Array.from(menuPop.querySelectorAll('[role="menuitem"]')));
  const i = items.indexOf(/** @type {HTMLElement} */ (document.activeElement));
  // nothing focused (i = -1): ArrowUp/End -> last, ArrowDown/Home -> first
  const next = { ArrowDown: i + 1, ArrowUp: (i < 0 ? items.length : i) - 1, Home: 0, End: items.length - 1 }[ev.key];
  if (next === undefined) return;
  ev.preventDefault();
  items[(next + items.length) % items.length].focus();
}

function closeMenu() {
  if (menuPop) { menuPop.remove(); menuPop = null; }
  menuEl.setAttribute("aria-expanded", "false");
  document.removeEventListener("keydown", onMenuKey);
  document.removeEventListener("click", closeMenu);   // keyboard-closed: drop the idle outside-click listener
  window.removeEventListener("resize", closeMenu);
}

/** The URL of screen `v`: `#<screen>` (Home: none), so a reload reopens the screen. @param {string} v */
const viewUrl = (v) => location.pathname + location.search + (v === "home" ? "" : "#" + v);

/** Deep links, taken once the runtime is known (the first `_meta`): a screen's hash (`#devicestatus`,
 * `#satsettings`, `#wifisetup`, `#<function>`; the satellite screens are Home on calictl) opens it as
 * this entry's screen, so a reload stays there; `#pair` (the wizard at the top of Home) is an action
 * and consumed. A deep link wins over the setup landing. */
function openFromHash() {
  const h = location.hash.slice(1);
  const screen = h === "devicestatus" || ORDER.includes(h) || (SAT_VIEWS.includes(h) && satellite());
  if (!STATE._meta || !(screen || h === "pair" || SAT_VIEWS.includes(h))) return;
  landChecked = true;
  if (h === "pair") {
    history.replaceState(null, "", viewUrl("home"));
    openPairingWizard();
    return;
  }
  const v = screen ? h : "home";
  if (screen && v === view) return;   // back/forward between our own entries fires hashchange too: popstate showed it
  try { history.replaceState({ v: v }, "", viewUrl(v)); } catch (e) { /* no history API */ }
  if (SAT_VIEWS.includes(v)) satDom = null;
  closeMenu();
  show(v, 0);
  if (v === "devicestatus" && !PAIRING) pairingFetch().then(() => { if (view === "devicestatus") render(); });
}

/** The in-app Device-status screen (from the ⋮ menu or the status pill). */
function openDeviceStatus() {
  closeMenu();
  goto("devicestatus");
  // The load-time /api/pairing fetch may have failed (PAIRING still null): retry on open so
  // the screen can tell "unknown" from a fetched "not paired" (CodeRabbit, PR #267).
  if (!PAIRING) pairingFetch().then(() => { if (view === "devicestatus") render(); });
}
statusEl.onclick = openDeviceStatus;
window.addEventListener("hashchange", openFromHash);   // the same link followed on an open page

/** A menu row. @param {string} label @param {() => any} onclick */
function menuItem(label, onclick) {
  const b = document.createElement("button");
  b.type = "button";
  b.setAttribute("role", "menuitem");
  b.textContent = label;
  b.onclick = onclick;
  return b;
}

menuEl.onclick = (ev) => {
  ev.stopPropagation();
  if (menuPop) { closeMenu(); return; }
  menuPop = document.createElement("div");
  menuPop.className = "menupop";
  menuPop.setAttribute("role", "menu");
  menuPop.setAttribute("aria-labelledby", "menu");
  // Groups, separated by dividers: [Device status] | [Help, language]. No pairing here (owner
  // 2026-10-15): it lives in the unpaired banner, Device status's Camper-unit card and the `#pair` link.
  /** @type {HTMLElement[][]} */
  const groups = [[], []];
  // The in-app Device-status screen, on both flavors (the satellite's settings open from it).
  groups[0].push(menuItem(/** @type {string} */ (t("Device status")), openDeviceStatus));   // also before the first answer (M1)
  if (HELP[view]) groups[1].push(menuItem(/** @type {string} */ (t("Help")), openHelp));   // per-screen help (#314)
  // Language toggle: label names the language you'd switch TO.
  groups[1].push(menuItem(LANG === "de" ? "English" : "Deutsch", () => { setLang(LANG === "de" ? "en" : "de"); }));
  for (const g of groups.filter((g) => g.length)) {
    if (menuPop.childElementCount) {
      const sep = document.createElement("div");
      sep.setAttribute("role", "separator");
      menuPop.appendChild(sep);
    }
    for (const b of g) menuPop.appendChild(b);
  }
  // fixed, right under the topbar: its bottom edge already includes env(safe-area-inset-top)
  // (iPhone PWA status bar) and the sticky topbar never scrolls away.
  menuPop.style.top = /** @type {HTMLElement} */ (menuEl.parentElement).getBoundingClientRect().bottom + "px";
  document.body.appendChild(menuPop);
  menuEl.setAttribute("aria-expanded", "true");
  /** @type {HTMLButtonElement} */ (menuPop.firstElementChild).focus();   // keyboard lands in the menu
  document.addEventListener("keydown", onMenuKey);                        // Escape closes it
  // close on any outside click/tap (registered after this event finishes bubbling)
  setTimeout(() => { if (menuPop) document.addEventListener("click", closeMenu, { once: true }); }, 0);
  // `top` is measured once: a rotation / topbar-height change closes rather than misplaces it
  window.addEventListener("resize", closeMenu, { once: true });
};

/** @type {State} */
let STATE = {};
let view = "home";
// Command queue: BLE writes are serial, so we send one at a time but keep the GUI fluid.
// Tapping a control enqueues it and shows an OPTIMISTIC value immediately; re-changing the
// same control prunes its still-queued command so rapid changes coalesce to the latest value.
/** @type {CommandItem[]} */
let queue = [];          // [{fn, what, value, key}] waiting to send (prunable)
/** @type {CommandItem|null} */
let inflight = null;     // {fn, what, value, key} currently POSTing (NOT prunable)
/** @type {Record<string, string|number|null>} */
const optimistic = {};   // key -> intended value, shown until the real state catches up
let lastRender = "";     // signature of the last paint, to skip idle re-renders
// Lighting's "Functions & settings" section open/closed, per viewer (survives the poll re-render;
// storage is optional — blocked storage just means "collapsed" on the next load)
const LFUNCS_KEY = "calictl.lightingFunctionsOpen";
let lightFuncsOpen = false;
try { lightFuncsOpen = localStorage.getItem(LFUNCS_KEY) === "1"; } catch (e) { /* storage blocked */ }
let lastAcNotice = 0;    // ts of the last auto-camper notice shown as a toast (dedupe)

/** @type {(fn: string, what: string) => string} */
const qKey = (fn, what) => fn + "·" + what;
// The wake-up builder packs the next local HH:MM as if UTC (dg/h.m0). The ESP satellite has no clock:
// the page sends its own wall clock read as UTC (whole seconds), like the app uses the phone's.
// calictl ignores it (buspi keeps its clock).
const localNow = () => Math.floor((Date.now() - new Date().getTimezoneOffset() * 60000) / 1000);
const hasWork = () => !!inflight || queue.length > 0;

// --- UX telemetry (R_UX_TELEMETRY): screen views, control ids + values (no secrets), load timings and
// script errors, batched into one navigator.sendBeacon("/api/ux") per 10 s + one on pagehide, and
// only while `_meta.ux` is true (events queued before the first `_meta` wait for it). A random
// per-tab id held in memory only; no user identity, no secrets. Telemetry never breaks the page: an
// old firmware's 404 is ignored (a beacon has no answer to read), and every step is guarded.
const UX_FLUSH_MS = 10000, UX_MAX_EVENTS = 16, UX_MAX_BYTES = 1024;   // the ESP's request buffer is 2 KB with headers
/** @type {Record<string, string|number>[]} */
let uxQueue = [];
const UX_SID = Array.from(crypto.getRandomValues(new Uint8Array(8)), (b) => b.toString(16).padStart(2, "0")).join("");
const UX_UA = /iPhone|iPad|iPod/.test(navigator.userAgent) || (/Macintosh/.test(navigator.userAgent) && navigator.maxTouchPoints > 1)
  ? "ios" : /Android/.test(navigator.userAgent) ? "android" : /Macintosh/.test(navigator.userAgent) ? "mac" : "other";
// busnet.<tailnet> (the van router's tailnet HTTPS proxy to the ESP), buspi (buspi:8088 /
// buspi.<tailnet>), else "lan" (the home or van LAN, 192.168.8.x included). Prefix match only.
/** @param {string} host */
const uxOrigin = (host) => host.startsWith("busnet") ? "busnet" : host.startsWith("buspi") ? "buspi" : "lan";
const UX_ORIGIN = uxOrigin(location.hostname);
const UX_STANDALONE = matchMedia("(display-mode: standalone)").matches || /** @type {any} */ (navigator).standalone === true;
/** An id as the endpoints accept it: [a-z0-9_.-]{1,32}. @param {string} s */
const uxId = (s) => String(s).toLowerCase().replace(/[^a-z0-9_.-]/g, "_").slice(0, 32) || "_";
/** @param {Record<string, string|number>} ev */
function ux(ev) {
  uxQueue.push(ev);
  if (uxQueue.length > UX_MAX_EVENTS) uxQueue.shift();
}
/** An error as one line of printable ASCII, at most 200 chars. @param {string} msg @param {string} [file] @param {number} [line] */
function uxError(msg, file, line) {
  const where = file ? " @ " + file.split("/").pop() + ":" + (line || 0) : "";
  ux({ type: "error", msg: (String(msg) + where).replace(/[^ -~]/g, "?").slice(0, 200) || "?" });
}
function uxFlush() {
  try {
    if (!uxQueue.length || !STATE._meta) return;
    if (!STATE._meta.ux) { uxQueue = []; return; }
    const body = { sid: UX_SID, ua_class: UX_UA, standalone: UX_STANDALONE, origin: UX_ORIGIN, events: uxQueue };
    uxQueue = [];
    let s = JSON.stringify(body);
    while (s.length > UX_MAX_BYTES && body.events.length > 1) { body.events.shift(); s = JSON.stringify(body); }
    if (navigator.sendBeacon) navigator.sendBeacon("/api/ux", s);
  } catch (e) { /* telemetry never breaks the page */ }
}
addEventListener("error", (e) => uxError(e.message, e.filename, e.lineno));
addEventListener("unhandledrejection", (e) => uxError("unhandled rejection: " + (e.reason && e.reason.message || e.reason)));
addEventListener("pagehide", uxFlush);
setInterval(uxFlush, UX_FLUSH_MS);
// a control has a command queued or in flight -> show a pending badge
/**
 * @param {string} fn
 * @param {string} what
 * @returns {boolean}
 */
function pending_is(fn, what) {
  const k = qKey(fn, what);
  return (inflight && inflight.key === k) || queue.some((c) => c.key === k);
}
// optimistic overlay: the user's intended value wins until the command settles + state refreshes
/** @type {(fn: string, what: string, realOn: boolean) => boolean} */
const optOn = (fn, what, realOn) => {
  const k = qKey(fn, what);
  return k in optimistic ? truthy(optimistic[k]) : realOn;
};
/** @type {(fn: string, what: string, realNum: number) => number} */
const optNum = (fn, what, realNum) => {
  const k = qKey(fn, what);
  return k in optimistic ? Number(optimistic[k]) : realNum;
};
/** @type {(fn: string, what: string, realVal: any) => any} */
const optSel = (fn, what, realVal) => {   // optimistic string/enum value (segmented selects)
  const k = qKey(fn, what);
  return k in optimistic ? optimistic[k] : realVal;
};
/** @type {(v: unknown) => boolean} */
const truthy = (v) => v === true || v === 1 || v === "on" || v === "1";

/** @type {(b: unknown) => string} */
const yn = (b) => /** @type {string} */ (t(b ? "yes" : "no"));
/** @type {(b: unknown) => string} */
const onoff = (b) => /** @type {string} */ (t(b ? "On" : "Off"));
// Format a numeric value with a unit, but never print "null V"/"undefined %": a null/undefined
// reading (e.g. starter battery when engine-off) renders as an em-dash instead.
/** @type {(v: number|null|undefined, u: string) => string} */
const withUnit = (v, u) => (v == null ? "—" : `${v} ${u}`);
/** "<W> W · <A> A · <V> V", W = V × A rounded; "—" for a missing part. @param {number|null|undefined} v @param {number|null|undefined} a */
const wav = (v, a) => `${v != null && a != null ? Math.round(v * a) : "—"} W · ${a ?? "—"} A · ${v ?? "—"} V`;
/** @type {(t: Tank|null|undefined) => string} */
const tank = (t) => (t && t.percent != null ? `${t.percent}%  (${t.liters}/${t.capacity_l} L)` : "—");
// dg/i brightness enum -> display: 0-10 are real levels, 11=device default, >=13 is no-reading
// (13=NOT_EQUIPPED, 14=post-reset sentinel) — never show those raw numbers next to a 0-10 slider
/** @type {(v: number|null|undefined) => string} */
const brightnessText = (v) => (v == null || v >= 13 ? "—" : v === 11 ? "def" : String(v));

// Feature specs. `controls[].what` are /api/command tokens (calictl/control.py BUILDERS);
// `state` is the interpreted /api/state key (calictl/semantics.py). `confirm` gates fuel/
// motion actuators behind a dialog. `readouts[].get(state)` formats a live value.
/** @type {(s: string) => string} */
const cap = (s) => (s ? s.charAt(0).toUpperCase() + s.slice(1).replace(/_/g, " ") : s);

// Cooler fault-enum -> banner text (see semantics.cooler / vf/c.java). Absent/"" = no fault.
/** @type {Record<string, string>} */
const COOLER_FAULT_MSG = {
  door_open: "⚠ Please close the refrigerator box door fully",
  emergency: "⚠ Refrigerator box in emergency mode",
  error: "⚠ Refrigerator box error — please visit a workshop",
};

// Air-heater ErrorCode name -> banner text (semantics._AIRHEATER_ERROR / rf/b.java). These are the
// unit's refusals AFTER a start request — the heater switch itself is never greyed pre-emptively.
// Water fault codes (semantics.water fresh_alert / waste_alert) → the unit's own dialog text,
// verbatim as the app pops it (app-observed 2026-09-16, tools/applab).
/** @type {Record<string, string>} */
const FRESH_WATER_ALERT_MSG = {
  pump_protection: "⚠ Pump protection is active. Please fill up fresh water.",
  sensor_error: "⚠ Fill level sensor in fresh water tank. Emergency water pump operation possible. Please visit a workshop.",
  error: "⚠ Unknown fresh water error.",
  pump_error: "⚠ Fresh water pump. Please visit a workshop.",
  empty: "⚠ Fresh water is empty. Please refill the fresh water tank.",
};
/** @type {Record<string, string>} */
const WASTE_WATER_ALERT_MSG = {
  full: "⚠ Grey water tank is full. Please empty.",
  sensor_error: "⚠ Fill level sensor in waste water tank. Please visit a workshop.",
  error: "⚠ General grey water error. Please visit a workshop.",
};

// Energy fault flags (semantics.energy().faults, raw field names) → the unit's own dialog text,
// verbatim as the app pops it (app-observed 2026-09-16, tools/applab; title · text).
/** @type {Record<string, string>} */
const ENERGY_FAULT_MSG = {
  WarningLevelActive: "⚠ Battery is flat. Charge now!",
  SystemError: "⚠ Something wrong with second battery. Please visit a workshop.",
  SleepWarning: "⚠ The charging cable is still plugged into the vehicle's external socket. Please remove it.",
  CurrentDeratingTemperature: "⚠ Charging current is limited. Ambient temperature is too high.",
  LandDefect: "⚠ External power source device not functioning. Please visit a workshop.",
  DcdcDefect: "⚠ Charging restricted while the vehicle is moving. Please visit a workshop.",
  PvDefect: "⚠ Solar inverter not working. Please visit a workshop.",
  TwoBattNotCharged: "⚠ Battery is not charging. Please reduce power consumption.",
  TwoBattSwitchAtWorkshop: "⚠ Additional battery: deep discharge protection is active. Please visit a workshop.",
  TwoBattSwitchAtCharging: "⚠ Additional battery: charging required. Power consumers will switch off.",
  LandNotAvailable: "⚠ External power source is currently unavailable.",
  WarningLevelTwo: "⚠ Second battery warning level 2",   // no dialog of its own in the app
};

// The unit's own dialog titles + gist for ErrorCode 1–7 (1–5 app-observed 2026-09-16, tools/applab):
// 1 "Battery voltage too low — your second battery looks low; connect external power or charge",
// 2 "Low fuel — switched off, activates again with sufficient fuel", 3 "fault, contact workshop",
// 4 "Emission limit exceeded — switched off automatically; on again at ≥ 5 km/h",
// 5 "deactivated — not while the engine runs or the auxiliary water heater is active";
// 6/7 (app 5.4.0, decompile only) split 5 into "engine running" / "auxiliary heater active".
/** @type {Record<string, string>} */
const AIRHEATER_ERROR_MSG = {
  low_battery: "⚠ Battery voltage too low — charge the second battery or connect external power",
  low_fuel: "⚠ Low fuel — the heater switched off, please refuel",
  system_error: "⚠ Auxiliary air heater fault — please contact your workshop",
  heating_time_exceeded: "⚠ Emission limit exceeded — the heater switched off, drive ≥ 5 km/h to re-enable",
  not_possible: "⚠ Heater deactivated — not while the engine or the auxiliary water heater is running",
  // 6/7 new in app 5.4.0 (ig/b.java:606-700) — the app's own dialog texts
  engine_running: "⚠ Auxiliary air heater deactivated due to engine running.",
  aux_heater_active: "⚠ Auxiliary air heater deactivated due to activation of the auxiliary heater.",
  unknown: "⚠ Heater error",
};

// Roof InfoPopUp alert -> banner text (see semantics.roof / ig/c.java). The enum names are the
// unit's internal IDs; the texts say what the condition MEANS for the user (child_lock is an
// over-use cooldown, not a switch; driving = roof open while the vehicle may move).
/** @type {Record<string, string>} */
const ROOF_ALERT_MSG = {
  child_lock: "⚠ Roof moved too often — available again in a few minutes",
  error: "⚠ Pop-up roof error — please visit a workshop",
  driving: "⚠ Pop-up roof is open — close it before moving the vehicle",
  sensor_error: "⚠ Check the pop-up roof — jammed or blocked",
  emergency_locked: "⚠ Secure the pop-up roof manually (see operating manual)",
  not_possible: "⚠ Function currently unavailable",
  low_battery: "⚠ Battery low — run the engine",
  open_checklist: "Please check before opening the pop-up roof: sufficient space above? Access board open? Window or vehicle door open?",
  not_stationary: "Only possible when stationary",
};
// The subset of roof alerts on which the app refuses a MOVE (ig/c.java j(), plus the not-stationary
// tile state observed on the real app 2026-09-16). sensor_error is deliberately absent: the app shows
// it but still allows open/close. open_checklist (InfoPopUp 2) is absent too: the app's flow is
// press -> checklist -> press again (CAPTURE 2026-10-10, real app, real unit). ROOF_MOVE_BLOCK itself is
// generated from control.ROOF_MOVE_BLOCK into semantics_tables.js.

/**
 * Terminal-15 (ignition) — the vehicle char (1004 `TerminalOneFive`, bit 7) is the authoritative
 * decode; `campingmode.enable` (1202 bit 3) mirrors it one poll later. Cross-feature read on
 * purpose: camping's own copy lagged a key-turn by a full poll in the field (2026-09-16).
 * @param {FnState} s  the current feature's state (fallback source)
 */
const ignitionOn = (s) => {
  const v = STATE.vehicle;
  if (v && typeof v.ignition_on === "boolean") return v.ignition_on;
  return !!s.enable;
};

const ORDER = ["cooler", "campingmode", "lighting", "airheater", "water", "energy", "roof", "vehicle"];

// --- per-screen Help (⋮ → Help, #314): the explanations live HERE, not as banners on the screens.
// view -> [docs page under DOCS_URL, English source text]; the German text is the strings.de.js
// entry keyed by the same English text (paragraphs split on a blank line). Facts come from
// docs/business-logic/*; tests/test_webui_help.py checks every docs page exists.
const DOCS_URL = "https://ckeller42.github.io/open-california/";
/** @type {Record<string, [string, string]>} */
const HELP = {
  home: ["index.html",
    "Each tile is one function of the camper unit; tap it for details. Only functions fitted to this van "
    + "are shown. A ⚠ on a tile means the unit reports a fault there.\n\n"
    + "Sensors: on the ESP satellite, add-on Bluetooth sensors you added in its Satellite settings join the "
    + "screens, named by the alias you gave them: Victron solar charger and battery sensor on Energy, a "
    + "Govee thermometer on Vehicle (its tile then shows the temperature). They are not part of the unit "
    + "and keep updating while it sleeps.\n\n"
    + "Offline: parked, the unit goes into deep sleep and stops answering until the van is used again. "
    + "The page then shows the last data and its age. 🕒 marks a value that was measured earlier, not live. "
    + "Still offline while the van is in use? Check the pairing in Device status.\n\n"
    + "Not paired: no camper unit is paired yet, so there is no data; tap Pair… on the "
    + "banner, or in Device status.\n\n"
    + "Not reachable: this page cannot reach calictl or the satellite (WiFi, Tailscale, power). The van "
    + "itself may be fine.\n\n"
    + "Read-only: control writes are switched off on calictl or the satellite; values still update. An untested-firmware "
    + "warning means the unit runs software this project was not validated on, so decoded values may be off."],
  devicestatus: ["firmware.html",
    "Camper unit: Connection is online while the Bluetooth link to the unit works; asleep usually means the "
    + "parked unit sleeps; not paired means no unit is paired yet. Last update is the age of the newest data. "
    + "Pairing says whether calictl or the satellite is paired with a unit (Details shows its Bluetooth "
    + "address). Pair… and Unpair… are here. Still offline while the van is in use? Check the pairing "
    + "here: a reset of the unit's Bluetooth ends it; unpair, then pair again.\n\n"
    + "Satellite (on the ESP satellite): its firmware, WiFi network, uptime, clock, microSD log, battery "
    + "and free memory. Satellite settings › opens its settings: WiFi (change the network there), restore "
    + "camping after you park, screen, firmware update, usage data and experimental sensors.\n\n"
    + "buspi (on the Pi): the calictl backend that serves this page, and whether control writes are on."],
  satsettings: ["firmware.html",
    "WiFi: the network the satellite is on. Change network… lists the networks it finds (Search again "
    + "looks once more); Other network… takes a name. Before it switches it asks: it then leaves the "
    + "current network, and if the new password is wrong it comes back to the old one by itself and "
    + "says so here.\n\nCamper unit: whether a unit is paired; Pair… and Unpair… are in Device "
    + "status.\n\nRestore camping after you park: turns camping mode and the rear USB ports back on once "
    + "you park, if the engine had switched them off.\n\nScreen: the satellite's display. Brightness from "
    + "room light, wake when a hand is near or on motion, and a brightness offset (− / +).\n\nFirmware "
    + "update: the running build and its channel; stable is tested, latest gets every change. Check "
    + "looks for an update, Install loads it and restarts the satellite.\n\nAdvanced: Send usage data "
    + "sends which screens and controls are used (never values). Experimental turns on the scan for "
    + "add-on Bluetooth sensors; under Sensors add the ones that are yours (a Victron needs its "
    + "encryption key), rename or remove them.\n\nOver the setup hotspot these switches need the home "
    + "WiFi."],
  wifisetup: ["firmware.html",
    "The satellite runs its setup hotspot: it has no home WiFi yet, or cannot reach it. Choose the "
    + "network, enter its password and tap Connect; the satellite then joins it and the hotspot closes. "
    + "Join that network with this phone too and open the address shown.\n\nPairing with the camper unit "
    + "works over the hotspot: tap Pair… and follow the steps. Controls of the unit work only once the "
    + "satellite is on the home WiFi."],
  cooler: ["business-logic/cooler-airheater.html#cooler",
    "Switch the refrigerator box on or off and set its cooling level.\n\n"
    + "Quiet mode: Off, Manual or Automatic. Manual runs quiet mode for up to 23 hours; Automatic runs it "
    + "between the start and end hour you set. Quiet mode can only be set while the box is on.\n\n"
    + "The timer starts cooling at a set time. It can only be set while the box is off.\n\n"
    + "Quiet mode and timer are not yet verified on the van. Banners show what the unit reports: the door "
    + "is not fully closed, emergency mode, or an error for the workshop."],
  campingmode: ["business-logic/auto-camper-mode.html",
    "Camping mode is the unit's master switch for the camper functions. Exterior and interior lighting and "
    + "the rear USB ports only work while it is on; the rear USB ports are off whenever camping mode is off.\n\n"
    + "The unit turns camping mode off when the engine starts and does not allow it while the ignition is "
    + "on: it is only possible when stationary.\n\n"
    + "Restore camping after you park: turns camping mode and the rear USB ports back on once you park "
    + "(ignition off), if the engine had switched them off. It respects a manual off and stands down on "
    + "low battery, so it never fights the unit's power saving."],
  lighting: ["business-logic/lighting-energy-water-sat-roof.html#lighting-service-0x1500-char-0x1501",
    "All lights switches every lamp on or off. The favourites 1–4 recall light profiles stored on the "
    + "unit; save the current lamp levels to one under Functions & settings.\n\n"
    + "Each lamp has a brightness from 0 (off) to 10 (100 %). The pop-up roof reading light only works "
    + "while the pop-up roof is open.\n\n"
    + "Wake-up light: at the wake-up time the chosen vehicle areas light up to the set brightness; the lead "
    + "time adds a gentle ramp of that many minutes before it. At least one area stays selected.\n\n"
    + "Lighting & sliding door: when on, opening the sliding door switches on the rear interior lights."],
  airheater: ["business-logic/cooler-airheater.html#airheater",
    "Immediate heating starts the heater now for the set run time (10–120 min). Continuous heating runs "
    + "without a time limit. It can only be started from inside the vehicle; here you can only turn it "
    + "off, and the run time is locked while it runs.\n\n"
    + "Heating temperature is a level from 1 to 9 or HI, not degrees.\n\n"
    + "Timer: set Start heating at, then Start timer. The heater then starts at that time for the run "
    + "time. If continuous heating is on, it turns off when the timer ends.\n\n"
    + "The heater burns fuel, so every start asks first; it is not live-verified. A ⚠ banner shows why "
    + "the unit refused or stopped it, for example low battery, low fuel or a running engine."],
  water: ["business-logic/value-freshness.html",
    "Fresh and waste water are read-only. The unit measures the tanks only while the van's water system "
    + "is running. Parked, it keeps its last measurement, so the level can lag until the water system "
    + "runs again: it is read correctly, just not live.\n\n"
    + "🕒 marks such a held value and when it was measured. A drop to about 1 litre while parked is the "
    + "unit's stale value, not a real reading; the last plausible level is kept until a new measurement "
    + "arrives.\n\n"
    + "⚠ banners are the unit's own tank alerts, for example pump protection, tank empty or full, or a "
    + "sensor fault."],
  energy: ["business-logic/lighting-energy-water-sat-roof.html#energy-service-0x1600",
    "Second battery is the leisure battery; it is measured all the time. The starter battery is only "
    + "measured with the engine running, so parked it keeps its last values (🕒 stale).\n\n"
    + "The battery bars come from a coarse level shown in 10 % steps, as in the app, not from a precise "
    + "percentage. Voltages and currents are in volts and amps.\n\n"
    + "Vehicle power is the charger fed by the alternator while the engine runs. Shore and solar power "
    + "appear only if fitted. House load is derived: what the chargers deliver minus "
    + "what reaches the second battery. Issues lists the unit's own warnings.\n\n"
    + "The energy mode is not yet verified on the van, and the unit can lock it."],
  roof: ["business-logic/lighting-energy-water-sat-roof.html#roof-pop-top-service-0x1400-char-0x1401",
    "Press and hold open or close: the roof moves while you hold and stops when you release (or press "
    + "stop). The ignition must be on. The unit may wait about 3 seconds before the motor starts, and a "
    + "new press within 1 second is ignored. The roof stops by itself at its end position.\n\n"
    + "⚠ banners are the unit's own roof alerts; most of them block open and close, stop always works.\n\n"
    + "Roof control is not live-verified: watch the roof while it moves. The ESP satellite has no roof "
    + "control."],
  vehicle: ["business-logic/alert-states.html",
    "Ignition shows terminal 15 as the unit reports it.\n\n"
    + "Level indicator: roll and pitch of the van in degrees. They are only available with the ignition "
    + "on; with it off the gauges show -.-°. Above about 10 km/h the unit sends no reading and asks you "
    + "to slow down. Positive pitch means the front is up; the roll direction is not yet confirmed.\n\n"
    + "Vehicle clock is the unit's own clock, advanced live between reads. Firmware shows the unit's "
    + "software versions and whether this project was tested on them."],
};

/** Open the Help sheet for the current screen (a native modal <dialog>; Esc or Close dismisses). */
function openHelp() {
  closeMenu();
  const h = HELP[view];
  if (!h) return;
  const d = document.createElement("dialog");
  d.className = "help";
  const head = document.createElement("h2");
  head.textContent = t("Help") + " · " + titleEl.textContent;
  d.appendChild(head);
  for (const para of /** @type {string} */ (t(h[1])).split("\n\n")) {
    const p = document.createElement("p"); p.textContent = para; d.appendChild(p);
  }
  const a = document.createElement("a");
  a.href = DOCS_URL + h[0]; a.target = "_blank"; a.rel = "noopener";
  a.textContent = /** @type {string} */ (t("More in the documentation"));
  const close = document.createElement("button");
  close.type = "button"; close.className = "btn";
  close.textContent = /** @type {string} */ (t("Close"));
  close.onclick = () => d.close();
  d.append(a, close);
  d.onclick = (ev) => { if (ev.target === d) d.close(); };   // tap on the backdrop closes
  d.onclose = () => d.remove();
  document.body.appendChild(d);   // outside #app: survives the 2 s re-render
  d.showModal();
}
/** @type {Record<string, Feature>} */
const FEATURES = {
  cooler: {
    title: "Cooler", icon: "❄️",
    controls: [
      { what: "power", kind: "toggle", label: "Refrigerator box", state: "on" },
      { what: "level", kind: "slider", label: "Cooling level", state: "level", min: CTL_COOLER_LEVEL_RANGE[0], max: CTL_COOLER_LEVEL_RANGE[1] },
      // Mode 0 = quiet mode off, 2 = manual quiet mode, 4 = automatic (timer-based) quiet mode.
      // Quiet mode is only settable while the box is ON; the start timer only while it is OFF
      // (the server refuses the latter too — control.command_precondition). Both gate on the
      // OPTIMISTIC power value so the rows follow the switch the moment it is tapped.
      { what: "mode", kind: "select", label: "Quiet mode",
        options: [{ value: "normal", label: "Off" }, { value: "quiet", label: "Manual" }, { value: "timer_quiet", label: "Automatic" }],
        current: (s) => Object.keys(CTL_COOLER_MODES).find((k) => CTL_COOLER_MODES[k] === s.mode) || "normal",
        disabled: (s) => !optOn("cooler", "power", !!s.on) && "Switch the refrigerator box on first",
        confirm: () => t("Set the refrigerator box's quiet mode? Not yet verified on the van. Continue?") },
      { what: "night_on", kind: "hour", label: "Quiet mode starts at", current: (s) => s.quiet_from ?? 0,
        disabled: (s) => !optOn("cooler", "power", !!s.on) && "Switch the refrigerator box on first",
        confirm: (h) => tf("Set the automatic quiet mode start to {h}:00? Not verified on the van. Continue?", { h: String(h).padStart(2, "0") }) },
      { what: "night_off", kind: "hour", label: "Quiet mode ends at", current: (s) => s.quiet_to ?? 0,
        disabled: (s) => !optOn("cooler", "power", !!s.on) && "Switch the refrigerator box on first",
        confirm: (h) => tf("Set the automatic quiet mode end to {h}:00? Not verified on the van. Continue?", { h: String(h).padStart(2, "0") }) },
      { what: "timer_set", kind: "time", label: "Cooling starts at",
        current: (s) => (s.timer_hour != null && s.timer_min != null)
          ? String(s.timer_hour).padStart(2, "0") + ":" + String(s.timer_min).padStart(2, "0") : null,
        disabled: (s) => optOn("cooler", "power", !!s.on) && "Switch the refrigerator box off first",
        confirm: (v) => tf("Set the timer so cooling starts at {t}? Not yet verified on the van. Continue?", { t: v }) },
      { what: "__cooltimer", kind: "buttons", label: "Timer",
        actions: [{ what: "timer_start", label: "Start timer" }, { what: "timer_cancel", label: "Cancel timer" }],
        disabled: (s) => optOn("cooler", "power", !!s.on) && "Switch the refrigerator box off first",
        confirm: (b) => tf("{b}? Not yet verified on the van. Continue?", { b: t(b.label) }) },
    ],
    readouts: [
      { label: "Refrigerator box door", get: (s) => (s.door_open ? "⚠ Open" : "Closed") },
      { label: "Timer", get: (s) => onoff(s.timer_active) },
      // Composed value: translate the parts here — renderReadout's t() can't match a concatenation.
      { label: "Automatic quiet mode", get: (s) => (s.quiet_scheduled || s.quiet_from || s.quiet_to)
        ? `${String(s.quiet_from ?? 0).padStart(2, "0")}:00 → ${String(s.quiet_to ?? 0).padStart(2, "0")}:00`
          + (s.quiet_scheduled ? "" : ` (${t("off")})`) : t("off") },
    ],
    // `fault` is the cooler's 2-bit Error enum, only meaningful while powered on (vf/c.java):
    // door_open | emergency | error. Surface it as a banner so a fault is obvious at a glance.
    warn: (s) => (COOLER_FAULT_MSG[/** @type {string} */ (s.fault)] || null),
    summary: (s) => (t(COOLER_FAULT_MSG[/** @type {string} */ (s.fault)]) || (s.on ? `${t("On")} · ${t("level")} ${s.level}` : t("Off"))),
  },
  campingmode: {
    title: "Camping mode", icon: "🏕️",
    controls: [
      // The unit sheds camping master when terminal-15 comes on and refuses it while driving
      // (camping-watch: every `ignition 0->1` pairs with `master_on 1->0`; the app gates its
      // master switch on the same bit — tf/a.java n0()). Read the ignition from the vehicle
      // char (1004 TerminalOneFive, the authoritative decode), falling back to campingmode.enable,
      // which mirrors it one poll later.
      { what: "master", kind: "toggle", label: "Camping mode", state: "master_on",
        disabled: (s) => ignitionOn(s) && "Only possible when stationary" },
      // Lights + rear USB are only actionable in the app while camping master is ON (a UI gate,
      // confirmed in semantics.campingmode + tf/a.java). Grey them when master is off. USB shows
      // the DERIVED usb_powered (master AND UsbCharger) so it reads "off" when master is off,
      // matching the physical state, not the latched UsbCharger field.
      // Gate on the OPTIMISTIC master value (what the master switch itself shows), not the raw
      // polled state: after tapping master ON the real state lands only after the BLE write +
      // readback (0.6-3 s live), and greying lights/USB for that window reads as a failed tap.
      { what: "lights", kind: "toggle", label: "Exterior and interior lighting", state: "lights_on",
        disabled: (s) => !optOn("campingmode", "master", !!s.master_on) && "Turn camping mode on first" },
      { what: "usb", kind: "toggle", label: "Rear USB ports", state: "usb_powered",
        disabled: (s) => !optOn("campingmode", "master", !!s.master_on) && "Turn camping mode on first" },
    ],
    readouts: [{ label: "Ignition", get: (s) => onoff(ignitionOn(s)) }],
    summary: (s) => onoff(s.master_on),
  },
  lighting: {
    title: "Lighting", icon: "💡", lighting: true,   // custom renderer (renderLighting)
    summary: (s) => onoff(s.any_on),
  },
  airheater: {
    title: "Air heater", icon: "🔥",
    // Fuel-burning: every START prompts. Turning continuous heating OFF is the one write here that
    // can't arm anything, and it has its own prompt below — no feature-level one on top.
    confirm: (w, v) => (w === "permanent" || w === "timer_cancel" ? null : tf("Start the fuel-burning auxiliary air heater ({w})? It is not live-verified. Continue?", { w: w })),
    controls: [
      // "Immediate heating" = NormalOperation (start now, for `runtime` minutes). Never greyed (app
      // 5.4.0): under ErrorCode 5 the server refuses ON with the app's dialog text; under continuous
      // heating an ON first asks to turn it off (the plain NormalOperationRequest=1 does that).
      { what: "power", kind: "toggle", label: "Immediate heating", state: "normal",
        confirm: (v, s) => (v === "on" && s && s.permanent ? t("Turn off continuous heating? You can only turn continuous heating on again using the controls in your California.") : null) },
      // "Continuous heating" (PermanentOperation) can only be STARTED from inside the vehicle —
      // the only remote write is OFF (the server refuses "on"). So the switch is live only while
      // it is on, and greyed with the reason otherwise. Optimistic: stays live for the
      // write+readback window after tapping it off.
      { what: "permanent", kind: "toggle", label: "Continuous heating", state: "permanent",
        disabled: (s) => !optOn("airheater", "permanent", !!s.permanent) && "Can only be started from inside the vehicle",
        confirm: (v) => (v === "off" ? t("Turn off continuous heating? It can only be turned back on from inside the vehicle. Continue?") : null) },
      // The app's temperature scale is 1–9 + "HI" (level 10); its run-time slider spans 10–120 min
      // (both observed on the app's heater page, tools/applab 2026-09-16).
      { what: "level", kind: "slider", label: "Heating temperature", state: "level", min: CTL_AIRHEATER_LEVEL_RANGE[0], max: CTL_AIRHEATER_LEVEL_RANGE[1] },
      // App 5.4.0 (2026-10-11): 10-min steps, greyed under continuous heating; a change while heating
      // asks first (the app then writes OFF, RunningTime, ON — calictl writes RunningTime only).
      { what: "runtime", kind: "slider", label: "Run time", state: "running_time", min: 10, max: 120, step: 10, unit: "min",
        disabled: (s) => optOn("airheater", "permanent", !!s.permanent) && "Not available during continuous heating",
        confirm: (v, s) => (s && s.running ? tf("Apply new settings now? Resetting the duration. The heating will stay on for {n} minutes.", { n: v }) : null) },
      // Greyed while the departure timer is armed (the app's row does nothing on tap then).
      { what: "timer", kind: "time", label: "Start heating at",
        current: (s) => (s.timer_hour != null && s.timer_min != null)
          ? String(s.timer_hour).padStart(2, "0") + ":" + String(s.timer_min).padStart(2, "0") : null,
        disabled: (s) => !!s.timer_armed && "Stop the timer first" },
      // The app's timer widget: "Start timer" arms the departure timer (OperationModeAirHeater=3 +
      // OperationModeCombined=1, frame 3f3b017f1f3f), "Stop" clears it (Mode=0, 3f0b007f1f3f) —
      // app-observed 2026-09-16. Armed, the heater starts at "Start heating at" for "Run time".
      { what: "__heattimer", kind: "buttons", label: "Timer",
        actions: [{ what: "timer_start", label: "Start timer" }, { what: "timer_cancel", label: "Stop timer" }],
        confirm: (b, s) => (b.what === "timer_start" && s && s.permanent ? t("Continuous heating will turn off when the timer ends. Would you like to proceed?") : null) },
    ],
    readouts: [
      { label: "Heating temperature", get: (s) => (s.level == null ? "—" : s.level >= 10 ? "HI" : `${t("Level")} ${s.level}`) },
      { label: "Run time", get: (s) => withUnit(s.running_time, "min") },
      // RunningTimeinAction only counts down while heating; parked it just echoes the configured
      // run time (60 = 60 live), which would read as "60 min left" on an idle heater.
      { label: "Remaining", get: (s) => (s.running ? withUnit(s.running_time_remaining, "min") : "—") },
      // The unit's own "Timer: On/Off" row: armed = Mode 3 (semantics.timer_armed), not merely a set time.
      { label: "Timer", get: (s) => (s.timer_armed && s.timer_hour != null && s.timer_min != null)
        ? String(s.timer_hour).padStart(2, "0") + ":" + String(s.timer_min).padStart(2, "0") : onoff(false) },
      { label: "Error", get: (s) => (s.error ? `${t(AIRHEATER_ERROR_MSG[/** @type {string} */ (s.error)] || String(s.error))} (${s.error_code})` : t("none")) },
    ],
    warn: (s) => (s.error ? AIRHEATER_ERROR_MSG[/** @type {string} */ (s.error)] || `⚠ Heater error ${s.error_code}` : null),
    // Status line mirrors the heater's own status bar: Active • Continuous heating / Active •
    // N min remaining / Inactive (• Timer HH:MM when one is armed).
    summary: (s) => {
      if (s.error) return /** @type {string} */ (t(AIRHEATER_ERROR_MSG[/** @type {string} */ (s.error)] || "⚠ Heater error"));
      // "• Timer: HH:MM" only while ARMED (the app drops it after "Stop" even with a time set).
      const timer = (s.timer_armed && s.timer_hour != null && s.timer_min != null)
        ? String(s.timer_hour).padStart(2, "0") + ":" + String(s.timer_min).padStart(2, "0") : null;
      if (s.running) {
        if (s.permanent) return timer ? tf("Active • Continuous heating • Timer starts in {t}", { t: timer }) : `${t("Active")} • ${t("Continuous heating")}`;
        if (s.running_time_remaining != null) {
          return timer ? tf("Active • Remaining time: {n} min • Timer starts: {t}", { n: s.running_time_remaining, t: timer })
            : tf("Active • {n} min remaining", { n: s.running_time_remaining });
        }
        return /** @type {string} */ (t("Active"));
      }
      return timer ? tf("Inactive • Timer: {t}", { t: timer }) : /** @type {string} */ (t("Inactive"));
    },
  },
  water: {
    title: "Water", icon: "💧",
    readouts: [
      // Composed values: translate the suffix here (renderReadout's t() can't match a concatenation).
      { label: "Fresh water", get: (s) => tank(s.fresh) + (s.fresh && s.fresh.stale ? "  " + measuredText(s, /** @type {string} */ (t("🕒 last measured"))) : ""),
        bar: (s) => s.fresh && s.fresh.percent },
      { label: "Waste water", get: (s) => tank(s.waste) + (s.waste && s.waste.stale ? "  " + measuredText(s, /** @type {string} */ (t("🕒 last measured"))) : ""),
        bar: (s) => s.waste && s.waste.percent },
    ],
    // The unit's own water fault dialogs (fresh first, then waste), see alert-states.md §5.
    warn: (s) => {
      const f = s.fresh_alert ? FRESH_WATER_ALERT_MSG[/** @type {string} */ (s.fresh_alert)] : null;
      const w = s.waste_alert ? WASTE_WATER_ALERT_MSG[/** @type {string} */ (s.waste_alert)] : null;
      return f && w ? `${t(f)} ${t(w)}` : (f || w || null);
    },
    // Held (stale) tanks carry their own "🕒 measured …" hint in the readouts above; the why (the unit
    // measures only while the water system runs) lives in the screen's Help (⋮, #314).
    summary: (s) => (s.fresh ? `${t("Fresh")} ${s.fresh.percent}%${s.fresh.stale ? " " + measuredText(s, /** @type {string} */ (t("(last meas.)"))) : ""}` : ""),
  },
  energy: {
    title: "Energy", icon: "🔋",
    controls: [
      { what: "mode", kind: "select", label: "Energy mode",
        options: [{ value: "normal", label: "Normal" }, { value: "max_charge", label: "Max" }, { value: "eco", label: "ECO" }],
        current: (s) => Object.keys(CTL_ENERGY_MODES).find((k) => CTL_ENERGY_MODES[k] === s.energy_mode),
        disabled: (s) => !!s.energy_mode_locked,
        confirm: () => t("Set the energy management mode? This control is derived from the app and not yet verified on the van. Continue?") },
    ],
    readouts: [
      { label: "Second battery", get: (s) => (s.soc2_pct != null ? `${s.soc2_pct}%` : "—"), bar: (s) => s.soc2_pct },
      // One row per battery in the solar row's order, W · A · V (owner 2026-10-15); W = V × A, derived
      // here from the two verified values (the unit reports no battery power).
      { label: "Second battery power", get: (s) => wav(s.batt2_v, s.batt2_current) },
      // Derived (docs/business-logic/signals.md "House load"): chargers in minus the battery's net
      // current. Satellite only: calictl reads no Victron charger, so its sum would be incomplete.
      { label: "House load", show: () => satellite(), get: (s) => houseLoad(s) },
      { ext: "victron_battery" },
      { label: "Time remaining", get: (s) => (s.batt2_remaining_h != null ? `${s.batt2_remaining_h} h` : "—") },
      { label: "Starter battery", get: (s) => (s.soc1_pct != null ? `${s.soc1_pct}%` : "—"), bar: (s) => s.soc1_pct },
      { label: "Starter power", get: (s) => wav(s.batt1_v, s.batt1_current) },
      // "Vehicle power" = the DC-DC charger fed from the alternator while the engine runs.
      { label: "Vehicle power", show: (s) => !!s.dcdc_installed, get: (s) => `${t(s.dcdc_state)} (${s.dcdc_power} W · ${s.dcdc_current} A)` },
      { label: "Shore power", show: (s) => !!s.shore_installed, get: (s) => `${t(s.shore_state)} (${s.shore_power} W · ${s.shore_current} A)` },
      { label: "Solar power", show: (s) => !!s.solar_installed, get: (s) => `${t(s.solar_state)} (${s.solar_power} W · ${s.solar_current} A)` },
      { ext: "victron_mppt" },
      // The unit's own dialog texts (ENERGY_FAULT_MSG), never the raw flag names; then the ESP's
      // solar chargers' error codes.
      { label: "Issues", get: (s) => {
        const all = (s.faults || []).map((/** @type {string} */ f) => t(ENERGY_FAULT_MSG[f] || f));
        const mppt = extEntries("victron_mppt");
        for (const [alias, e] of mppt) {
          if (e.error_code) all.push(tf("Solar charger error {n}", { n: e.error_code }) + ` (${cap(alias)})`);
        }
        return all.length ? all.join(" · ") : t("none");
      } },
      // The unit reports an age ONLY for the STARTER battery (AgeOneBattValuesMinutes); 255 = the
      // stale sentinel (starter subsystem asleep). The leisure battery has no such field — it is
      // measured continuously, so its freshness is just the daemon's read age. Label it as STARTER
      // so it isn't mistaken for the leisure data.
      { label: "Starter data age", get: (s) => (s.stale ? "🕒 stale (starter asleep)"
        : s.age_min != null ? `${s.age_min} min` : "—") },
    ],
    // Starter-battery staleness is the "Starter data age" row; the why lives in the Help (⋮, #314).
    summary: (s) => (s.soc2_pct != null ? `${t("Battery")} ${s.soc2_pct}%` : ""),
  },
  roof: {
    title: "Roof", icon: "🚐", roof: true,
    readouts: [
      { label: "Position", get: (s) => (s.position_name ? cap(s.position_name) : (s.position ?? "—")) },
      { label: "Safety valid", get: (s) => yn(s.safety_valid) },
      { label: "Alert", get: (s) => (s.alert ? ROOF_ALERT_MSG[s.alert] || s.alert : "none") },
    ],
    // InfoPopUp alert -> a banner (ig/c.java): child lock, sensor error, low battery, etc.
    warn: (s) => (s.alert ? ROOF_ALERT_MSG[s.alert] || `⚠ Roof: ${s.alert}` : null),
    summary: (s) => (s.alert ? /** @type {string} */ (t(ROOF_ALERT_MSG[s.alert] || `⚠ ${s.alert.replace(/_/g, " ")}`))
                     : (s && s.position_name ? /** @type {string} */ (t(cap(s.position_name))) : "")),
  },
  vehicle: {
    title: "Vehicle", icon: "🚗",
    readouts: [
      { label: "Ignition", get: (s) => onoff(s.ignition_on) },
      // 1004 CarVariant -> the app's model name (semantics._CAR_MODEL); unknown raw -> "—"
      { label: "Model", get: (s) => s.car_model ?? "—" },
      { label: "Level indicator (roll / pitch)",
        // CarLevelPopUp 2 = driving: the app swaps the gauges for "Please slow down" (< 10 km/h).
        // No reading (ignition off): the app's "-.-°" plus its "Signal unavailable" hint.
        get: (s) => (s.level_popup === 2 && s.ignition_on ? t("Please slow down (below 10 km/h)")
          : s.level_roll == null || s.level_pitch == null
          ? "-.-° / -.-°" : `${fmtLevel(s.level_roll)} / ${fmtLevel(s.level_pitch)}`),
        widget: (s) => (s.level_popup === 2 && s.ignition_on ? null
          : levelGauges(s.level_roll, s.level_pitch, roofIsUp())) },
      { label: "Vehicle clock", get: (s) => s.car_clock ?? "—", tick: true },
      { ext: "govee" },
      { label: "Firmware (amb · cm · comm)",
        get: () => {
          const fw = (STATE._meta && STATE._meta.firmware) || {};
          if (fw.amb_sw_version == null && fw.comm_version == null) return "—";
          return `${fw.amb_sw_version ?? "?"} · ${fw.cm_sw_version ?? "?"} · ${fw.comm_version ?? "?"}`
            + (fw.untested ? t("  ⚠ untested") : t("  ✓ tested"));
        } },
    ],
    // The climate inside when the ESP reads a Govee (the first by alias), else the ignition.
    summary: (s) => {
      const g = extEntries("govee")[0];
      if (!g) return /** @type {string} */ (t(s.ignition_on ? "Ignition on" : "Ignition off"));
      const e = g[1];
      return `${fix(e.temp_c, 1, " °C")} · ${fix(e.humidity, 0, " %")}` + (extStale(e) ? " 🕒" : "");
    },
  },
};

/**
 * @param {string} path
 * @param {RequestInit} [opts]
 * @returns {Promise<any>}
 */
async function api(path, opts) {
  // Defence in depth: the satellite serves /api/state, /api/pairing and, when it accepts writes,
  // /api/command (+ /api/auto_camper if reported) -- never let any caller fan out to a calictl-only
  // endpoint there; refuse before any network request.
  const w = path === "/api/command" || (path === "/api/auto_camper" && hasAutoCamper());
  if (satellite() && path.split("?")[0] !== "/api/state" && path !== "/api/pairing" && !(w && !readOnly()))
    throw new Error("satellite: no " + path);
  const r = await fetch(path, opts);
  return r.json();
}

/**
 * @param {string} text
 * @param {string} [kind]
 */
function setStatus(text, kind) {
  const tx = /** @type {string} */ (t(text)), cls = "pill" + (kind ? " " + kind : "");
  // tap -> that screen; the name keeps the state for screen readers ("Device status: live")
  const label = t("Device status") + ": " + tx;
  if (statusEl.textContent === tx && statusEl.className === cls
      && statusEl.getAttribute("aria-label") === label) return;   // aria-live: no re-announce
  statusEl.textContent = tx;
  statusEl.className = cls;
  statusEl.setAttribute("aria-label", label);
}

/**
 * @param {string} fn
 * @returns {boolean}
 */
function installed(fn) {
  const s = STATE[fn];
  return !s || s.installed !== false;
}

// read-only = the daemon rejects control writes (the safe default; enable with --enable-writes /
// CALICTL_ENABLE_WRITES=1). The UI disables every control and shows a banner when true.
// Unknown runtime (no `_meta` answered yet) is treated restrictively: read-only, and no calictl-only
// affordance (the same app.js runs on the ESP32 satellite, which must never see /api/session|roof|...).
const readOnly = () => !STATE._meta || !!STATE._meta.read_only;
// The ESP32 satellite (semantics.js adaptSatellite): no session API.
const satellite = () => !!(STATE._meta && STATE._meta.satellite);
// The satellite inlines this script into its page (no src); calictl serves it as /app.js. Known before
// the first answer, so the first poll of a page load can already say "seen" to the satellite.
const SAT_BUNDLE = !(/** @type {HTMLScriptElement|null} */ (document.currentScript))?.src;
// A calictl daemon has answered (positively known; not the satellite, not still unknown).
const isCalictl = () => !!(STATE._meta && !STATE._meta.satellite);
// calictl, or a satellite firmware reporting device.auto_camper
const hasAutoCamper = () => isCalictl() || !!(STATE._meta && STATE._meta.auto_camper);
// What the satellite cannot do (the roof): the firmware answers the same words.
const ELSEWHERE = "Only via buspi or the app";
// The firmware's /api/command error codes the owner can act on, as a sentence (the rest stay
// "Command failed: <code>"): 403 setup_mode, 409 busy, 503 not_connected (web.c).
/** @type {Record<string, string>} */
const CMD_ERRORS = {
  setup_mode: "Controls work only on your home WiFi — not over the setup hotspot",
  busy: "The satellite is still sending the previous command — try again in a moment",
  not_connected: "Not connected to the camper unit yet — try again in a few seconds",
};
// How long a command waits for the satellite's link (not_connected) before it gives up. Live with
// camping off (2026-10-24): the rest of the 7 s reconnect cooldown + connect + GATT discovery (~6 s)
// + the read pass took 14.5-22.9 s per tap, ~32 s when one connect attempt timed out (10 s).
const CMD_LINK_WAIT_MS = 45000;

/**
 * @param {string} msg
 * @param {string} [kind]
 * @param {number} [ms]
 */
function toast(msg, kind, ms = 4000) {
  const d = document.createElement("div");
  d.className = "toast" + (kind ? " " + kind : "");
  d.textContent = /** @type {string} */ (t(msg));
  (/** @type {HTMLElement} */ (document.getElementById("toasts"))).appendChild(d);
  if (ms) setTimeout(() => d.remove(), ms);   // 0 = stays until removed
  return d;
}

let polling = false;     // a state poll is on the wire
let forceNext = false;   // a forced refresh arrived while one was: repaint when that one lands
let seenNext = false;    // ... and the next poll that goes out tells the satellite "seen" (R_FW_BLE_VIEWER_KEEP)

// --- link state (R_UI_LINK_STATE): one linkState() drives the status pill AND the #conn banner ----
// "Unreachable" after this long with no answer: longer than the satellite's 4 s command stall plus a
// 2 s tick, and longer than a daemon restart (the TLS proxy 502s, then JSON again), so a blip never
// shows red. Timed from the oldest unanswered poll's send, not from the last answer: a hidden tab
// sends nothing, so it never counts down.
const UNREACHABLE_MS = 15000;
// The state poll's own timeout: above the satellite's 4 s command stall (that poll just waits), so a
// hung TCP connection (dead Tailscale peer) fails and the next tick retries instead of waiting out
// iOS's ~75 s connect timeout.
const POLL_TIMEOUT_MS = 8000;
let waitingSince = /** @type {number|null} */ (null);   // ms: the oldest unanswered poll was sent
let lastAnswerAt = /** @type {number|null} */ (null);   // ms: the backend last answered (restored: saved)
let stateAt = 0;                                        // ms: STATE (its _meta.age_s) was taken
const connEl = /** @type {HTMLElement} */ (document.getElementById("conn"));
let connKey = "";

/** @returns {"live"|"asleep"|"never"|"unpaired"|"connecting"|"unreachable"|"satellite"|"rest"|"redial"} */
function linkState() {
  if (waitingSince != null && Date.now() - waitingSince >= UNREACHABLE_MS) return "unreachable";
  const m = STATE._meta;
  // serve --no-ble: this Pi never opens BLE, the ESP satellite talks to the unit (no offline banner)
  if (m && m.session === "satellite" && !m.restored) return "satellite";
  // a snapshot, nothing yet, or no FRESH answer (a tab back after hours: its STATE is hours old;
  // a normal blip's gap is ~2 s)
  if (lastAnswerAt == null || (m && m.restored)
      || (waitingSince != null && waitingSince - lastAnswerAt > UNREACHABLE_MS)) return "connecting";
  // satellite in power-save rest (R_UI_REST_CARD): the unit hung up with camping off; the data ages
  if (m && m.sat && m.sat.link_rest) return "rest";
  // a planned redial (R_UI_LINK_CALM): the unit hung up to save energy and the satellite dials again
  // for this viewer. On data younger than SAT_OFFLINE_S the pill alone says so (no banner, so no
  // layout shift and nothing announced); older data takes the connecting banner with its age.
  if (m && m.sat && m.sat.link_redial && !m.sat.link_ready)
    return m.age_s != null && m.age_s + (Date.now() - stateAt) / 1000 <= SAT_OFFLINE_S ? "redial" : "connecting";
  // satellite: fresh data, but no link that takes commands yet (down, or up and still reading/arming);
  // a link that is up is connecting even when the shown data is old (right after Reconnect in rest)
  if (m && m.sat && ((m.online && !m.sat.link_ready) || (m.sat.link_up && !m.sat.link_ready))) return "connecting";
  if (!m || m.online) return "live";
  // no data ever and no unit paired: the cause is "not paired", not a sleeping van (M6)
  return m.last_seen ? "asleep" : m.paired === false ? "unpaired" : "never";
}

// This page's own backend: the satellite, or calictl on the host that served the page (buspi).
function backendName() {
  if (satellite()) return /** @type {string} */ (t("Satellite"));
  const h = location.hostname;
  return "calictl (" + (/^[\d.:[\]]+$/.test(h) ? h : h.split(".")[0]) + ")";
}

/** @type {Record<ReturnType<typeof linkState>, [string, string]>} every link state (tsc checks the set) */
const LINK_PILL = { live: ["live", "ok"], asleep: ["offline", "warn"], never: ["offline", "warn"],
  unpaired: ["not paired", ""],
  connecting: ["connecting…", ""], unreachable: ["unreachable", "error"],
  satellite: ["🛰 Satellite mode", ""], rest: ["power saving", ""], redial: ["reconnecting…", ""] };

// Paint the pill + the persistent #conn banner (outside #app: a hung poll never reaches render(),
// the 2 s tick still lands here). #conn is a role=status region: its text is replaced only when the
// state/wording changes; the ticking {ago} (or "Checked" clock) lives in an aria-hidden child.
function paintLink() {
  const s = linkState();
  if (!hasWork()) setStatus(LINK_PILL[s][0], LINK_PILL[s][1]);   // a command keeps "Sending…"
  const m = STATE._meta;
  const clock = m && m.last_seen ? clockText(m.last_seen) : null;
  const ago = agoText(m && m.age_s != null ? m.age_s + (Date.now() - stateAt) / 1000 : null);
  let cls = "offline", tmpl = "", tick = "ago", restNext = "";
  /** @type {Record<string, any>} */
  let vars = { clock: clock, ago: ago };
  if (s === "unreachable") {
    cls += " unreachable";
    vars.name = backendName();
    vars.since = clockText(/** @type {number} */ (lastAnswerAt ?? waitingSince) / 1000);
    tmpl = clock ? "⚠ {name} not reachable since {since}. Last data {clock} ({ago})."
      : "⚠ {name} not reachable since {since}.";
  } else if (s === "satellite") {
    cls = "satellite";   // neutral, not the offline warning: the age of the frozen Pi cache, never shown as live
    tmpl = clock ? "🛰 Satellite mode — Pi data from {clock} ({ago})." : "";
  } else if (s === "rest") {
    cls = "rest";   // neutral, not the offline warning: the satellite stays away on purpose
    const next = m && m.sat && m.sat.rest_next_s != null ? Math.max(1, Math.ceil(m.sat.rest_next_s / 60)) : null;
    // ticks like {ago}: outside the key, so the card (and its button's focus) survives each minute
    restNext = next == null ? "" : tf("Next reading in {next} min.", { next: next });
    tmpl = !clock ? "Turn on camping mode to stay connected."
      : "Turn on camping mode to stay connected. Last data {clock} ({ago}).";
  } else if (s === "connecting") {
    cls += " connecting";
    tmpl = clock ? "Connecting… Last data {clock} ({ago})." : "Connecting…";
  } else if (s === "asleep") {
    tmpl = "Offline — van asleep. Last data {clock} ({ago}).";
  } else if (s === "never") {
    tmpl = "Offline — no data yet (van asleep since the monitor started). Checked {clock}.";
    vars.clock = clockText(/** @type {number} */ (lastAnswerAt) / 1000);
    tick = "clock";
  } else if (s === "unpaired" && view !== "wifisetup") {   // WiFi setup: its unpaired banner says it once
    cls += " unpaired";   // neutral: nothing is broken, the setup is not done yet
    tmpl = "Not paired yet — no data.";
  }
  // the ticking value and its brackets go into the aria-hidden span (no "( )" read aloud)
  const filled = tmpl ? tf(tmpl, Object.assign({}, vars, { [tick]: "\u0001" })) : "";
  const br = filled.includes("(\u0001)");
  const parts = tmpl ? filled.replace("(\u0001)", "\u0001").split("\u0001") : [];
  // the rest card is rebuilt (its screen-reader "next reading" re-read) only when that coarse bucket moves
  const nextBucket = s !== "rest" || !restNext ? "" : m && m.sat && (m.sat.rest_next_s ?? 0) >= 600 ? "+" : "-";
  const key = cls + "|" + parts.join("\u0001") + "|" + nextBucket;
  if (key !== connKey) {
    connKey = key;
    // the card's button had the focus: keep it on the rebuilt card, else on the heading (not <body>)
    const focused = connEl.contains(document.activeElement) ? /** @type {HTMLElement} */ (document.activeElement).id : null;
    connEl.className = tmpl ? cls : "";
    connEl.replaceChildren();
    if (s === "rest") {   // the app's card: its headline, then the text
      const hd = document.createElement("strong");
      hd.className = "rest-head";
      hd.textContent = /** @type {string} */ (t("Disconnected to save energy"));
      connEl.append(hd, " ");
    }
    if (parts.length > 1) {
      const sp = document.createElement("span");
      sp.className = "tick";
      sp.setAttribute("aria-hidden", "true");
      connEl.append(parts[0], sp, parts[1]);
    } else if (tmpl) connEl.append(parts[0]);
    if (s === "rest") {
      const nx = document.createElement("span");
      nx.className = "rest-next";
      nx.setAttribute("aria-hidden", "true");
      // read once with the card (not each minute): the visible count ticks in the aria-hidden span
      const once = document.createElement("span");
      once.className = "sr-only";
      once.textContent = restNext;
      const acts = document.createElement("div");
      acts.className = "acts";
      const cm = document.createElement("button");
      cm.type = "button"; cm.className = "btn"; cm.id = "rest-camping";
      cm.textContent = /** @type {string} */ (t("Open camping mode"));
      cm.onclick = () => goto("campingmode");   // the screen only: camping mode also switches lights + USB
      const b = document.createElement("button");
      b.type = "button"; b.className = "btn"; b.id = "rest-connect";
      b.textContent = /** @type {string} */ (t("Reconnect"));
      b.onclick = restConnect;
      acts.append(cm, b);
      connEl.append(" ", nx, once, acts);
    }
    if (focused != null) ((focused && document.getElementById(focused)) || titleEl).focus({ preventScroll: true });
  }
  const sp = connEl.querySelector("span.tick"), nx = connEl.querySelector("span.rest-next");
  if (sp) sp.textContent = br ? "(" + vars[tick] + ")" : String(vars[tick]);
  if (nx) nx.textContent = restNext;
}

/** The rest card's Reconnect: POST /api/ble {"connect":true} (R_UI_REST_CARD), then a fresh poll. */
async function restConnect() {
  ux({ type: "control", fn: "satellite", control: "connect" });
  try { await satFetch("/api/ble", { connect: true }); } catch (e) { /* the next poll shows the state */ }
  await refreshState(true);
}

/** @param {boolean} [force] */
async function refreshState(force) {
  // The satellite answers POST /api/command only after the unit's ACK (<= 4 s) and serves nothing
  // meanwhile (its listen backlog is 2): a poll just waits (no error, so no offline flash). Send at
  // most ONE poll at a time -- Chromium/Firefox hold a second same-URL GET behind the first anyway,
  // WebKit (Safari, iPhone) does not and would stack a connection per 2 s tick on the stalled core,
  // then drain them oldest-first (tests/e2e/test_satellite.py pins "at most one waiting", both engines).
  if (polling) { forceNext = forceNext || !!force; seenNext = seenNext || !!force; return; }
  const seen = !!force || seenNext;
  seenNext = false;
  polling = true;
  if (waitingSince == null) waitingSince = Date.now();
  const ac = new AbortController();
  const timer = setTimeout(() => ac.abort(), POLL_TIMEOUT_MS);
  let next;
  try {
    // a forced poll follows a page load, a return to the page or a user action: the satellite counts it
    // as input (R_FW_BLE_VIEWER_KEEP's idle cap); the 2 s tick is a plain poll. Satellite only.
    next = await api(seen && (SAT_BUNDLE || satellite()) ? "/api/state?seen=1" : "/api/state", { signal: ac.signal });
    // an error body (calictl's 500/503 {error}, a proxy page) is no answer: keep STATE and the
    // unreachable clock. Checked here, not in api(): the wizard and commands read JSON error bodies.
    if (!isSatelliteBody(next) && !(next && typeof next === "object" && next._meta)) throw new Error("no state");
  } catch (e) {
    paintLink();   // a failure alone changes nothing: unreachable is time-based (UNREACHABLE_MS)
    return;
  } finally {
    clearTimeout(timer);
    polling = false;
  }
  waitingSince = null;
  lastAnswerAt = stateAt = Date.now();
  force = force || forceNext;
  forceNext = false;
  // The ESP32 satellite answers RAW decoded fields ({t, fn, device}); interpret them in the browser.
  STATE = isSatelliteBody(next) ? /** @type {State} */ (adaptSatellite(next, Date.now())) : next;
  if (PWA) try { localStorage.setItem(LAST_STATE_KEY, JSON.stringify({ at: Date.now(), state: STATE })); } catch (e) { /* storage blocked/full */ }
  // One-off /api/pairing (not the wizard's 1 s poll): decides the setup card's prominence and the
  // Device status's "Unpair…". Taken the first time a `_meta` answers (calictl or the satellite) -- also when
  // the first poll failed (daemon restarting) -- and never on an unknown runtime.
  if (STATE._meta && !pairingFetched) { pairingFetched = true; await pairingFetch(); openFromHash(); }
  // The setup hotspot (device.wifi.mode setup/off): the app lands on WiFi setup once per load; back in
  // station mode WiFi setup gives way to Home (unless the join was made here: it shows the result).
  const wm = satellite() && STATE._meta && STATE._meta.sat && STATE._meta.sat.wifi ? STATE._meta.sat.wifi.mode : null;
  let landed = false;
  if (STATE._meta && !landChecked) {
    landChecked = true;
    if ((wm === "setup" || wm === "off") && view === "home") { view = "wifisetup"; satDom = null; landed = true; }
  } else if (view === "wifisetup" && wm === "station" && !joinedHere) { view = "home"; landed = true; }
  // the entry shows the new screen: back from a screen opened here returns to it (not pushed: ‹ stays in the app)
  if (landed) try { history.replaceState(Object.assign({}, history.state, { v: view }), "", viewUrl(view)); } catch (e) { /* no history API */ }
  // Auto-camper give-up/stand-down notice: the daemon stamps a one-time {ts,msg} when it stands
  // down (low battery) or gives up (keeps dropping). Show it as a toast once.
  const acn = STATE._meta && STATE._meta.auto_camper && STATE._meta.auto_camper.notice;
  if (acn && acn.ts && acn.ts > lastAcNotice) { lastAcNotice = acn.ts; toast(acn.msg, "warn"); }
  paintLink();
  // the satellite screens keep their DOM (a typed password, an open step): a poll updates them in place
  if (SAT_VIEWS.includes(view) && satDom && satDom.view === view && !landed) { satUpdate(); return; }
  if (landed) { lastRender = ""; force = true; }
  // don't repaint out from under a control the user is actively using — a poll-driven re-render
  // rebuilds the DOM (app.innerHTML="") and would slam an OPEN <select> dropdown shut, or interrupt
  // a slider drag / time entry. Skip while such a control is focused; optimistic values + the queue
  // keep the rest fresh, and a forced refresh after a command still repaints.
  const ae = /** @type {HTMLInputElement} */ (document.activeElement);
  if (!force && ae && (ae.type === "range" || ae.type === "time" || ae.tagName === "SELECT"
      || ae.id === "pairing-passkey")) return;   // don't clobber a mid-typed pairing passkey either
  if (roofHold) return;                          // never rebuild the screen under a held roof button
  const sig = view + "|" + JSON.stringify(next);
  if (!force && sig === lastRender) return; // nothing changed -> no flicker
  lastRender = sig;
  render();
}

// Enqueue a command. Shows the intended value immediately (optimistic) and coalesces rapid
// changes to the same control: any still-queued command for it is pruned so only the latest
// value is sent (the in-flight one can't be un-sent). Then the queue drains one at a time.
/**
 * @param {string} fn
 * @param {string} what
 * @param {string|number|null} value
 */
function command(fn, what, value) {
  if (readOnly()) { toast("Read-only mode — writes are disabled", "warn"); return; }
  /** @type {Record<string, string|number>} */
  const ev = { type: "control", fn: uxId(fn), control: uxId(what) };
  // the value too (printable ASCII, <= 32); secrets never pass through here: the pairing passkey,
  // WiFi password and sensor keys have their own endpoints
  if (value !== null && value !== undefined) ev.value = String(value).replace(/[^ -~]/g, "?").slice(0, 32) || "?";
  ux(ev);
  const key = qKey(fn, what);
  optimistic[key] = value;
  const i = queue.findIndex((c) => c.key === key);   // prune a superseded queued command
  if (i >= 0) queue.splice(i, 1);
  queue.push({ fn, what, value, key });
  // Never rebuild the DOM while a roof move is being held: this render() is what used to
  // detach the held button under the user's finger (see roofHold). The release repaints.
  if (!roofHold) render();
  processQueue();
}

// Drain the queue one command at a time (BLE is serial). Always sends confirm:true — the
// frontend already showed any confirm dialog; the backend only needs it for roof/airheater.
async function processQueue() {
  if (inflight || !queue.length) return;
  inflight = /** @type {CommandItem} */ (queue.shift());
  setStatus("Sending…", "busy");
  if (!roofHold) render();          // same rule as command(): never rebuild under a held roof button
  /** @type {CommandResponse} */
  let res;
  /** @type {Record<string, any>} */
  const body = { function: inflight.fn, what: inflight.what, value: inflight.value, confirm: true };
  const until = Date.now() + CMD_LINK_WAIT_MS;
  for (;;) {
    // the wall clock at each try: a retry can cross the wake-up time the first try saw
    if (inflight.fn === "lighting" && inflight.what === "wakeup") body.local_now = localNow();
    try {
      res = await api("/api/command", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
    } catch (e) {
      res = { ok: false, error: String(e) };
    }
    // The satellite answers not_connected while its link is down (each try also brings the link up):
    // retry until it is ready, unless a newer value for this control is already queued.
    const key = inflight.key;
    if (!(res && res.error === "not_connected") || Date.now() >= until || queue.some((c) => c.key === key)) break;
    setStatus("connecting…", "busy");   // the link state's own word (pill + banner say the same)
    await new Promise((r) => setTimeout(r, 2000));
  }
  const done = inflight;
  inflight = null;
  const expected = satellite() ? expectedState(done) : null;
  if (res && res.ok && res.refused) toast(res.refused, "warn");   // physical precondition not met
  else if (res && res.ok && res.applied === true) toast("✓ Applied", "ok");
  // The ESP satellite has no readback (applied is always null): confirm from the unit's own state.
  // unconfirmed (#264): the link dropped after the write left — wait out the reconnect + read-all.
  else if (res && res.ok && res.applied == null && expected) confirmFromState(done, expected, res.unconfirmed ? SAT_RECONNECT_CONFIRM_MS : SAT_CONFIRM_MS);
  // applied === null: sent + acknowledged but not verifiable remotely. For lighting the state
  // char is a write-through echo, so only the lamp itself is proof — say so. Other functions
  // that return null (e.g. roof, which has no readback check) keep the neutral phrasing.
  else if (res && res.ok && res.applied == null && res.function === "lighting") toast("Sent — check the lamp", "ok");
  else if (res && res.ok) toast("Sent — the unit didn't confirm it", "warn");
  else if (res && res.error && CMD_ERRORS[res.error]) toast(CMD_ERRORS[res.error], "error");
  else toast("Command failed" + (res && res.error ? `: ${res.error}` : ""), "error");
  // drop the optimistic value only if no newer change for this control is still queued
  if (!queue.some((c) => c.key === done.key)) delete optimistic[done.key];
  await refreshState(true);
  processQueue();
}

/**
 * What the unit's interpreted state shows once `c` landed — from the same FEATURES row that renders
 * the control (toggle `state`, slider `state`, select `current`), plus the All-lights master
 * (`any_on`). `null` = not mappable: the caller keeps the neutral "sent" message.
 * @param {CommandItem} c
 * @returns {((s: FnState) => boolean) | null}
 */
function expectedState(c) {
  if (c.fn === "lighting" && c.what === "power") return (s) => !!s.any_on === (c.value === "on");
  if (c.fn === "lighting" && c.what === "wakeup" && typeof c.value === "string") {
    // the unit's Mode-20 report shows the sent time, positionals and (if sent) switch
    const tk = c.value.split(/\s+/), time = tk.find((x) => x.includes(":"));
    const pos = tk.filter((x) => x !== "on" && x !== "off" && !x.includes(":"));
    const sw = tk.includes("on") ? true : tk.includes("off") ? false : null;
    return (s) => {
      const w = s.wakeup;
      return !!w && (!time || w.time === time) && (sw === null || w.enabled === sw)
        && (pos[0] == null || w.areas.join(",") === pos[0].split(",").map(Number).sort().join(","))
        && (pos[1] == null || w.brightness === Number(pos[1])) && (pos[2] == null || w.ramp === Number(pos[2]));
    };
  }
  const f = FEATURES[c.fn];
  const row = f && f.controls ? f.controls.find((x) => x.what === c.what) : null;
  if (!row) return null;
  if (row.kind === "toggle") { const key = row.state; return (s) => !!s[key] === (c.value === "on"); }
  if (row.kind === "slider") { const key = row.state; return (s) => s[key] === Number(c.value); }
  if (row.kind === "select" && row.current) { const cur = row.current; return (s) => cur(s) === c.value; }
  return null;
}

const SAT_RECONNECT_CONFIRM_MS = 20000;   // a dropped link: reconnect (viewer-paced) + read-all
const SAT_CONFIRM_MS = 5000;   // a few 2 s polls: the satellite stores each frame the unit pushes

/**
 * Watch the next `/api/state` polls (no extra requests) for `ok(STATE[c.fn])`, then toast the result.
 * A newer command for the same control supersedes this one silently (it reports its own result).
 * @param {CommandItem} c
 * @param {(s: FnState) => boolean} ok
 * @param {number} ms how long to watch
 */
async function confirmFromState(c, ok, ms) {
  const fn = c.fn;
  const end = Date.now() + ms;
  while (Date.now() < end) {
    if ((inflight && inflight.key === c.key) || queue.some((q) => q.key === c.key)) return;
    const s = STATE[fn];
    if (s && ok(/** @type {FnState} */ (s))) { toast("✓ Applied", "ok"); return; }
    await new Promise((r) => setTimeout(r, 250));
  }
  if (fn === "lighting" && c.what !== "wakeup") toast("Sent — check the lamp", "ok");   // a wake-up lights no lamp
  else toast("Sent — the unit didn't confirm it", "warn");
}

// Actuate a control, gating fuel/motion features behind a confirm() dialog.
/**
 * @param {string} fn
 * @param {string} what
 * @param {string|number|null} value
 */
function act(fn, what, value) {
  const f = FEATURES[fn];
  const msg = f && f.confirm ? f.confirm(what, value) : null;   // null/"" = no feature-level prompt
  if (msg && !confirm(msg)) return;
  command(fn, what, value);
}

// Every screen is a history entry (UX review "navigation" H1/M3) with its `#<screen>` URL (a reload
// reopens it: openFromHash): the browser's back and the iOS edge swipe walk the screens back (from Home
// they leave the app, as before), each with its scroll position; ‹ goes back the same way, or to the
// screen's parent on a fresh page.
/** @param {string} v */
function goto(v) {
  if (SAT_VIEWS.includes(v) && !satellite()) v = "home";   // calictl never shows the satellite screens
  const fwd = v !== view;
  if (fwd) {
    try {
      history.replaceState(Object.assign({}, history.state, { y: scrollY }), "");
      history.pushState({ v: v, pushed: true }, "", viewUrl(v));
    } catch (e) { /* no history API (a sandboxed frame): ‹ still works */ }
    if (SAT_VIEWS.includes(v)) satDom = null;   // a fresh visit, a fresh form
  }
  show(v, fwd ? 0 : null);
}

/** Render screen `v`, scroll to `y` (null: stay) and move the focus to its heading.
 * @param {string} v @param {number|null} y */
function show(v, y) {
  view = v;
  ux({ type: "view", screen: uxId(v) });
  lastRender = "";
  render();
  if (y != null) scrollTo(0, y);
  titleEl.focus({ preventScroll: true });   // keyboard/SR users follow the nav
}

window.addEventListener("popstate", (ev) => {
  if (!ev.state && location.hash) return;   // a followed or typed deep link: hashchange -> openFromHash
  const s = ev.state || {};
  const v = s.v && (!SAT_VIEWS.includes(s.v) || satellite()) ? s.v : "home";
  if (SAT_VIEWS.includes(v)) satDom = null;
  closeMenu();
  show(v, s.y || 0);
});

/** ‹: the screen before when this app pushed this entry, else the screen's parent. */
function back() {
  if (history.state && history.state.pushed) history.back();
  else goto(view === "satsettings" ? "devicestatus" : "home");
}

/**
 * Age of a HELD water reading, like the app's "Updated: x ago": "🕒 measured 2 h ago". `measured_at`
 * is epoch s (calictl serve.state(); semantics.js maps the ESP's device.water_held_age_ms onto it).
 * @param {FnState} s  the water state
 * @param {string} unknown  shown (already translated) when the age is not known
 * @returns {string}
 */
function measuredText(s, unknown) {
  if (typeof s.measured_at !== "number") return unknown;
  return tf("🕒 measured {ago}", { ago: agoText(Math.max(0, Math.round(Date.now() / 1000 - s.measured_at))) });
}

/**
 * @param {number|null|undefined} sec
 * @returns {string}
 */
function agoText(sec) {
  if (sec == null) return /** @type {string} */ (t("unknown"));
  if (sec < 90) return /** @type {string} */ (t("just now"));
  const m = Math.round(sec / 60);
  if (m < 90) return tf("{m} min ago", { m: m });
  const h = Math.round(m / 60);
  return h < 48 ? tf("{h} h ago", { h: h }) : tf("{d} d ago", { d: Math.round(h / 24) });
}

// Absolute wall-clock label from epoch seconds (undefined -> null). Shows a date too when the
// timestamp is not from today, so a days-old "last seen" isn't mistaken for this morning.
/**
 * @param {number|null|undefined} epochSec
 * @returns {string|null}
 */
function clockText(epochSec) {
  if (epochSec == null) return null;
  const d = new Date(epochSec * 1000);
  const loc = LANG === "de" ? "de-DE" : [];   // 24-hour clock + localized date in German
  const t = d.toLocaleTimeString(loc, { hour: "2-digit", minute: "2-digit" });
  const sameDay = d.toDateString() === new Date().toDateString();
  return sameDay ? t : `${d.toLocaleDateString(loc, { month: "short", day: "numeric" })} ${t}`;
}

// Connection toggle for buspi's persistent BLE session. It SHOWS the state and lets you release
// the session now (instead of waiting out the idle auto-release) or reconnect after you have. The
// unit serves several centrals at once, so releasing is not needed for the phone app to connect.
// It is shown ONLY when a session is actually held/connecting, the van is asleep, or you chose to
// disconnect. When the session is merely idle in auto mode the daemon is still polling normally —
// showing a "Connect" button there falsely reads as "no connection", so we hide it (the fast path
// warms automatically the moment you interact). The main status pill still shows live/offline.
function sessionToggle() {
  const m = /** @type {Meta} */ (STATE._meta || {});
  const mode = m.session_mode, st = m.session;
  if (mode == null || mode === "off" || st === "satellite") return null;   // no persistent session / no BLE here -> no toggle
  let spec, action;
  if (mode === "release") { spec = { cls: "", text: "🔌 Disconnected" }; action = "connect"; }
  else if (st === "up") { spec = { cls: "ok", text: "🟢 Live · fast" }; action = "disconnect"; }
  else if (st === "connecting" || st === "degraded") { spec = { cls: "busy", text: "🟡 Connecting" }; action = "disconnect"; }
  else if (st === "asleep") { spec = { cls: "", text: "⚪ Asleep — tap to wake" }; action = "connect"; }
  else return null;   // idle in auto mode while polling normally — not a disconnection, don't imply one
  const el = document.createElement("button");
  el.type = "button";
  el.className = "session-pill" + (spec.cls ? " " + spec.cls : "");
  el.textContent = /** @type {string} */ (t(spec.text));
  el.title = /** @type {string} */ (t(action === "connect"
    ? "Connect the fast BLE session (warm it before controlling)"
    : "Disconnect — release this session now (it also auto-releases when idle)"));
  el.setAttribute("aria-label", el.title);
  el.onclick = async () => {
    el.disabled = true;
    try {
      await api("/api/session", { method: "POST", headers: { "Content-Type": "application/json" },
                                  body: JSON.stringify({ action }) });
    } catch (e) { /* the state refresh below reflects whatever actually happened */ }
    await refreshState(true);
  };
  return el;
}

// --- Level indicator: the app's two gauges -------------------------------------------------------
// The vehicle app shows two round gauges: the van from the SIDE (pitch) and from the REAR (roll),
// each tilted by its angle against a fixed scale, the angle below as an ABSOLUTE value with one
// decimal ("1.5°"), the ring green when level (evidence-ledger "Level Indicator", REAL PHONE
// 2026-10-11). The van outlines are our own drawing, not the app's artwork. Sign conventions:
// +pitch = nose up (front, drawn left, goes up; verified); +roll = the right side up (assumed, as the
// old bubble did; roll's sign is not yet confirmed on the van).
/** Ring green within this many degrees (the app: green at 0.0°, not at 1.5°; its threshold is unknown). */
const LEVEL_OK_DEG = 1;
/** The drawn tilt is exaggerated so 1–2° is visible; the number below it is exact. */
const LEVEL_TILT_GAIN = 3;
/** The scale's ends (drawn degrees); a larger tilt pins at the end. */
const LEVEL_SCALE_DEG = 40;

/** @type {(d: number) => string} */
const fmtLevel = (d) => Math.abs(d).toFixed(1) + "°";

// The Open California logo's van (docs/assets/logo.svg), side view facing left, as filled evenodd
// paths in half the logo's 586x471 units: VAN_BODY = everything below the roofline, VAN_TENT = the raised
// pop-top. Derived by keeping only the logo's big shapes (no background, wordmark, tent mesh, figure or
// small trim), coordinates halved, rounded and made relative. VAN_SHELL = the closed pop-top (our drawing, logo style).
const VAN_BODY = "M195 195c14-1 33-1 42-1 35-1 49-2 47-4 0 0-8-1-22-2-8 0-15 0-15-1 0 0 1-1 1-2 2-3 4-5 5-9 1-9-3-18-11-22-4-2-4-2-8-2-4 0-6 0-8 2-6 2-10 6-12 12-1 2-1 4-1 7 0 5 1 8 4 12l2 3-2-1c-3 0-132-1-137 0l-4 1 2-4c3-4 4-8 4-13-1-5-3-9-6-13-12-12-32-5-34 11-2 6 0 13 4 17l2 2-2 0c-1 0-4 0-5 0-2 0-8 0-14 0-14 1-21 2-17 3 7 2 44 3 100 4 18 0 40 0 46 0 8 0 24 0 39 0zm-139-11c-2 0-6-4-6-6-2-2-2-7-1-10 1-2 4-6 7-8 3-1 8-1 11 1 5 2 7 6 7 11 0 10-8 16-18 12zm174 1c-6-2-10-7-10-13 0-8 6-12 13-12 3 0 6 0 9 3 4 3 5 8 4 13-2 6-10 11-16 9zm-164-5c6-4 5-14-2-16-4-1-8 0-10 4-3 4-1 11 4 12 2 2 6 1 8 0zm172 0c6-4 5-12-1-16-3-1-5-1-8 0-3 2-4 4-5 6 0 4 2 9 5 10 3 2 7 1 9 0zm-202-6c0-2 0-6 0-7 1-4 4-11 6-13 0 0 0 0 0 0 0-1-2-4-8-10-1-1-2-2-2-2-1 1-4 2-8 2-12 2-14 3-16 7 0 3 0 8 0 9 1 0 3 0 8 1 6 0 8 0 8-1 0-2-2-2-6-2-4 0-5-1-4-3 1-1 2-1 6-1 2 0 5 0 6 0 1 1 2 6 3 11 0 5 0 5-1 6-1 1-2 1-6 1-5 0-6-1-10-6 0-1-1-2-2-2l-2 0 0 2c2 7 10 11 26 12l2 0 0-4zm87 3 9 0 0-5 0-5-20-1c-16 0-20 0-20 0 0-1 0-1 0-2 0 0 4 0 20-1l20 0 0-11c0-13 0-13 6-17 4-3 4-3 4-4l0-1-14 0c-8 0-22 0-32 0l-18 1 0 11 0 10 2 2c4 4 6 10 8 18 0 6 0 6 14 6 6 0 16 0 21-1zm83-1c0 0 1-2 1-2 0-7 3-15 7-20 6-4 10-7 17-8 9 0 17 4 23 11l2 3 5 0c3 0 6 0 7 0l2 0 0-16 0-16-10 0c-6 0-26 0-46 0-20 1-42 1-52 2l-16 0 0 16 0 17 28 0 29 0 1 1c0 1 0 2 0 2 0 0-14 0-30 0l-28 0 0 6 0 5 30 0c22-1 30-1 30-1zm65 0c11-2 13-2 13-4 1-2 1-5 0-7-1-3-2-3-15-2-10 0-11 1-11 1 0 4 0 6 0 9 0 3 0 3 2 3 0 0 5 0 11 0zm-223-26c6-4 14-6 21-3 2 1 4 1 5 1 0 0 0 0 0-8l0-9-12 0c-7 0-14 0-16 1l-4 0-4 4-3 4 5 6c3 2 5 5 6 5 0 0 0-1 2-1zm-18-21 5-1 3-2c2-2 6-4 12-7 4-3 9-5 10-5l1-2-5-1c-4 0-6-1-7-1-2 0-15 6-21 9-3 1-6 3-8 5-2 1-5 6-5 6 1 0 3 0 5 0 2 0 6 0 10-1zm38-1 6 0 0-5 0-4-1 0c-3 0-3-1-3-3 0 0 1-2 6-7 12-12 24-21 32-25 4-2 8-2 19-2 11 0 11 0 11 3 0 3-2 19-3 27-1 4 0 4-15 5-16 0-42 1-42 2 0 0 0 2 0 5l0 4 16-1c10 0 24 0 33-1l15 0 0-25 0-25-6 0c-16 1-26 2-33 6-5 2-30 23-39 32-3 2-4 4-8 6-8 4-15 7-15 8 0 0 17 0 27 0zm135-2c17-1 39-1 49-2l18 0 0-4c0-11-2-25-5-35-1-6-2-8-3-9 0 0-27 0-59 0l-57 0 0 26 0 24 12 0c8 0 28 0 45 0zm77-2c0 0 0-3-1-6-1-16-6-36-9-40 0-1-4-2-4-2 0 0 0 2 1 3 3 6 7 33 7 42l0 3 2 0c2 0 4 1 4 1 0 0 0 0 0-1zm-128-8c-2-2-2-3-2-18 0-12 0-14 0-16 1 0 2 0 12 0 7 0 30-1 50-1 42 0 42 0 43 3 1 2 3 6 3 16 1 8 1 10 0 11-1 3 0 3-40 4-19 0-42 1-50 1-12 0-15 0-16 0zm-84-10c7-8 27-24 30-25 1-1 2-1 0-1 0 0-1 0-2-1-2 0-2 1-8 5-12 8-31 22-31 24 0 0 1 0 5 0 2 0 2 0 6-2z";
const VAN_TENT = "M109 76c3-1 5-2 7-2 2 0 2 0 2 0 0-2-2-4-2-4-4-2-12 1-16 4-1 2-1 2 0 2 1 2 4 1 9 0zm73-4c0 0-4-7-4-8-1 0-5 0-18 0-16 0-16 0-16-2-2-2 2-11 6-16 4-4 7-6 10-6 4-2 4-2 4-2-1-1-2-3-4-6-2-4-2-4-5-4-3-2-21-8-21-8-1 0-1 2-1 4-2 16-4 27-8 38-1 5-3 9-3 10 0 0 1 0 30 0 20 0 30 0 30 0zm8 0c-1-1-4-7-8-14-8-16-10-19-10-19 1 0 4 1 7 2 7 2 14 5 17 9 6 4 8 10 6 12-1 2-2 2-7 2-5 0-6 0-6 0 0 0 1 2 2 4l2 4 13 0c8 0 14 0 14 0 0 0 0-1 0-2-2-4-2-4 2-6 5-3 5-3 2-5-6-3-8-5-8-6 0-1 2-2 2-3 2-1 2-2 2-2 0 0-10-4-23-8-12-4-25-8-28-8-3-2-6-2-6-2 0 0 3 5 17 30 6 13 6 12 8 12 2 0 2 0 2 0zm68-1c0-3 0-7 0-9 1 0 2-2 2-2 0 0-4-1-8-2l-7-2-5 2c-4 2-6 4-6 4 0 0 11 8 14 10 2 0 3 0 6 0 4 0 4 0 4-1zm10-14c0-1-2-3-4-5-1-2-3-2-38-13-10-3-25-8-33-11-20-6-31-10-35-10-2-1-9-4-16-6-14-4-16-4-19-3-2 1-2 2-1 3 1 1 6 3 38 13 8 2 17 5 20 6 4 1 13 4 21 7 37 11 42 13 53 16 6 2 11 4 12 4 0 0 1 0 2-1z";
const VAN_SHELL = "M98 74.5L107 68Q109.5 66.5 115 66L265 64Q273 63.5 274 68.5L274.5 74.5Z";
/**
 * The side view: the logo's van, scaled into the gauge, its pop-top up or down as the unit reports it.
 * @param {boolean} roofUp
 * @returns {string}
 */
const vanSide = (roofUp) =>
  `<g transform="scale(0.33) translate(-145.5 -129)"><path class="lg-logo" fill-rule="evenodd" ` +
  `d="${VAN_BODY} ${roofUp ? VAN_TENT : VAN_SHELL}"/></g>`;
/**
 * The rear view (our drawing, the logo's solid style) around (0, 0) in gauge units; the raised
 * pop-top shows as the tent above the roof shell.
 * @param {boolean} roofUp
 * @returns {string}
 */
const vanRear = (roofUp) =>
  (roofUp ? `<path d="M-19,-29 L-14,-42 L14,-42 L19,-29 Z" class="lg-roof"/>` : "") + VAN_REAR;
const VAN_REAR =
  `<rect x="-20" y="-29" width="40" height="5" rx="2" class="lg-roof"/>` +
  `<rect x="-28" y="-24" width="56" height="36" rx="7" class="lg-body"/>` +
  `<rect x="-21" y="-19" width="42" height="13" rx="4" class="lg-glass"/>` +
  `<rect x="-27" y="-2" width="7" height="5" rx="1.5" class="lg-light"/>` +
  `<rect x="20" y="-2" width="7" height="5" rx="1.5" class="lg-light"/>` +
  `<rect x="-29" y="12" width="12" height="8" rx="2.5" class="lg-tyre"/>` +
  `<rect x="17" y="12" width="12" height="8" rx="2.5" class="lg-tyre"/>`;

/**
 * One gauge: a fixed scale (ticks every 5° from -40° to +40° on both sides, the level mark at 0°),
 * the van and its axis markers tilted by the angle, the absolute angle below.
 * @param {number} deg the angle in degrees (signed)
 * @param {string} van the van outline (vanSide() / vanRear())
 * @param {string} label "Nick" / "Roll"
 * @returns {HTMLDivElement}
 */
function levelGauge(deg, van, label) {
  const C = 80, FACE = 66;
  const ok = Math.abs(deg) < LEVEL_OK_DEG;
  const tilt = Math.max(-LEVEL_SCALE_DEG, Math.min(LEVEL_SCALE_DEG, deg * LEVEL_TILT_GAIN));
  let ticks = "";
  for (let a = -LEVEL_SCALE_DEG; a <= LEVEL_SCALE_DEG; a += 5) {
    const cls = a === 0 ? "lg-tick zero" : a % 20 === 0 ? "lg-tick major" : "lg-tick";
    const r1 = FACE - (a === 0 ? 14 : a % 20 === 0 ? 10 : 6), r2 = FACE - 2;
    const rad = (a * Math.PI) / 180;
    for (const side of [1, -1]) {   // right, left (mirrored)
      /** @type {(r: number) => string} */
      const x = (r) => (C + side * r * Math.cos(rad)).toFixed(1);
      /** @type {(r: number) => string} */
      const y = (r) => (C - r * Math.sin(rad)).toFixed(1);
      ticks += `<line x1="${x(r1)}" y1="${y(r1)}" x2="${x(r2)}" y2="${y(r2)}" class="${cls}"/>`;
    }
  }
  // SVG rotate() is clockwise; a positive angle lifts the van's left end (front / right side): -tilt.
  const turn = `rotate(${(-tilt).toFixed(2)} ${C} ${C})`;
  const g = document.createElement("div");
  g.className = "lg-gauge";
  g.innerHTML =
    `<svg viewBox="0 0 ${2 * C} ${2 * C}" class="lg-svg${ok ? " ok" : ""}" role="img" ` +
    `aria-label="${label} ${fmtLevel(deg)}">` +
    `<circle cx="${C}" cy="${C}" r="76" class="lg-ring"/>` +
    `<circle cx="${C}" cy="${C}" r="${FACE}" class="lg-face"/>` + ticks +
    `<g transform="${turn}">` +
    `<line x1="${C - FACE + 3}" y1="${C}" x2="${C - FACE + 18}" y2="${C}" class="lg-axis"/>` +
    `<line x1="${C + FACE - 18}" y1="${C}" x2="${C + FACE - 3}" y2="${C}" class="lg-axis"/>` +
    `<g transform="translate(${C} ${C - 2})">${van}</g></g>` +
    `<text x="${C}" y="${C + 44}" class="lg-val">${fmtLevel(deg)}</text></svg>` +
    `<div class="lg-label">${label}</div>`;
  return g;
}

/**
 * The level indicator widget: the pitch (side) and roll (rear) gauges, or — with no reading
 * (ignition off) — the app's own "Signal unavailable" hint.
 * @param {number|null|undefined} roll
 * @param {number|null|undefined} pitch
 * @param {boolean} roofUp the pop-top is open (or part-way); the van is drawn closed otherwise
 * @returns {HTMLDivElement}
 */
function levelGauges(roll, pitch, roofUp) {
  const wrap = document.createElement("div");
  if (roll == null || pitch == null) {
    wrap.className = "lg-hint";
    wrap.textContent = /** @type {string} */ (t("Signal unavailable. Ignition is off."));
    return wrap;
  }
  wrap.className = "lg-wrap";
  // Left to right in the row label's order, "roll / pitch".
  wrap.appendChild(levelGauge(roll, vanRear(roofUp), cap(/** @type {string} */ (t("roll")))));
  wrap.appendChild(levelGauge(pitch, vanSide(roofUp), cap(/** @type {string} */ (t("pitch")))));
  return wrap;
}

/** @returns {boolean} the unit reports the pop-top open or part-way (semantics.roof `position_name`) */
function roofIsUp() {
  const pos = STATE.roof && STATE.roof.position_name;
  return pos === "open" || pos === "middle";
}

// --- Guided BLE pairing wizard --------------------------------------------------------------
// Client owns no state machine of its own: `PAIRING` is just the last polled `/api/pairing`
// snapshot ({state, attempts, error, address} — calictl.pairing.STATE_NAMES/ERR_NAMES), and the
// card renders whichever step that names. `pairingOpen`/`pairingReady` are pure UI toggles.
// Poll at 1 s ONLY while the card is open (a wizard flow expects snappy feedback); the ambient
// dashboard poll stays at 2 s. See .superpowers/sdd/2026-08-31-guided-pairing.
let pairingOpen = false;
let pairingReady = false;              // "I'm on that screen" checkbox
let pairingAdvOpen = false;            // the bonded step's "Advanced" disclosure (calictl only)
// True for the brief window between opening the wizard and the fresh `pairingFetch()` landing,
// but ONLY when the cached PAIRING snapshot might be stale (see openPairingWizard): renders a
// neutral loading step instead of flashing a leftover passkey/bonded/error/etc. step from a
// flow the server has since moved on from (e.g. it restarted while this tab stayed open).
let pairingLoading = false;
/** @type {{state:string, attempts:number, error:string|null, address:string|null, radio_busy?: boolean}|null} */
let PAIRING = null;
let pairingFetched = false;            // the one-off load-time fetch (refreshState) has run
/** @type {ReturnType<typeof setInterval>|null} */
let pairingTimer = null;

async function pairingFetch() {
  try { PAIRING = await api("/api/pairing"); } catch (e) { /* transient -- keep the last snapshot */ }
}

// Terminal snapshot states: no flow is mid-flight, so nothing will change again until the user
// acts (Start/Retry/reset). Polling every 1 s forever after that would just be a background
// fetch loop running even once the user has navigated away.
const PAIRING_TERMINAL_STATES = new Set(["bonded", "error", "idle"]);

let pairingPolling = false;   // a wizard poll is on the wire (one at a time, like refreshState)

function startPairingPoll() {
  if (pairingTimer) return;
  pairingTimer = setInterval(async () => {
    // The satellite's core serves one connection with a backlog of 2: never stack a 1 s poll
    // behind a stalled one (WebKit would; Chromium/Firefox hold same-URL GETs themselves).
    if (pairingPolling) return;
    pairingPolling = true;
    try { await pairingFetch(); } finally { pairingPolling = false; }
    const ae = document.activeElement;
    if (!(ae && ae.id === "pairing-passkey")) {      // don't clobber the user mid-type
      if (wizardView()) render();                    // the card only shows on Home and WiFi setup
    }
    if (PAIRING && PAIRING_TERMINAL_STATES.has(PAIRING.state)) stopPairingPoll();
  }, 1000);
}
function stopPairingPoll() {
  if (pairingTimer) { clearInterval(pairingTimer); pairingTimer = null; }
}

/** @type {Record<string, string>} */
const PAIRING_ERROR_MSG = {
  timeout: "No vehicle found.",
  connect_failed: "Could not connect to the camper unit.",
  pairing_failed: "Pairing was refused.",
  verify_failed: "Could not verify the pairing.",
};

/** @type {Record<string, string>} */
const PAIRING_ERROR_HINT = {
  timeout: "Check that “Gerät verbinden” is open on the unit, that buspi is in range, and that no phone is connected to the unit.",
  connect_failed: "The unit may be asleep, a phone may still hold its single connection, or another app on this Pi keeps Bluetooth scanning. Wake the unit at its panel, disconnect the phone, pause other Bluetooth apps, then try again. An existing pairing is kept.",
  pairing_failed: "Wrong passcode, or the unit left pairing mode. Reopen “Gerät verbinden” on the unit and try again.",
  verify_failed: "The pairing was made but the unit did not answer. Try again; if it repeats, unpair and pair again.",
};

// Global constraint: never show a raw error enum to the user (only the four codes above are
// mapped; anything else -- a future code this build doesn't know about yet -- falls back to this
// translated generic message, never to the bare `perr` string).
const PAIRING_ERROR_FALLBACK = "Something went wrong. Try again.";

// Errors of the POST /api/pairing REQUEST itself (web.py), as opposed to the flow's own error in
// a snapshot. Unmapped codes fall back to "Pairing request failed" (translated) in pairingAction.
/** @type {Record<string, string>} */
const PAIRING_REQUEST_ERROR_MSG = {
  bad_passkey: "Enter the 6-digit passcode shown on the unit.",
  confirm_required: "Confirm the unpair first.",
  pairing_failed: "The daemon did not answer the pairing request. Try again.",
  busy: CMD_ERRORS.busy,   // the satellite: start/reset while a control command holds the link (409)
};

// The satellite's own advice where the buspi text names the Pi, its daemon or its config: the wizard
// is the same, only these device-specific hints differ (R_FW_PAIRING_WIZARD).
/** @type {Record<string, string>} */
const SAT_PAIRING_ERROR_HINT = {
  timeout: "Check that “Gerät verbinden” is open on the unit, that the satellite is in range, and that no phone is connected to the unit.",
  connect_failed: "The unit may be asleep, or a phone or a Raspberry Pi with calictl may still hold its single connection. Wake the unit at its panel, disconnect the phone, then try again. An existing pairing is kept.",
};

/**
 * @param {string} action
 * @param {string} [value]
 * @param {boolean} [confirmFlag]
 */
async function pairingAction(action, value, confirmFlag) {
  if (action === "start" || action === "reset") startPairingPoll();  // re-arm: a new flow begins
  /** @type {Record<string, any>} */
  const body = { action };
  if (value !== undefined) body.value = value;
  if (confirmFlag) body.confirm = true;
  let res;
  try {
    res = await api("/api/pairing", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  } catch (e) { toast("Pairing request failed", "error"); return; }
  if (res && res.state) PAIRING = res;              // a real snapshot -> adopt it
  // never a raw error enum: known request errors get their own text, anything else the generic one
  else toast(PAIRING_REQUEST_ERROR_MSG[(res && res.error) || ""] || "Pairing request failed", "warn");
  render();
}

/** The screens that show the pairing wizard's card (the hotspot pairs from WiFi setup, no home WiFi). */
const wizardView = () => view === "home" || view === "wifisetup";

// Open the wizard on demand (from the topbar context menu, or auto on true first-run).
async function openPairingWizard() {
  pairingOpen = true;
  // Render synchronously (no visible delay opening the wizard) ONLY when that's safe: no cached
  // snapshot yet, or the cached one was already `idle` -- either way it matches what a fresh
  // fetch would show anyway. Any OTHER cached state (waiting_passkey/pairing/verifying/bonded/
  // error/…) could be stale left over from a flow the server has since moved on from -- e.g. the
  // daemon restarted while this tab stayed open -- so show a neutral loading step instead of
  // flashing it, until the fetch below lands a fresh snapshot.
  pairingLoading = !!(PAIRING && PAIRING.state !== "idle");
  if (!wizardView()) goto("home");   // the card renders on Home (and WiFi setup) only
  else render();                       // already home: show the card now (idle checklist, or the
                                        // loading step) instead of leaving the click waiting on
                                        // the network round-trip below
  showPairingCard();
  await pairingFetch();
  pairingLoading = false;
  startPairingPoll();
  render();
  showPairingCard();
}

// The card renders at the top of Home; bring it into view however far the user had scrolled (H2).
function showPairingCard() {
  const c = document.getElementById("pairing-card");
  if (c) c.scrollIntoView({ block: "start" });
}

// The one unpair action (Device status and the wizard's button): one confirm, then the wizard guides
// straight into pairing again (idle step) while the reset runs.
async function unpair() {
  if (!confirm(/** @type {string} */ (t("Unpair the camper unit? Data stops until you pair again.")))) return;
  if (!pairingOpen || !wizardView()) await openPairingWizard();   // from Device status: Home, at the top
  pairingAction("reset", undefined, true);
}

// The guided-pairing card. UX: invisible in the normal paired+online flow — it renders ONLY
// while open (Device status, the unpaired banner, the #pair link), always at the top of Home, so a
// working installation never shows pairing chrome.
function pairingCard() {
  if (!pairingOpen) return null;
  const card = document.createElement("div");
  card.className = "card"; card.id = "pairing-card";
  const head = document.createElement("div"); head.className = "row";
  const hlbl = document.createElement("span"); hlbl.className = "lbl"; hlbl.textContent = /** @type {string} */ (t("Pair the camper unit"));
  head.appendChild(hlbl);
  const closeBtn = document.createElement("button"); closeBtn.type = "button"; closeBtn.className = "btn";
  closeBtn.textContent = /** @type {string} */ (t("Close"));
  closeBtn.onclick = () => { pairingOpen = false; pairingLoading = false; stopPairingPoll(); render(); };
  head.appendChild(closeBtn);
  card.appendChild(head);

  if (pairingLoading) {
    // The cached PAIRING snapshot might be stale (see openPairingWizard) -- show a neutral
    // loading step instead of a leftover passkey/bonded/error/etc. step from an earlier flow.
    const row = document.createElement("div"); row.className = "row";
    row.appendChild(spinner());
    const lbl = document.createElement("span"); lbl.className = "lbl";
    lbl.textContent = /** @type {string} */ (t("Loading…"));
    row.appendChild(lbl);
    card.appendChild(row);
    return card;
  }

  const p = PAIRING || { state: "idle", attempts: 0, error: null, address: null };
  if (p.radio_busy) {
    const busy = document.createElement("div"); busy.className = "warn"; busy.id = "pairing-radio-busy";
    busy.textContent = /** @type {string} */ (t("Another app on this Pi keeps Bluetooth scanning — pairing will likely fail until it stops (for example the Home Assistant Bluetooth integration)."));
    card.appendChild(busy);
  }
  const sat = satellite();
  if (p.state === "idle") {
    const steps = document.createElement("ol"); steps.className = "note";
    for (const s of [
      sat ? "On the camper unit open Einstellungen → Bluetooth → Gerät verbinden. It shows “Passcode: ---” until the satellite connects."
        : "On the camper unit open Einstellungen → Bluetooth → Gerät verbinden. It shows “Passcode: ---” until buspi connects.",
      "Close the California On Tour app while pairing — its reconnects can collide with the passkey window.",
      sat ? "If a Raspberry Pi with calictl runs near the van, stop it during pairing — its Bluetooth activity can collide with the passkey window."
        : "Stop other Bluetooth scanners on this Pi during pairing (for example the Home Assistant Bluetooth integration).",
    ]) {
      const li = document.createElement("li");
      li.textContent = /** @type {string} */ (t(s));
      steps.appendChild(li);
    }
    card.appendChild(steps);
    const crow = document.createElement("label"); crow.className = "row";
    const cb = document.createElement("input"); cb.type = "checkbox"; cb.id = "pairing-ready";
    cb.checked = pairingReady;
    cb.onchange = () => { pairingReady = cb.checked; render(); };
    const clbl = document.createElement("span"); clbl.textContent = /** @type {string} */ (t("I'm on that screen"));
    crow.append(cb, clbl);
    card.appendChild(crow);
    const btns = document.createElement("div"); btns.className = "btnrow";
    const startBtn = document.createElement("button"); startBtn.type = "button"; startBtn.className = "btn";
    startBtn.textContent = /** @type {string} */ (t("Pair now")); startBtn.disabled = !pairingReady;
    startBtn.onclick = () => pairingAction("start");
    btns.appendChild(startBtn);
    card.appendChild(btns);
  } else if (p.state === "scanning" || p.state === "connecting") {
    const row = document.createElement("div"); row.className = "row";
    row.appendChild(spinner());
    const lbl = document.createElement("span"); lbl.className = "lbl";
    lbl.textContent = /** @type {string} */ (t(p.state === "scanning" ? "Searching…" : "Connecting to the unit…"));
    row.appendChild(lbl);
    card.appendChild(row);
    const btns = document.createElement("div"); btns.className = "btnrow";
    const cancelBtn = document.createElement("button"); cancelBtn.type = "button"; cancelBtn.className = "btn";
    cancelBtn.textContent = /** @type {string} */ (t("Cancel"));
    cancelBtn.onclick = () => pairingAction("cancel");
    btns.appendChild(cancelBtn);
    card.appendChild(btns);
  } else if (p.state === "waiting_passkey") {
    const instr = document.createElement("div"); instr.className = "note";
    instr.textContent = /** @type {string} */ (t("Enter the passcode now shown on the camper unit (it replaced ---). A fresh code each attempt."));
    card.appendChild(instr);
    const row = document.createElement("div"); row.className = "row";
    const inp = document.createElement("input");
    inp.type = "text"; inp.inputMode = "numeric"; inp.pattern = "[0-9]{6}"; inp.maxLength = 6;
    inp.id = "pairing-passkey";
    inp.placeholder = "123456";
    row.appendChild(inp);
    const sendBtn = document.createElement("button"); sendBtn.type = "button"; sendBtn.className = "btn";
    sendBtn.textContent = /** @type {string} */ (t("Send"));
    sendBtn.onclick = () => {
      const v = inp.value.trim();
      if (!/^[0-9]{6}$/.test(v)) { toast("The passcode has 6 digits", "warn"); return; }
      pairingAction("passkey", v);
    };
    row.appendChild(sendBtn);
    card.appendChild(row);
  } else if (p.state === "pairing" || p.state === "verifying" || p.state === "resetting") {
    const row = document.createElement("div"); row.className = "row";
    row.appendChild(spinner());
    const lbl = document.createElement("span"); lbl.className = "lbl";
    lbl.textContent = /** @type {string} */ (t(p.state === "pairing" ? "Pairing…" : p.state === "verifying" ? "Verifying…" : "Resetting…"));
    row.appendChild(lbl);
    card.appendChild(row);
  } else if (p.state === "bonded") {
    const ok = document.createElement("div"); ok.className = "note";
    ok.textContent = p.address ? (t("✓ Paired — ") + p.address) : /** @type {string} */ (t("paired — address cache unavailable (see logs)"));
    card.appendChild(ok);
    if (p.address && sat) {
      const saved = document.createElement("div"); saved.className = "note";
      saved.textContent = /** @type {string} */ (t("Saved on the satellite — survives a restart. No further action needed."));
      card.appendChild(saved);
    } else if (p.address) {
      const saved = document.createElement("div"); saved.className = "note";
      saved.textContent = /** @type {string} */ (t("Saved to the daemon — survives a reboot. No further action needed."));
      card.appendChild(saved);
      // jargon off the happy path (L4): the env line only matters when the Pi is reflashed
      const adv = document.createElement("details"); adv.className = "note";
      adv.open = pairingAdvOpen;
      adv.ontoggle = () => { pairingAdvOpen = adv.open; };   // survives a re-render
      const sum = document.createElement("summary"); sum.textContent = /** @type {string} */ (t("Advanced"));
      const code = document.createElement("code"); code.textContent = "CALICTL_ADDR=" + p.address;
      const note = document.createElement("div");
      note.textContent = /** @type {string} */ (t("Only needed if you reflash the Pi (a fresh install wipes the saved pairing). Set it in /etc/buspi/calictl.env to survive that."));
      adv.append(sum, code, note);
      card.appendChild(adv);
    }
  } else if (p.state === "error") {
    const errRow = document.createElement("div"); errRow.className = "warn";
    const perr = /** @type {string} */ (p.error || "");
    errRow.textContent = /** @type {string} */ (t("Error: "))
      + (/** @type {string} */ (t(PAIRING_ERROR_MSG[perr])) || /** @type {string} */ (t(PAIRING_ERROR_FALLBACK)));
    card.appendChild(errRow);
    const hint = (sat && SAT_PAIRING_ERROR_HINT[perr]) || PAIRING_ERROR_HINT[perr];
    if (hint) {
      const hintRow = document.createElement("div"); hintRow.className = "note";
      hintRow.textContent = /** @type {string} */ (t(hint));
      card.appendChild(hintRow);
    }
    const btns = document.createElement("div"); btns.className = "btnrow";
    const retryBtn = document.createElement("button"); retryBtn.type = "button"; retryBtn.className = "btn";
    retryBtn.textContent = /** @type {string} */ (t("Try again"));
    retryBtn.onclick = () => pairingAction("start");
    btns.appendChild(retryBtn);
    card.appendChild(btns);
  }
  // Unpair: available whenever a bond exists to lose, on top of whatever step is showing above
  // (e.g. still visible next to an "error" from a re-pair attempt on an already-bonded unit).
  if (p.state !== "resetting" && (p.state === "bonded" || p.address)) {
    const rbtns = document.createElement("div"); rbtns.className = "btnrow";
    const resetBtn = document.createElement("button"); resetBtn.type = "button"; resetBtn.className = "btn";
    resetBtn.textContent = /** @type {string} */ (t("Unpair…"));
    resetBtn.onclick = () => unpair();
    rbtns.appendChild(resetBtn);
    card.appendChild(rbtns);
  }
  return card;
}

function render() {
  backEl.hidden = view === "home";
  backEl.onclick = back;
  stopClockTicker();          // any live vehicle-clock ticker belongs to the old DOM; restarted on re-render
  app.innerHTML = "";
  const sp = sessionToggle();
  if (sp) app.appendChild(sp);
  paintLink();   // the asleep / unreachable banner lives in #conn, above #app
  // WiFi setup explains the hotspot once itself (its Camper unit box), not as a read-only warning
  if (readOnly() && STATE._meta && !STATE._meta.restored && view !== "wifisetup") {
    const b = document.createElement("div");
    b.className = "readonly";
    b.textContent = /** @type {string} */ (satellite() ? t("Satellite — display only")
      : t("🔒 Read-only — control is disabled on this daemon."));
    app.appendChild(b);
  }
  const fw = STATE._meta && STATE._meta.firmware;
  if (fw && fw.untested) {
    const b = document.createElement("div");
    b.className = "fwwarn";
    b.textContent = tf("⚠ Untested firmware — unit reports amb {amb} · comm {comm} (validated on {tested}); readings may be off.",
      { amb: fw.amb_sw_version ?? "?", comm: fw.comm_version ?? "?", tested: fw.tested });
    app.appendChild(b);
  }
  const anc = (STATE._meta && STATE._meta.anchors) || [];
  if (anc.length) {
    const b = document.createElement("div");
    b.className = "fwwarn";
    b.textContent = tf("⚠ Implausible reading(s): {list} — possible decode drift.", { list: anc.join("; ") });
    app.appendChild(b);
  }
  if (view === "home") renderDashboard();
  else if (view === "devicestatus") renderDeviceStatus();
  else if (SAT_VIEWS.includes(view)) renderSat();
  else renderFeature(view);
  roofViewSync();
}

// --- the in-app Device-status screen (menu, both flavors) -------------------------------------
// One card per device (UX review 3.2): the Camper unit (from `_meta` + the load-time /api/pairing
// snapshot, with Pair…/Unpair…), then the satellite (`_meta.sat`, adaptSatellite passthrough, and the
// link to its Satellite settings screen) or, on calictl, the buspi backend (no settings page).
/** @param {number} ms @returns {string} */
function uptimeText(ms) {
  const m = Math.floor(ms / 60000);
  if (m < 60) return tf("{m} min", { m: m });
  const h = Math.floor(m / 60);
  if (h < 48) return tf("{h} h {m} min", { h: h, m: m % 60 });
  return tf("{d} d {h} h", { d: Math.floor(h / 24), h: h % 24 });
}

/** The SD log's line. @param {SatSd} sd @returns {string} */
function sdText(sd) {
  /** @param {number|null} mb */
  const gb = (mb) => ((mb || 0) / 1024).toFixed(1);
  const parts = [sd.state === "ok" ? tf("SD {free} of {total} GB free", { free: gb(sd.free_mb), total: gb(sd.total_mb) })
    : /** @type {string} */ (t(sd.state === "error" ? "SD card error" : "No SD card"))];
  if (sd.state === "ok" && sd.last_seq != null) parts.push(tf("log #{seq}", { seq: sd.last_seq }));
  if (sd.dropped > 0) parts.push(tf("{n} records dropped", { n: sd.dropped }));
  return parts.join(" · ");
}

/** RAM / PSRAM / CPU of the satellite; "" when it reports none. @param {SatRes} r @returns {string} */
function resText(r) {
  const parts = [];
  if (r.heap_free_kb != null) parts.push(tf("RAM {free} KB free (lowest {min})", { free: r.heap_free_kb, min: r.heap_min_kb ?? "?" }));
  if (r.psram_free_kb != null) parts.push(tf("PSRAM {free} KB free", { free: r.psram_free_kb }));
  if (r.cpu_pct != null) parts.push(tf("CPU {pct} %", { pct: r.cpu_pct }));
  return parts.join(" · ");
}

/** The satellite's own battery; "" while the percent is
 * unknown (no power chip). @param {SatBattery} b @returns {string} */
function batteryText(b) {
  if (b.pct == null) return "";
  const parts = [b.pct + " %"];
  if (b.mv != null) parts.push((b.mv / 1000).toFixed(2) + " V");
  if (b.usb != null) parts.push(/** @type {string} */ (t(b.usb ? (b.charging ? "on USB, charging" : "on USB") : "on battery")));
  if (!b.usb && b.runtime_min != null) {
    const h = Math.floor(b.runtime_min / 60), m = b.runtime_min % 60;
    parts.push(tf("~{time} left", { time: h ? tf("{h} h {m} min", { h: h, m: m }) : tf("{m} min", { m: m }) }));
  }
  return parts.join(" · ");
}

// device.time -> the Clock row (R_FW_TIME)
/** @type {Record<string, string>} */
const SAT_CLOCK = { synced: "synced (SNTP) · boot {boot}", waiting: "not synced yet · boot {boot}",
  none: "no source · boot {boot}", unit: "from the camper unit · boot {boot}" };

// The camper unit's connection, in linkState()'s words (the pill + banner): online / asleep / not paired
/** @type {Record<ReturnType<typeof linkState>, string>} every link state (tsc checks the set) */
const LINK_WORD = { live: "online", asleep: "asleep", never: "asleep", unpaired: "not paired",
  connecting: "connecting…", unreachable: "unreachable", satellite: "🛰 Satellite mode", rest: "power saving",
  redial: "reconnecting…" };

/** A Device-status card: its heading names the device (UX review 3.2: one card per device).
 * @param {string} title @returns {HTMLElement} */
function dsCard(title) {
  const card = document.createElement("section");
  card.className = "card dcard";
  const h = document.createElement("h2"); h.textContent = title;
  card.appendChild(h);
  app.appendChild(card);
  return card;
}

/** A button row for a Device-status card. @param {HTMLElement} card @param {[string, () => any, string?][]} btns */
function dsButtons(card, btns) {
  const row = document.createElement("div"); row.className = "btnrow";
  for (const [label, onclick, cls] of btns) {
    const b = document.createElement("button"); b.type = "button";
    b.className = "btn" + (cls ? " " + cls : "");
    b.textContent = label; b.onclick = onclick;
    row.appendChild(b);
  }
  card.appendChild(row);
}

function renderDeviceStatus() {
  titleEl.textContent = /** @type {string} */ (t("Device status"));
  const m = STATE._meta || /** @type {Meta} */ ({});
  const unit = dsCard(/** @type {string} */ (t("Camper unit")));
  unit.appendChild(sumRow(/** @type {string} */ (t("Connection")), /** @type {string} */ (t(LINK_WORD[linkState()]))));
  unit.appendChild(sumRow(/** @type {string} */ (t("Last update")), agoText(m.age_s)));
  // "Not paired" only from a fetched snapshot that says so; a failed/absent fetch is unknown ("—").
  const addr = PAIRING && PAIRING.address;
  unit.appendChild(sumRow(/** @type {string} */ (t("Pairing")),
    PAIRING ? /** @type {string} */ (t(addr ? "Paired ✓" : "Not paired")) : "—"));
  if (addr) {   // the bonded MAC: jargon, behind a disclosure, never shown by default
    const det = document.createElement("details"); det.className = "note";
    const sum = document.createElement("summary"); sum.textContent = /** @type {string} */ (t("Details"));
    const code = document.createElement("code"); code.textContent = addr;
    det.append(sum, code);
    unit.appendChild(det);
  }
  const fw = m.firmware;
  if (fw && (fw.amb_sw_version != null || fw.comm_version != null))
    unit.appendChild(sumRow(/** @type {string} */ (t("Unit firmware")),
      tf("amb {amb} · comm {comm}", { amb: fw.amb_sw_version ?? "?", comm: fw.comm_version ?? "?" })));
  // the same handlers + confirm as the wizard; only the one that applies
  dsButtons(unit, addr ? [[/** @type {string} */ (t("Unpair…")), () => unpair(), "danger"]]
    : [[/** @type {string} */ (t("Pair…")), () => openPairingWizard()]]);

  const sat = m.sat;
  if (sat) {   // the ESP satellite (its link to the unit is the Camper unit's Connection above)
    const card = dsCard(/** @type {string} */ (t("Satellite")));
    if (sat.fw) card.appendChild(sumRow(/** @type {string} */ (t("Firmware")), sat.fw));
    const w = sat.wifi;
    if (w && w.ssid) card.appendChild(sumRow(/** @type {string} */ (t("WiFi")),
      [w.ssid, w.ip, typeof w.rssi === "number" ? w.rssi + " dBm" : ""].filter(Boolean).join(" · ")));
    if (sat.uptime_ms != null)
      card.appendChild(sumRow(/** @type {string} */ (t("Uptime")), uptimeText(sat.uptime_ms)));
    const tm = sat.time;   // the clock the SD log stamps with (R_FW_TIME), with the boot number
    if (tm) card.appendChild(sumRow(/** @type {string} */ (t("Clock")), tf(SAT_CLOCK[tm.source === "none" || tm.source === "unit"
      ? tm.source : tm.synced ? "synced" : "waiting"], { boot: tm.boot })));
    if (sat.sd) card.appendChild(sumRow(/** @type {string} */ (t("Storage")), sdText(sat.sd)));
    const batt = sat.battery && batteryText(sat.battery);
    if (batt) card.appendChild(sumRow(/** @type {string} */ (t("Battery")), batt));
    const res = sat.res && resText(sat.res);
    if (res) card.appendChild(sumRow(/** @type {string} */ (t("Resources")), res));
    dsButtons(card, [[/** @type {string} */ (t("Satellite settings ›")), () => goto("satsettings")]]);
  } else {   // calictl on the Pi: which backend serves this page, and whether it may write
    const card = dsCard("buspi");
    card.appendChild(sumRow(/** @type {string} */ (t("Backend")), backendName()));
    card.appendChild(sumRow(/** @type {string} */ (t("Control writes")), /** @type {string} */ (t(m.read_only ? "off" : "on"))));
  }
}

// --- Satellite settings + WiFi setup (the ESP satellite only; one-web-UI design, U1) -------------
// The satellite's own settings as two screens of this app: `satsettings` (Device status' Satellite
// card, `#satsettings`) and `wifisetup` (the landing while the satellite runs its setup hotspot: the
// join form + the pairing entry). Unlike the other screens they keep their DOM across polls (satDom):
// a typed password, an open confirm step or a sensor name must survive the 2 s poll, so a poll only
// updates them in place (satUpdate). Their endpoints are the firmware's (web.c, firmware/docs):
// GET/POST /api/wifi, /api/sensors; POST /api/auto_camper, /api/display, /api/ota, /api/ux_setting,
// /api/experimental, /api/pi_wake, /api/pi_halt, /api/pi_wake_setting, /api/ble — reached through satFetch, never api() (which refuses them by design).
const SAT_VIEWS = ["satsettings", "wifisetup"];
/** @type {{view: string, lang: string, root: HTMLElement}|null} the open satellite screen's DOM */
let satDom = null;
let landChecked = false;   // the setup landing is decided once per load (on the first `_meta`)
const SAT_STATION_ONLY = "needs the home WiFi";   // the firmware answers 403 setup_mode over the hotspot

/** t() for a string the caller knows is a key (a literal): never null. @param {string} s @returns {string} */
const T = (s) => /** @type {string} */ (t(s));

/**
 * An element with attributes and children (null/undefined children and attributes are skipped).
 * @template {keyof HTMLElementTagNameMap} K
 * @param {K} tag
 * @param {Record<string, string|null|undefined>|null} [at]
 * @param {...(Node|string|null|undefined)} kids
 * @returns {HTMLElementTagNameMap[K]}
 */
function h(tag, at, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(at || {})) if (v != null) e.setAttribute(k, v);
  for (const c of kids) if (c != null) e.append(c);
  return e;
}
/** @param {string} label @param {string} [cls] @param {string} [id] */
const satBtn = (label, cls, id) => h("button", { type: "button", class: "btn" + (cls ? " " + cls : ""), id: id }, label);
/** A link that shows its address. @param {string} href */
const satLink = (href) => h("a", { href: href }, href);
/** The open satellite screen's element (built with every id this code uses). @param {string} id @returns {HTMLElement} */
const sq = (id) => /** @type {HTMLElement} */ (/** @type {NonNullable<typeof satDom>} */ (satDom).root.querySelector("#" + id));
/** @param {string} id */
const sqInput = (id) => /** @type {HTMLInputElement} */ (sq(id));

/**
 * GET (no body) or POST a satellite endpoint; refuses off the satellite. A hung connection ends after
 * `ms` (the phone left the network, a dead Tailscale peer).
 * @param {string} path @param {object} [body] @param {number} [ms]
 * @returns {Promise<{status: number, body: any}>}
 */
async function satFetch(path, body, ms = POLL_TIMEOUT_MS) {
  if (!satellite()) throw new Error("not a satellite: " + path);
  const ac = new AbortController(), timer = setTimeout(() => ac.abort(), ms);
  try {
    const r = await fetch(path, body === undefined ? { cache: "no-store", signal: ac.signal }
      : { method: "POST", cache: "no-store", signal: ac.signal, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    return { status: r.status, body: await r.json() };
  } finally { clearTimeout(timer); }
}

/** A Satellite-settings card. @param {string} id @param {string|null} title @param {...(Node|null)} kids */
const satCard = (id, title, ...kids) => h("section", { class: "card dcard", id: id }, title ? h("h2", null, title) : null, ...kids);
/** The one on/off row: "<id>-status" left, the "<id>-toggle" switch right, named by the status. @param {string} id */
const satSwitchRow = (id) => h("div", { class: "row" }, h("span", { class: "lbl", id: id + "-status" }),
  h("button", { type: "button", class: "switch", role: "switch", id: id + "-toggle", "aria-labelledby": id + "-status" }));
/** A row's error line. @param {string} id */
const satMsg = (id) => h("p", { class: "warn", id: id });

/** The WiFi box (both screens): online "Connected to <ssid> · ip · dBm" + Change network…; the join
 * form over the hotspot, after Change network…, or while a hotspot join runs; the Switching panel. */
function satWifiBox() {
  const change = satBtn(T("Change network…"), "", "wifi-change");
  change.onclick = () => { wifiOpen = true; satUpdate(); sq("ssid").focus(); };
  const connect = satBtn(T("Connect"), "primary", "connect");
  connect.onclick = satConnect;
  const rescan = satBtn(T("Search again"), "", "rescan");
  rescan.onclick = satScan;
  const ssid = h("select", { id: "ssid" });
  ssid.onchange = satOther;
  const show = h("input", { id: "psk-show", type: "checkbox" });
  show.onchange = () => { sqInput("psk").type = show.checked ? "text" : "password"; };
  return satCard("setup", null, h("h2", { id: "setup-title" }), h("p", { id: "wifi-now" }),
    h("div", { class: "btnrow" }, change),
    h("div", { id: "wifi-form" },
      h("p", { class: "note" }, T("Choose a network and enter its password.")),
      h("label", { for: "ssid" }, T("Network")), ssid,
      h("p", { class: "note", id: "scan-empty", hidden: "" }, T("No networks listed — type the network's name.")),
      h("input", { id: "ssid-name", autocomplete: "off", autocapitalize: "none", spellcheck: "false", hidden: "",
        placeholder: T("Network name"), "aria-label": T("Network name") }),
      h("label", { for: "psk" }, T("Password")), h("input", { id: "psk", type: "password", autocomplete: "off" }),
      h("label", { class: "row" }, show, h("span", null, T("Show password"))),
      h("div", { class: "btnrow acts", id: "wifi-acts" }, connect),
      h("div", { class: "btnrow" }, rescan)),
    h("div", { id: "wifi-switch", hidden: "" }),
    h("p", { class: "note", id: "setup-msg", "aria-live": "polite" }));
}

/** The Camper unit box: paired or not; Device status holds Pair…/Unpair… (WiFi setup also shows the
 * unpaired banner / the wizard right there, over the hotspot: renderSat). */
function satPairBox() {
  const ds = satBtn(T("Device status ›"));
  ds.onclick = openDeviceStatus;
  return satCard("pair", T("Camper unit"), h("p", { id: "pair-status" }),
    view === "wifisetup" ? h("p", { class: "note" }, T("Controls work only on your home WiFi — not over the setup hotspot")) : null,
    h("div", { class: "btnrow" }, ds));
}

/** @param {string} v the satellite screen to build @returns {NonNullable<typeof satDom>} */
function buildSat(v) {
  joining = null; wifiOpen = false; formShown = false; sensOn = false; sensBusy = false;
  if (scanTimer !== null) { clearTimeout(scanTimer); scanTimer = null; }
  const root = h("div", { class: "sat" });
  satDom = { view: v, lang: LANG, root: root };   // sq() works from here on
  root.append(satWifiBox(), satPairBox());
  if (v !== "satsettings") return satDom;
  const idle = h("select", { id: "ble-idle" });
  for (const n of BLE_IDLE_MIN) idle.append(idleOpt(n));
  idle.onchange = () => satBleSet("ble-idle", "idle_min");
  const rest = h("select", { id: "ble-rest" });
  for (const n of BLE_REST_MIN) rest.append(restOpt(n));
  rest.onchange = () => satBleSet("ble-rest", "rest_poll_min");
  sq("pair").append(h("div", { class: "row", id: "ble-idle-row" }, h("label", { class: "lbl", for: "ble-idle" }, T("Connection")), idle),
    h("p", { class: "note", id: "ble-idle-note" }, T("Between reads the satellite lets go of the unit; opening this app connects again within seconds.")),
    h("div", { class: "row", id: "ble-rest-row" }, h("label", { class: "lbl", for: "ble-rest" }, T("Read while in power-save mode")), rest),
    satMsg("ble-msg"));
  const toggle = (/** @type {string} */ id, /** @type {string} */ path, /** @type {string} */ err, /** @type {string} */ key, /** @type {string} */ msgId) =>
    { sq(id + "-toggle").onclick = () => satToggle(path, id, err, key, msgId); };
  root.append(satCard("ac", T("Restore camping after you park"), satSwitchRow("ac"), satMsg("ac-msg")));
  const wake = satBtn(T("Wake Pi"), "", "pw-wake"), halt = satBtn(T("Shut down Pi"), "", "pw-halt");
  wake.onclick = satPiWake;
  const hacts = h("div", { class: "btnrow", id: "pw-hacts" }, halt);
  halt.onclick = () => satAsk(hacts, [h("span", null, T("Shut down the Raspberry Pi?"))], T("Shut down Pi"), "danger", satPiHalt, "pw-msg");
  root.append(satCard("pw", T("Raspberry Pi"), h("p", { class: "note" }, T("Wakes the Raspberry Pi through pin 5 and shuts it down through pin 11.")),
    satSwitchRow("pw"), h("div", { class: "btnrow" }, wake), hacts,
    satPiSelect("pw-after", "Shut down after parking", PI_HALT_MIN),
    satPiSelect("pw-soc", "Shut down at low battery", PI_HALT_SOC),
    h("p", { class: "note", id: "pw-last" }), h("p", { class: "note", id: "pw-halt-st" }),
    h("p", { class: "note", id: "pw-msg", "aria-live": "polite" })));
  const dn = satBtn("−", "", "scr-dn"), up = satBtn("+", "", "scr-up");
  dn.setAttribute("aria-label", T("Darker")); up.setAttribute("aria-label", T("Brighter"));
  dn.onclick = () => satScrStep(-5); up.onclick = () => satScrStep(5);
  root.append(satCard("scr", T("Screen"), h("p", { id: "scr-status" }),
    ...SAT_SCR_ROWS.map(([id]) => satSwitchRow("scr-" + id)),
    h("div", { class: "row" }, h("span", { class: "lbl", id: "scr-off" }), h("span", { class: "step" }, dn, up)),
    satMsg("scr-msg")));
  const check = satBtn(T("Check for update"), "", "fw-check"), install = satBtn("", "primary", "fw-install");
  check.onclick = () => satOta({ action: "check" }); install.onclick = () => satOta({ action: "install" });
  const ch = h("select", { id: "fw-channel" }, h("option", { value: "stable" }, T("stable (tested)")),
    h("option", { value: "latest" }, T("latest (every merge)")));
  ch.onchange = () => satOta({ channel: ch.value });
  root.append(satCard("fw", T("Firmware update"), h("p", { id: "fw-status" }), h("div", { class: "btnrow" }, install, check),
    h("div", { class: "row", id: "fw-ch-row" }, h("label", { class: "lbl", for: "fw-channel" }, T("Channel")), ch), satMsg("fw-msg")));
  root.append(h("h2", { class: "sec" }, T("Advanced")), satCard("ux", null, satSwitchRow("ux"), satMsg("ux-msg")),
    satCard("exp", T("Experimental"), h("p", { class: "note" }, T("Enables the sensor scan and setup. Stable features do not depend on it.")),
      satSwitchRow("exp"), satMsg("exp-msg")),
    satCard("sens", T("Sensors"), h("p", { class: "note" }, T("Nearby sensors appear below; add the ones that are yours.")),
      h("div", { id: "sens-cfg" }), h("div", { id: "sens-seen" }), h("p", { class: "warn", id: "sens-msg", "aria-live": "polite" })));
  toggle("ac", "/api/auto_camper", "Couldn't change auto camper", "enabled", "ac-msg");
  toggle("pw", "/api/pi_wake_setting", "Couldn't change the Pi wake", "enabled", "pw-msg");
  toggle("ux", "/api/ux_setting", "Couldn't change usage data", "enabled", "ux-msg");
  toggle("exp", "/api/experimental", "Couldn't change the switch", "sensors", "exp-msg");
  for (const [id, k] of SAT_SCR_ROWS) toggle("scr-" + id, "/api/display", "Couldn't change the screen setting", k, "scr-msg");
  return satDom;
}

function renderSat() {
  titleEl.textContent = T(view === "wifisetup" ? "WiFi setup" : "Satellite settings");
  if (!satDom || satDom.view !== view || satDom.lang !== LANG) buildSat(view);
  app.appendChild(/** @type {NonNullable<typeof satDom>} */ (satDom).root);
  if (view === "wifisetup") {   // the wizard (or its banner), right there over the hotspot
    const pc = pairingCard();
    if (pc) app.appendChild(pc); else unpairedBanner();
  }
  satUpdate();
}

/** Fill the open satellite screen from STATE (every poll; never rebuilds). */
function satUpdate() {
  const m = STATE._meta, s = m && m.sat;
  if (!satDom || !m || !s || !s.wifi) return;
  const w = s.wifi, st = w.mode === "station";
  satWifi = w;
  satRenderWifi(w);
  sq("pair-status").textContent = T(m.paired ? "Paired ✓" : "Not paired");
  sq("pair-status").hidden = !m.paired && satDom.view === "wifisetup";   // there the unpaired banner says it
  if (satDom.view !== "satsettings") return;
  sq("ac").hidden = !s.auto_camper;
  if (s.auto_camper) {
    const a = s.auto_camper;
    satRow("ac", [T(a.enabled ? "Auto camper on" : "Auto camper off"), a.state === "armed" ? T("⟳ will restore on park") : "",
      a.state === "restoring" ? T("⟳ restoring …") : ""], a.enabled, st);
  }
  const ble = s.ble, idleKnown = !!ble && ble.idle_min !== undefined;
  sq("ble-idle-row").hidden = !idleKnown || !st;
  sq("ble-idle-note").hidden = !idleKnown || !st || !(ble && ble.idle_min);
  if (ble && idleKnown) satPiSelectSet("ble-idle", ble.idle_min);
  sq("ble-rest-row").hidden = !ble || ble.rest_poll_min === undefined || !st;
  if (ble) satPiSelectSet("ble-rest", ble.rest_poll_min);
  sq("pw").hidden = !s.pi_wake;
  if (s.pi_wake) {
    const p = s.pi_wake;
    satRow("pw", [T("Wake on ignition") + ": " + T(p.enabled ? "on" : "off")], p.enabled, st);
    sq("pw-wake").hidden = !st;
    sq("pw-last").textContent = p.last_s === null ? ""
      : tf("Last wake: {n} s ago ({reason})", { n: p.last_s, reason: T(p.reason === "ignition" ? "Ignition" : "Manual") });
    satPiHaltUpdate(p, st);
  }
  satRow("ux", [T(m.ux ? "Send usage data: on" : "Send usage data: off")], !!m.ux, st);
  satRow("exp", [T(s.experimental ? "Sensors (experimental): on" : "Sensors (experimental): off")], !!s.experimental, st);
  satRenderScreen(s.display || null, st);
  satRenderFw(s.ota || null, s.fw, st);
  sq("sens").hidden = !s.experimental;
  if (s.experimental && !sensOn) { sensOn = true; satSensPoll(); }
}

/** The on/off row: the status (empty parts dropped; over the hotspot it ends in "needs the home
 * WiFi" and the switch is hidden — every switch is station only). @param {string} id
 * @param {string[]} parts @param {boolean} on @param {boolean} st station mode */
function satRow(id, parts, on, st) {
  const b = /** @type {HTMLButtonElement} */ (sq(id + "-toggle"));
  sq(id + "-status").textContent = [...parts, st ? "" : T(SAT_STATION_ONLY)].filter(Boolean).join(" · ");
  b.hidden = !st;
  b.disabled = false;
  b.setAttribute("aria-checked", String(on));
}

/** POST {<key>: <the opposite>} to a settings endpoint; the next poll shows the satellite's state.
 * @param {string} path @param {string} id @param {string} err @param {string} key @param {string} msgId */
async function satToggle(path, id, err, key, msgId) {
  const b = /** @type {HTMLButtonElement} */ (sq(id + "-toggle")), msg = sq(msgId), on = b.getAttribute("aria-checked") !== "true";
  msg.textContent = "";
  b.disabled = true;   // one POST per tap
  ux({ type: "control", fn: "satellite", control: uxId(id) });
  let ok = false;
  try { ok = !!(await satFetch(path, { [key]: on })).body.ok; } catch (e) { /* ok stays false */ }
  if (!ok) msg.textContent = T(err);
  await refreshState(true);
  if (satDom) b.disabled = false;
}

/** Wake Pi (R_FW_PI_WAKE): POST /api/pi_wake, then what the satellite said. */
async function satPiWake() {
  const b = /** @type {HTMLButtonElement} */ (sq("pw-wake")), msg = sq("pw-msg");
  msg.textContent = "";
  b.disabled = true;   // one pulse per tap
  ux({ type: "control", fn: "satellite", control: "pi_wake" });
  /** @type {any} */
  let r = null;
  try { r = (await satFetch("/api/pi_wake", {})).body; } catch (e) { /* r stays null */ }
  msg.textContent = !r ? T("No answer from the satellite.") : r.ok ? T("Wake signal sent")
    : tf("Couldn't wake the Pi ({error})", { error: r.error || "" });
  await refreshState(true);
  if (satDom) b.disabled = false;
}

// The Pi halt (R_FW_PI_HALT): the select choices (0 = Off); a stored value not listed is added
const PI_HALT_MIN = [0, 10, 30, 60, 120], PI_HALT_SOC = [0, 20, 30, 40];
/** @type {Record<string, string>} */
const PI_HALT_REASON = { timer: "Timer", battery: "Battery", manual: "Manual" };

/** An option's text: Off, "30 min" (pw-after) or "30 %" (pw-soc). @param {string} id @param {number} v */
const piOpt = (id, v) => h("option", { value: String(v) }, !v ? T("Off") : id === "pw-soc" ? v + " %" : tf("{n} min", { n: v }));

/** A labelled select row posting {key: <number>} to /api/pi_wake_setting.
 * @param {string} id @param {string} label @param {number[]} vals */
function satPiSelect(id, label, vals) {
  const sel = h("select", { id: id });
  for (const v of vals) sel.append(piOpt(id, v));
  sel.onchange = () => satPiSetting(sel, id === "pw-after" ? "halt_after_min" : "halt_soc_pct");
  return h("div", { class: "row", id: id + "-row" }, h("label", { class: "lbl", for: id }, T(label)), sel);
}

// The unit link's idle policy (R_FW_BLE_IDLE): 0 = always connected, else a read every n min while nobody watches
const BLE_IDLE_MIN = [0, 2, 5, 15, 30];
/** @param {number} n */
const idleOpt = (n) => h("option", { value: String(n) }, !n ? T("Always connected") : tf("Read every {n} min", { n: n }));

// The power-save rest's reads (R_FW_BLE_REST): 0 = none, else a read every n min while resting
const BLE_REST_MIN = [0, 15, 30, 60, 120];
/** @param {number} n */
const restOpt = (n) => h("option", { value: String(n) }, !n ? T("Off")
  : n < 120 || n % 60 ? tf("Every {n} min", { n: n }) : tf("Every {n} h", { n: n / 60 }));

/** POST {key: n} (idle_min, rest_poll_min) to /api/ble; a refusal puts the shown value back.
 * @param {string} id the select @param {string} key the /api/ble key */
async function satBleSet(id, key) {
  const sel = /** @type {HTMLSelectElement} */ (sq(id)), msg = sq("ble-msg");
  msg.textContent = "";
  ux({ type: "control", fn: "satellite", control: key });
  let ok = false;
  try { ok = (await satFetch("/api/ble", { [key]: Number(sel.value) })).status === 200; } catch (e) { /* ok stays false */ }
  if (!ok) { msg.textContent = T("Couldn't change the connection"); sel.value = sel.dataset.v || ""; }
  sel.blur();
  await refreshState(true);
}

/** Select the current value (an option added for a value the list lacks; never while the owner picks).
 * @param {string} id @param {number|undefined} v */
function satPiSelectSet(id, v) {
  const sel = /** @type {HTMLSelectElement} */ (sq(id));
  if (v === undefined || document.activeElement === sel) return;
  if (!Array.from(sel.options).some((o) => o.value === String(v)))
    sel.append(id === "ble-idle" ? idleOpt(v) : id === "ble-rest" ? restOpt(v) : piOpt(id, v));
  sel.value = sel.dataset.v = String(v);
}

/** The halt part of the Raspberry Pi box. @param {SatPiWake} p @param {boolean} st */
function satPiHaltUpdate(p, st) {
  const has = p.halt_after_min !== undefined;
  sq("pw-hacts").hidden = sq("pw-after-row").hidden = sq("pw-soc-row").hidden = !has || !st;
  satPiSelectSet("pw-after", p.halt_after_min);
  satPiSelectSet("pw-soc", p.halt_soc_pct);
  const parts = [];
  if (has && p.halt_in_s != null) parts.push(tf("Shutdown in {n} min", { n: Math.ceil(p.halt_in_s / 60) }));
  else if (has && p.halted && p.halt_last_s != null)
    parts.push(tf("Shut down {n} min ago ({reason})", { n: Math.floor(p.halt_last_s / 60), reason: T(PI_HALT_REASON[p.halt_reason || ""] || "Manual") }));
  if (has && p.batt_low) parts.push(T("Battery low"));
  sq("pw-halt-st").textContent = parts.join(" · ");
}

/** POST one halt setting; a refusal puts the shown value back. @param {HTMLSelectElement} sel @param {string} key */
async function satPiSetting(sel, key) {
  const msg = sq("pw-msg");
  msg.textContent = "";
  ux({ type: "control", fn: "satellite", control: key });
  let ok = false;
  try { ok = !!(await satFetch("/api/pi_wake_setting", { [key]: Number(sel.value) })).body.ok; } catch (e) { /* ok stays false */ }
  if (!ok) { msg.textContent = T("Couldn't change the Pi shutdown"); sel.value = sel.dataset.v || ""; }
  sel.blur();
  await refreshState(true);
}

/** Shut down Pi (R_FW_PI_HALT), after the in-page confirm: POST /api/pi_halt, then what the satellite said.
 * @param {() => void} close */
async function satPiHalt(close) {
  ux({ type: "control", fn: "satellite", control: "pi_halt" });
  /** @type {any} */
  let r = null;
  try { r = (await satFetch("/api/pi_halt", {})).body; } catch (e) { /* r stays null */ }
  close();
  sq("pw-msg").textContent = !r ? T("No answer from the satellite.") : r.ok ? T("Shutdown signal sent")
    : tf("Couldn't shut down the Pi ({error})", { error: r.error || "" });
  await refreshState(true);
}

// Screen (R_FW_DISPLAY_WAKE): [id suffix, device.display member = the POST key, label]
const SAT_SCR_ROWS = [["auto", "auto", "Brightness from room light"], ["prox", "prox_wake", "Wake when a hand is near"],
  ["motion", "motion_wake", "Wake on motion"]];
/** @type {Record<string, string>} */
const SAT_SCR_STATE = { on: "on", dim: "dimmed", off: "off" };
let scrOffset = 0;
/** @param {SatDisplay|null} p @param {boolean} st */
function satRenderScreen(p, st) {
  sq("scr").hidden = !p || p.state === null;
  if (!p || p.state === null) return;
  sq("scr-status").textContent = [T(SAT_SCR_STATE[p.state] || p.state), p.brightness_pct + " % (" + p.min + "–" + p.max + ")",
    p.lux === null ? "" : p.lux + " lx", p.sensor === "ok" ? "" : T("no light sensor reading"),
    st ? "" : T(SAT_STATION_ONLY)].filter(Boolean).join(" · ");
  for (const [id, k, l] of SAT_SCR_ROWS) {
    const on = !!(/** @type {Record<string, unknown>} */ (p))[k];
    satRow("scr-" + id, [T(l) + ": " + T(on ? "on" : "off")], on, st);
  }
  scrOffset = p.offset;
  sq("scr-off").textContent = tf("Brightness {n}", { n: (p.offset > 0 ? "+" : "") + p.offset });
  sq("scr-dn").hidden = sq("scr-up").hidden = !st;
}
/** The brightness offset in 5-point steps, -50..+50. @param {number} step */
function satScrStep(step) {
  scrOffset = Math.max(-50, Math.min(50, scrOffset + step));
  sq("scr-off").textContent = tf("Brightness {n}", { n: (scrOffset > 0 ? "+" : "") + scrOffset });
  satFetch("/api/display", { offset: scrOffset }).catch(() => { sq("scr-msg").textContent = T("Couldn't change the screen setting"); });
}

// Firmware update (R_FW_OTA): device.ota.state -> its text (idle: none)
/** @type {Record<string, string>} */
const SAT_OTA = { checking: "checking …", up_to_date: "up to date", available: "{sha} is available",
  downloading: "downloading {pct} %", verifying: "verifying …", rebooting: "installed, restarting …", error: "update failed ({error})" };
/** The running build + channel, the update's progress, Check / Install <sha7> and the channel (station
 * mode only, like the firmware's POST /api/ota). @param {SatOta|null} o @param {string|null} fw @param {boolean} st */
function satRenderFw(o, fw, st) {
  sq("fw").hidden = !o;
  if (!o) return;
  const sha = (o.available_sha || "").slice(0, 7), key = SAT_OTA[o.state];
  const busy = ["checking", "downloading", "verifying", "rebooting"].includes(o.state);
  sq("fw-status").textContent = [tf("Running {sha}", { sha: fw || "?" }), T(o.channel === "latest" ? "latest (every merge)" : "stable (tested)"),
    key ? tf(key, { sha: sha, pct: o.progress_pct, error: o.error || "" }) : "", st ? "" : T(SAT_STATION_ONLY)].filter(Boolean).join(" · ");
  sq("fw-check").hidden = !st || busy;
  const install = sq("fw-install");
  install.hidden = !st || busy || !sha;
  install.textContent = tf("Install {sha}", { sha: sha });
  sq("fw-ch-row").hidden = !st || busy;
  const sel = /** @type {HTMLSelectElement} */ (sq("fw-channel"));
  sel.dataset.v = o.channel;
  if (document.activeElement !== sel) sel.value = o.channel;
}
/** POST /api/ota: {action} (Check / Install) or {channel}. @param {{action?: string, channel?: string}} body */
async function satOta(body) {
  const msg = sq("fw-msg");
  msg.textContent = "";
  if (body.action) sq("fw-check").hidden = sq("fw-install").hidden = true;   // no second tap in flight
  try {
    const r = (await satFetch("/api/ota", body)).body;
    if (!r.ok) msg.textContent = body.channel ? T("Couldn't change the channel") : tf("update failed ({error})", { error: r.error || "" });
  } catch (e) { msg.textContent = T("No answer from the satellite."); }
  const sel = /** @type {HTMLSelectElement} */ (sq("fw-channel"));
  if (body.channel && msg.textContent) sel.value = sel.dataset.v || "";
  refreshState(true);
}

// --- the WiFi box's join (first setup over the hotspot; Change network… online, PR D) -------------
/** @type {{ssid: string, polls: number, left: boolean, away: boolean}|null} a join being watched
 * (away: seen off station or unreachable since the POST) */
let joining = null;
/** @type {SatWifi|null} the last polled device.wifi */
let satWifi = null;
let wifiOpen = false, formShown = false;
// a join started on WiFi setup: once online the screen stays (its "Connected as … open:" link), no Home
let joinedHere = false;
/** @type {ReturnType<typeof setTimeout>|null} the pending re-read of the last scan */
let scanTimer = null;
// /api/wifi last_error -> why a join failed (hotspot) / the same as a phrase (a reverted change)
/** @type {Record<string, string>} */
const JOIN_FAILED = { not_found: "Network not found — check that it is in range, then try again.",
  auth: "Wrong password — try again.", other: "Could not join the network — try again." };
/** @type {Record<string, string>} */
const JOIN_WHY = { not_found: "network not found", auth: "wrong password", other: "could not join" };

/** @param {SatWifi} w */
function satRenderWifi(w) {
  const st = w.mode === "station", form = !st || wifiOpen, sw = !sq("wifi-switch").hidden;
  sq("setup-title").textContent = T(st ? "WiFi" : "Connect to your WiFi");
  sq("wifi-now").textContent = st && !sw ? [tf("Connected to {ssid}", { ssid: w.ssid || "" }), w.ip, w.rssi + " dBm"].join(" · ") : "";
  /** @type {HTMLElement} */ (sq("wifi-change").parentElement).hidden = form || joining !== null || sw;
  sq("wifi-form").hidden = !form || sw;
  if (form && !formShown && !joining) satScan();
  formShown = form;
}

/** The scan list + "Other network…" (value "": the typed name; the only choice while the list is
 * empty). An open network is listed but not choosable (the satellite needs a passphrase).
 * @param {{ssid: string, rssi: number, secure: boolean}[]} scan */
function satNetworks(scan) {
  const sel = /** @type {HTMLSelectElement} */ (sq("ssid")), keep = sel.value, had = sel.options.length;
  sel.replaceChildren(...scan.map((ap) => {
    const o = h("option", { value: ap.ssid }, ap.ssid + "  (" + ap.rssi + " dBm" + (ap.secure ? "" : ", " + T("open network, not supported")) + ")");
    o.disabled = !ap.secure;
    return o;
  }), h("option", { value: "" }, T("Other network…")));
  // keep the choice ("Other network…" only when it was one: not while it was the only option)
  if (scan.some((ap) => ap.ssid === keep) || (had > 1 && !keep)) sel.value = keep;   // else the first network
  sq("scan-empty").hidden = scan.length > 0;
  satOther();
}
/** The name field shows while "Other network…" is chosen. */
function satOther() { sq("ssid-name").hidden = /** @type {HTMLSelectElement} */ (sq("ssid")).value !== ""; }

/** GET /api/wifi (each GET starts a fresh scan, also online) and read its result a moment later. */
async function satScan() {
  const msg = sq("setup-msg");
  if (scanTimer !== null) { clearTimeout(scanTimer); scanTimer = null; }
  msg.className = "note";
  msg.textContent = T("Searching for networks…");
  const still = () => msg.textContent === T("Searching for networks…");   // nothing newer landed
  try {
    satNetworks((await satFetch("/api/wifi")).body.scan || []);
    scanTimer = setTimeout(async () => {
      scanTimer = null;
      try { satNetworks((await satFetch("/api/wifi")).body.scan || []); if (still()) msg.textContent = ""; }
      catch (e) { if (still()) msg.textContent = T("No answer from the satellite."); }
    }, 2 * NET_PAGE_POLL_MS);
  } catch (e) { if (still()) msg.textContent = T("No answer from the satellite."); }
}

/** Connect: over the hotspot it posts at once; online it asks first (the satellite leaves its network;
 * a wrong password brings it back by itself), then shows the Switching panel BEFORE posting — the
 * phone loses the page once the satellite leaves, so the panel needs no server. */
function satConnect() {
  const ssid = /** @type {HTMLSelectElement} */ (sq("ssid")).value || sqInput("ssid-name").value.trim();
  const psk = sqInput("psk").value, msg = sq("setup-msg");
  if (scanTimer !== null) { clearTimeout(scanTimer); scanTimer = null; }
  msg.className = "warn";
  if (!ssid || ssid.length > NET_SSID_MAX) { msg.textContent = tf("Choose a network (1–{max} characters).", { max: NET_SSID_MAX }); return; }
  if (psk.length < NET_PSK_MIN || psk.length > NET_PSK_MAX) {
    msg.textContent = tf("The password needs {min}–{max} characters.", { min: NET_PSK_MIN, max: NET_PSK_MAX }); return;
  }
  msg.textContent = "";
  const w = satWifi;
  if (!w || w.mode !== "station") { satJoin(ssid, psk, true); return; }
  const old = w.ssid || "";
  satAsk(sq("wifi-acts"), [h("span", null, tf("The satellite leaves {ssid} now. If the new password is wrong, it returns to {ssid} by itself.", { ssid: old }))],
    T("Switch network"), "danger", (close) => {
      close();
      const p = sq("wifi-switch"), url = "http://" + NET_HOSTNAME + ".local", ap = "http://" + NET_AP_ADDR;
      p.replaceChildren(h("h3", null, T("Switching networks")),
        h("p", null, (ssid === old ? T("Reload this page in ~30 s, or open:") : tf("Join {ssid} with this phone, then open:", { ssid: ssid })) + " ", satLink(url)),
        h("p", null, tf("Back on {old} within a minute if the new password was wrong; otherwise join the setup hotspot {ap} (password {appsk}) and open:",
          { old: old, ap: NET_AP_SSID, appsk: NET_AP_PSK }) + " ", satLink(ap)));
      p.hidden = false;
      sq("wifi-now").textContent = "";   // it is leaving that network
      sq("wifi-form").hidden = true;
      wifiOpen = false;
      return satJoin(ssid, psk, false);
    }, "setup-msg");
}

/** POST /api/wifi and watch the join. @param {string} ssid @param {string} psk
 * @param {boolean} hotspot over the hotspot a lost answer is retried once; online it means the satellite already left */
async function satJoin(ssid, psk, hotspot) {
  const msg = sq("setup-msg");
  const send = () => satFetch("/api/wifi", { ssid: ssid, psk: psk }, NET_CONNECT_TIMEOUT_MS);
  try {
    let r = null;
    try { r = await send(); } catch (e) {
      // no answer: the phone can drop off the setup hotspot for a moment (the shared radio scans or
      // switches channel) — say so, wait, and try once more. Online the satellite already left.
      if (hotspot) {
        msg.className = "note";
        msg.textContent = T("Retrying the connection…");
        await new Promise((done) => setTimeout(done, NET_CONNECT_RETRY_MS));
        msg.className = "warn";
        r = await send();
      }
    }
    const res = r ? r.body : { ok: true };
    if (!res.ok) {
      msg.className = "warn";
      msg.textContent = res.error === "ssid" ? tf("Choose a network (1–{max} characters).", { max: NET_SSID_MAX })
        : res.error === "psk" ? tf("The password needs {min}–{max} characters.", { min: NET_PSK_MIN, max: NET_PSK_MAX })
          : T(res.error === "store" ? "Could not save the WiFi settings." : "The satellite rejected the request.");
      sq("wifi-switch").hidden = true;
      return;
    }
    sqInput("psk").value = "";
    msg.className = "note";
    msg.textContent = tf("Connecting to {ssid}…", { ssid: ssid });
    joining = { ssid: ssid, polls: 0, left: false, away: hotspot };
    if (hotspot) joinedHere = true;
    setTimeout(satWatchJoin, NET_PAGE_POLL_MS);
  } catch (e) {
    msg.className = "warn";
    msg.textContent = T("No answer from the satellite.");
    sq("wifi-switch").hidden = true;
  }
}

/** One look at GET /api/wifi while a join runs (every NET_PAGE_POLL_MS until it ends). */
async function satWatchJoin() {
  const j = joining;
  if (!j || !satDom) return;
  const msg = sq("setup-msg");
  try {
    const w = /** @type {SatWifiGet} */ ((await satFetch("/api/wifi")).body);
    j.polls++;
    if (w.mode !== "setup") j.left = true;
    if (w.mode !== "station") j.away = true;
    if (w.mode === "station" && w.ip && j.away) {   // online again: only once it left and came back
      msg.className = w.reverted ? "warn" : "note";
      msg.replaceChildren(w.reverted   // PR D: the new network failed, back on the old one
        ? tf("The new network didn't work ({why}); back on {ssid}.", { why: T(JOIN_WHY[w.last_error || ""] || "could not join"), ssid: w.ssid || "" })
        : tf("Connected as {ip}. Join {ssid} with this phone too, then open:", { ip: w.ip, ssid: j.ssid }) + " ");
      if (!w.reverted) msg.appendChild(satLink("http://" + NET_HOSTNAME + ".local"));
      sq("wifi-switch").hidden = true;
      joining = null;
    } else if (w.mode === "setup" && (j.left || j.polls > 3)) {   // why it failed: out of range, a typo, …
      msg.className = "warn";
      msg.textContent = T(JOIN_FAILED[w.last_error || ""] || JOIN_FAILED.other);
      joining = null;
    }
  } catch (e) { j.away = true; }   // gone (it left the network): keep watching
  if (joining === j) setTimeout(satWatchJoin, NET_PAGE_POLL_MS);
  else satUpdate();
}

/** Swap a row's buttons for an inline step (a question or fields) + OK and Cancel; Cancel puts them
 * back (and clears msgId). The Sensors poll leaves its box alone while one is open (.open). Focus: the
 * first field, else Cancel.
 * @param {HTMLElement} acts @param {Node[]} nodes @param {string} okText @param {string} okCls
 * @param {(close: () => void) => Promise<void>|void} ok OK is disabled while it runs (a double tap posts once)
 * @param {string} msgId */
function satAsk(acts, nodes, okText, okCls, ok, msgId) {
  const old = Array.from(acts.childNodes), cls = acts.className;
  acts.className = cls + " open";
  acts.replaceChildren(...nodes);
  const b = acts.appendChild(satBtn(okText, okCls)), c = acts.appendChild(satBtn(T("Cancel")));
  const close = () => {
    acts.className = cls;
    acts.replaceChildren(...old);
    sq(msgId).textContent = "";
  };
  b.onclick = async () => { b.disabled = true; await ok(close); b.disabled = false; };
  c.onclick = close;
  (nodes[0] instanceof HTMLInputElement ? nodes[0] : c).focus();
}

// --- Sensors (R_FW_SENSORS): shown while Experimental is on; its own 10 s poll (a GET kicks a scan window)
/** @type {Record<string, string>} type -> its name */
const SENS_TYPE = { govee: "Thermo-hygrometer (Govee)", victron_mppt: "Solar charger (Victron)", victron_battery: "Battery sense (Victron)" };
/** @type {Record<string, string>} */
const SENS_ERR = { bad_alias: "Name already used", bad_key: "Key must be 32 hex digits",
  key_mismatch: "Key does not match this device", full: "Sensor list is full (16)" };
/** a field name -> its unit, in display order (the rest is not shown here) @type {[RegExp, string][]} */
const SENS_UNIT = [[/_c$/, "°C"], [/humidity/, "%"], [/voltage/, "V"], [/power/, "W"], [/kwh/, "kWh"], [/current/, "A"]];
let sensOn = false, sensBusy = false;

async function satSensPoll() {
  if (!(sensOn = !!satDom && view === "satsettings" && !sq("sens").hidden)) return;
  try { await sensFetch(); } catch (e) { /* the next poll */ }
  setTimeout(satSensPoll, 10000);
}
/** GET /api/sensors and show it (also the e2e tests' "poll now"). */
async function sensFetch() { satRenderSens((await satFetch("/api/sensors")).body); }

/** Up to 3 readings with their units ("16.5 °C"). @param {Record<string, number|null>} v @returns {string[]} */
function sensVals(v) {
  /** @type {[number, string][]} */
  const out = [];
  for (const [k, x] of Object.entries(v)) {
    const i = SENS_UNIT.findIndex(([re]) => re.test(k));
    if (i >= 0 && x !== null) out.push([i, x + " " + SENS_UNIT[i][1]]);
  }
  return out.sort((a, b) => a[0] - b[0]).slice(0, 3).map((e) => e[1]);
}
/** A name field. @param {string} value @param {string} ph @returns {HTMLInputElement} */
function sensName(value, ph) {
  const n = h("input", { type: "text", autocapitalize: "none", spellcheck: "false", placeholder: ph, "aria-label": T("Name (a–z, 0–9, -, _)") });
  n.value = value;
  return n;
}
/** The name typed, lowercased; null (and the error shown) when invalid, or missing but needed.
 * @param {HTMLInputElement} n @param {boolean} need @returns {string|null} */
function sensAlias(n, need) {
  const a = n.value = n.value.trim().toLowerCase();
  if ((a || need) && !/^[a-z0-9_-]{1,16}$/.test(a)) { sensErr("Use a–z, 0–9, - or _ (max. 16)"); return null; }
  return a;
}
/** Show an error where the owner looks (announced: aria-live). @param {string} text */
function sensErr(text) {
  const m = sq("sens-msg");
  m.textContent = T(text);
  m.scrollIntoView({ block: "nearest" });
}
/** A sensor row: what it is, then its buttons. @param {string} text @returns {[HTMLElement, HTMLElement]} the row, its buttons */
function sensRow(text) {
  const acts = h("span", { class: "acts" });
  return [h("div", { class: "row srow" }, h("span", { class: "lbl" }, text), acts), acts];
}
/** @param {SatSensors} s */
function satRenderSens(s) {
  if (!satDom || sq("sens").querySelector(".open")) return;   // a confirmation or a name/key being typed
  const cfg = sq("sens-cfg"), seen = sq("sens-seen");
  cfg.replaceChildren(); seen.replaceChildren();
  for (const c of s.configured) {
    const [row, acts] = sensRow([T(SENS_TYPE[c.type] || c.type), c.alias, ...(c.value ? sensVals(c.value) : ["—"]),
      c.last_seen_s === null ? "—" : c.last_seen_s + " s"].join(" · "));
    const ren = acts.appendChild(satBtn(T("Rename"))), rm = acts.appendChild(satBtn(T("Remove")));
    ren.onclick = () => {
      const n = sensName(c.alias, "");
      satAsk(acts, [n], T("Save"), "primary", () => { const a = sensAlias(n, true); if (a) return sensPost({ id: c.id, alias: a }); }, "sens-msg");
    };
    rm.onclick = () => satAsk(acts, [h("span", null, tf("Remove {alias}?", { alias: c.alias }))], T("Remove"), "danger",
      () => sensPost({ remove: c.id }), "sens-msg");
    cfg.appendChild(row);
  }
  // an empty scan under configured sensors means no OTHER ones nearby (live check 2026-10-15)
  if (s.scan === "paused" || !s.seen.length)
    seen.appendChild(h("p", { class: "note" }, T(s.scan === "paused" ? "Scan paused while pairing"
      : s.configured.length ? "No other sensors nearby (the scan runs every minute)." : "No sensors found yet (the scan runs every minute).")));
  for (const r of s.seen) {
    const [row, acts] = sensRow([T(SENS_TYPE[r.type] || r.type), r.model, r.rssi + " dBm"].join(" · "));
    // a name (optional for the first of a type: the firmware then names it after the type id), a key
    // for a key type; then Add posts
    acts.appendChild(satBtn(T("Add"), "primary")).onclick = () => {
      const need = s.configured.some((c) => c.type === r.type);
      const n = sensName("", need ? T("Name (a–z, 0–9, -, _)") : r.type), nodes = [n];
      const k = r.type.startsWith("victron") ? h("input", { type: "text", autocomplete: "off", spellcheck: "false",
        placeholder: T("Encryption key (32 hex digits)"), "aria-label": T("Encryption key (32 hex digits)") }) : null;
      if (k) nodes.push(k);
      satAsk(acts, nodes, T("Add"), "primary", () => {
        const a = sensAlias(n, need);
        if (a === null) return;
        /** @type {Record<string, string|number>} */
        const o = { add: r.id };
        if (a) o.alias = a;
        if (k) o.key = k.value.trim();
        return sensPost(o);
      }, "sens-msg");
    };
    seen.appendChild(row);
  }
}
/** @param {Record<string, string|number>} o */
async function sensPost(o) {
  if (sensBusy) return;   // one POST at a time
  sensBusy = true;
  sq("sens-msg").textContent = "";
  try {
    const r = await satFetch("/api/sensors", o);
    if (r.status !== 200) { sensErr(SENS_ERR[r.body.error || ""] || "Couldn't change the sensors"); return; }
    sq("sens-cfg").replaceChildren(); sq("sens-seen").replaceChildren();   // closes the open step
    satRenderSens(r.body);
  } catch (e) { sensErr("Couldn't change the sensors"); } finally { sensBusy = false; }
}

// The app's "California Status" overview card: fresh/grey water + leisure battery, at a glance.
// A row is emitted only when its data is present (installed + non-null), so a truncated poll or
// an uninstalled tank never renders a "— / — l" ghost row.
/**
 * @param {string} label
 * @param {string} value
 * @returns {HTMLDivElement}
 */
function sumRow(label, value) {
  const row = document.createElement("div"); row.className = "row";
  const l = document.createElement("span"); l.className = "lbl"; l.textContent = label;
  const v = document.createElement("span"); v.className = "val"; v.textContent = value;
  row.append(l, v);
  return row;
}

function renderSummary() {
  /** @type {HTMLDivElement[]} */
  const rows = [];
  /** @type {FnState} */
  const w = STATE.water || {};
  if (installed("water")) {
    const f = w.fresh, g = w.waste;
    if (f && f.liters != null) rows.push(sumRow(/** @type {string} */ (t("Fresh water")), `${f.liters} / ${f.capacity_l} l${f.stale ? " 🕒" : ""}`));
    if (g && g.liters != null) rows.push(sumRow(/** @type {string} */ (t("Waste water")), `${g.liters} / ${g.capacity_l} l${g.stale ? " 🕒" : ""}`));
  }
  /** @type {FnState} */
  const e = STATE.energy || {};
  if (e.soc2_pct != null) {
    const h = e.batt2_remaining_h;
    rows.push(sumRow(/** @type {string} */ (t("Second battery")), `${e.soc2_pct}%` + (h ? ` · ${h} h` : "")));
  }
  if (!rows.length) return null;
  const card = document.createElement("div"); card.className = "card summary";
  rows.forEach((r) => card.appendChild(r));
  return card;
}

// The ESP's external BLE sensors (R_FW_SENSORS, `ext`), placed by their `type`: EXT_TYPES' rows join
// the screen whose readouts carry `{ ext: type }` (Energy: Victron, Vehicle: Govee); an entry of a
// type not listed here (a future firmware's) shows on the start page's Sensors card. Only the
// satellite reports `ext`, so buspi's page shows neither.
// Victron's charger states the solar charger reports most (VictronConnect's words)
/** @type {Record<number, string>} */
const CHARGE_STATES = { 0: "off", 2: "fault", 3: "bulk", 4: "absorption", 5: "float", 6: "storage", 7: "equalize",
  245: "starting up", 247: "recondition", 252: "external control" };
const SENSOR_STALE_S = 600;   // the firmware's /api/sensors drops a reading this old too
const LOW_SENSOR_BATTERY = 20;   // % — below it the Govee's battery joins its temperature row

/** @param {unknown} v @param {number} digits @param {string} [unit] @returns {string} a number, else "—" */
const fix = (v, digits, unit = "") => (typeof v === "number" ? v.toFixed(digits) + unit : "—");
/** @param {unknown} v @returns {string} the raw value, else "—" */
const raw = (v) => (typeof v === "number" ? String(v) : "—");
/** @param {SensorReading} e */
const extStale = (e) => typeof e.age_s !== "number" || e.age_s > SENSOR_STALE_S;

/** Rows per external sensor type: [label, value of one entry]. Labels go through t() on render.
 * @type {Record<string, { rows: [string, (e: SensorReading) => string][] }>} */
const EXT_TYPES = {
  // The unit's own "Solar power" format, charge state word (W · A), plus the battery voltage the
  // charger measures (owner decision 2026-10-15)
  victron_mppt: { rows: [
    // W · A · V like the battery rows; the charge phase on its own row (owner 2026-10-15)
    ["Solar power", (e) => `${raw(e.pv_power)} W · ${raw(e.battery_current)} A · ${fix(e.battery_voltage, 1, " V")}`],
    ["Charge phase", (e) => (typeof e.charge_state === "number" ? /** @type {string} */ (t(CHARGE_STATES[e.charge_state] || String(e.charge_state))) : "—")],
    ["Solar yield today", (e) => fix(e.yield_today_kwh, 2, " kWh")],
  ] },
  victron_battery: { rows: [
    ["Battery sensor", (e) => `${fix(e.battery_voltage, 2, " V")} · ${fix(e.temperature_c, 1, " °C")}`],
  ] },
  // A comfort reading: whole percent. The sensor's own battery is no row — only a low one shows,
  // like the unit's own low-battery alerts.
  govee: { rows: [
    ["Temperature", (e) => fix(e.temp_c, 1, " °C")
      + (typeof e.battery === "number" && e.battery < LOW_SENSOR_BATTERY ? ` · 🔋 ${e.battery} %` : "")],
    ["Humidity", (e) => fix(e.humidity, 0, " %")],
  ] },
};

/** Age limit (s) of a charger reading in the house-load sum: the two pollers are independent. */
const HOUSE_LOAD_MAX_AGE_S = 60;
/**
 * House load = Σ charger battery-side currents − the second battery's net current, "<W> W · <A> A".
 * "—" when an installed source or the battery reading is missing, a charger reading is stale, or
 * the result is below −0.5 A (a measurement mismatch); −0.5…0 A shows as 0.
 * @param {FnState} s the energy state
 * @returns {string}
 */
function houseLoad(s) {
  let sum = 0;
  for (const [, e] of extEntries("victron_mppt")) {
    if (typeof e.age_s !== "number" || e.age_s > HOUSE_LOAD_MAX_AGE_S || typeof e.battery_current !== "number") return "—";
    sum += e.battery_current;
  }
  /** @type {[boolean|undefined, number|null|undefined][]} */
  const sources = [[s.solar_installed, s.solar_current], [s.dcdc_installed, s.dcdc_current], [s.shore_installed, s.shore_current]];
  for (const [on, a] of sources) {
    if (!on) continue;
    if (a == null) return "—";
    sum += a;
  }
  if (s.batt2_current == null || s.batt2_v == null) return "—";
  const amps = sum - s.batt2_current;
  if (amps < -0.5) return "—";
  const a = Math.max(0, amps);
  return `${Math.round(a * s.batt2_v)} W · ${a.toFixed(1)} A`;
}

/** @param {string} type @returns {[string, SensorReading][]} the `ext` entries of a type, by alias */
function extEntries(type) {
  return Object.entries(STATE.ext || {}).filter(([, e]) => e.type === type).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0));
}

/**
 * A screen's readouts with each `{ ext: type }` marker expanded into its type's rows per entry; the
 * label leads with the entry's alias, the name the owner gave the device in Satellite settings › Sensors ("Innen
 * Temperature", "Dach Solar power"; owner decision 2026-10-15: the sensors broadcast no usable name);
 * an old reading keeps its value and adds "🕒 <ago>" (never colour alone). The one entry of its type
 * still on the firmware's default alias (its type id) drops it ("Battery sensor", not "Battery-sense Battery
 * sensor"), unless a row would then read like one of the unit's own shown rows ("Solar power").
 * @param {(Readout|ExtReadouts)[]} readouts
 * @param {FnState} s the screen's state (which of the unit's own rows show)
 * @returns {Readout[]}
 */
function expandReadouts(readouts, s) {
  const own = new Set(readouts.flatMap((r) => ("ext" in r || (r.show && !r.show(s)) ? [] : [r.label])));
  return readouts.flatMap((r) => {
    if (!("ext" in r)) return [r];
    const entries = extEntries(r.ext), rows = EXT_TYPES[r.ext].rows;
    return entries.flatMap(([alias, e]) => {
      const bare = entries.length === 1 && alias === r.ext && !rows.some(([l]) => own.has(l));
      return rows.map(([label, get]) => ({
        label: (bare ? "" : cap(alias) + " ") + t(label),
        get: () => get(e) + (extStale(e) ? ` 🕒 ${agoText(/** @type {number|null} */ (e.age_s))}` : ""),
      }));
    });
  });
}

// The Sensors card's generic formatting for a type EXT_TYPES does not know: the fields it shares
// with the known ones, in this order, then any other field raw.
/** @type {[string, number, string, string?][]} field, decimals, unit, prefix — in display order;
 * unit "" = the solar charger's charge state, shown as a word */
const SENSOR_FIELDS = [
  ["temp_c", 1, "°C"], ["humidity", 1, "%"], ["battery", 0, "%", "🔋 "],
  ["battery_voltage", 2, "V"], ["battery_current", 1, "A"], ["pv_power", 0, "W"],
  ["yield_today_kwh", 2, "kWh"], ["charge_state", 0, ""], ["temperature_c", 1, "°C"],
];

/** @param {SensorReading} r @returns {string} */
function sensorValues(r) {
  const parts = [];
  for (const [k, digits, unit, prefix] of SENSOR_FIELDS) {
    if (!(k in r)) continue;
    const v = r[k];
    if (!unit) parts.push(typeof v === "number" ? /** @type {string} */ (t(CHARGE_STATES[v] || String(v))) : "—");
    else parts.push((prefix || "") + fix(v, digits) + " " + unit);
  }
  const known = new Set(["type", "age_s", "error_code", ...SENSOR_FIELDS.map((f) => f[0])]);
  for (const [k, v] of Object.entries(r)) if (!known.has(k)) parts.push(`${k} ${v ?? "—"}`);
  if (r.error_code) parts.push(tf("error {n}", { n: r.error_code }));
  return parts.join(" · ");
}

function renderSensors() {
  const unknown = Object.entries(STATE.ext || {}).filter(([, r]) => !(String(r.type) in EXT_TYPES));
  if (!unknown.length) return null;
  const card = document.createElement("div");
  card.className = "card sensors";
  card.setAttribute("role", "group");
  card.setAttribute("aria-label", /** @type {string} */ (t("Sensors")));
  for (const [alias, r] of unknown) {
    const age = typeof r.age_s === "number" ? r.age_s : null;
    const stale = age != null && age > SENSOR_STALE_S;
    const row = document.createElement("div");
    row.className = "row sensor" + (stale ? " stale" : "");
    const lbl = document.createElement("span"); lbl.className = "lbl";
    const name = document.createElement("span"); name.className = "sname";
    name.textContent = alias;   // the name the owner gave the device: this UI knows no better one
    lbl.appendChild(name);
    const sv = document.createElement("span"); sv.className = "sv"; sv.textContent = sensorValues(r);
    lbl.appendChild(sv);
    const val = document.createElement("span"); val.className = "val";
    val.textContent = (stale ? "🕒 " : "") + agoText(age);
    row.append(lbl, val);
    card.appendChild(row);
  }
  return card;
}

/** No unit paired and the wizard closed: the banner with Pair… (Home, and WiFi setup over the hotspot). */
function unpairedBanner() {
  if (!(STATE._meta && STATE._meta.paired === false && !pairingOpen)) return;
  const b = document.createElement("div"); b.className = "card"; b.id = "unpaired-banner";
  const msg = document.createElement("span"); msg.className = "lbl";
  msg.textContent = /** @type {string} */ (t("No camper unit is paired yet."));
  b.append(msg);
  const go = document.createElement("button"); go.type = "button"; go.className = "btn";
  go.textContent = /** @type {string} */ (t("Pair…"));
  go.onclick = () => openPairingWizard();   // calictl and the satellite both run the wizard
  b.append(go);
  app.appendChild(b);
}

function renderDashboard() {
  titleEl.textContent = "Open California";   // the app's name (manifest short_name), not the Vehicle screen's (H3)
  // The wizard, when open (Device status, the unpaired banner below, the #pair link), always leads the
  // page: a card after the tiles was out of sight on a phone (UX review H2).
  const pc = pairingCard();   // null unless open
  if (pc) app.appendChild(pc);
  unpairedBanner();
  const summary = renderSummary();
  if (summary) app.appendChild(summary);
  const sensors = renderSensors();
  if (sensors) app.appendChild(sensors);
  const grid = document.createElement("div");
  grid.className = "tilegrid";
  for (const fn of ORDER) {
    if (!installed(fn)) continue;
    const f = FEATURES[fn];
    /** @type {FnState} */
    const s = STATE[fn] || {};
    const tile = document.createElement("button");
    // A feature's own warn(s) doubles as the dashboard alert signal (cooler fault, roof alert):
    // non-null -> the tile gets a ⚠ badge + highlight so a fault is visible without drilling in.
    const alertMsg = f.warn ? f.warn(s) : null;
    tile.className = "tile" + (alertMsg ? " alert" : "");
    tile.innerHTML = (alertMsg ? `<div class="tbadge">⚠</div>` : "") +
      `<div class="ti">${f.icon || ""}</div><div class="tt">${t(f.title)}</div>` +
      `<div class="tv">${f.summary ? f.summary(s) || "" : ""}</div>`;
    if (alertMsg) tile.title = alertMsg;
    tile.onclick = () => goto(fn);
    grid.appendChild(tile);
  }
  app.appendChild(grid);
}

// Lighting lamps, grouped like the app (from the HCI capture + screenshots). `what` is the
// control API zone key (control.LIGHT_ZONES); `zone` is the interpreted brightness_zone_<N>.
// Full van lamp map — DEVICE-confirmed 2026-08-30 by single-light isolation + owner-watched
// writes (each lamp turned on alone, wire zone read). See control.LIGHT_ZONES.
/** @type {{ group: string, lamps: { label: string, what: string, zone: number, hint?: string }[] }[]} */
const LIGHT_LAMPS = [
  { group: "Reading lights", lamps: [
    { label: "Left", what: "reading-1", zone: 2 },
    { label: "Right", what: "reading-2", zone: 1 },
    { label: "Front passenger", what: "reading-3", zone: 4 } ] },
  { group: "Kitchen", lamps: [
    { label: "Background lighting", what: "kitchen-ambient", zone: 5 },
    // Web-only lamp: the app has no control for L6, but it is real (DEVICE 2026-08-30, owner
    // re-confirmed 2026-09-16) — keep it.
    { label: "Cabinet", what: "kitchen-cabinet", zone: 6 },
    { label: "Cooking", what: "kitchen", zone: 7 } ] },
  { group: "Pop-up roof", lamps: [
    { label: "Background lighting", what: "roof-ambient", zone: 8 },
    { label: "Reading light", what: "roof-reading", zone: 9, hint: "only when the pop-up roof is open" } ] },
  { group: "Exterior light", lamps: [
    { label: "Rear surroundings", what: "outside-rear", zone: 3 },
    { label: "Entrance", what: "entrance", zone: 12 } ] },
];
// The app's T7 wake-up area labels (ti/b: AREA_1..4)
const WAKE_AREAS = ["Living area reading lights", "Kitchen background lighting",
  "Pop-up roof reading lights", "Pop-up roof background lighting"];
const LIGHT_MAX = 10;   // dg/i enum: 0=off, 1-10 = 10%..100% (11=default; 13=NOT_EQUIPPED — never send)

/** @param {FnState} s */
function renderLighting(s) {
  titleEl.textContent = /** @type {string} */ (t("Lighting"));
  // Actuation is photon-verified (2026-08-16): a bare SET+commit drives the lamps on an awake
  // unit, so the daemon's fast path skips the arm/preamble and lamps respond in well under a
  // second (like the app). No latency caveat needed; the command confirms from the 1502 Mode-4
  // notification ("✓ Applied") or falls back to "Sent — check the lamp".
  // Lamps are ALWAYS controllable, like the app: dragging a lamp sends a SET_BRIGHTNESS that
  // self-carries the working profile (control.LIGHT_BRIGHTNESS_PROFILE=9), so there is no
  // "activate a profile first" step — the old gate was a workaround for our echoing PN=0.
  // "All lights" master toggle (the app's "Alle Lichter"): on = any real lamp lit.
  const allOn = optOn("lighting", "power", !!s.any_on);
  const mc = document.createElement("div"); mc.className = "card";
  const mrow = document.createElement("div"); mrow.className = "row";
  const mlbl = document.createElement("span"); mlbl.className = "lbl"; mlbl.textContent = /** @type {string} */ (t("All lights"));
  mrow.appendChild(mlbl);
  if (pending_is("lighting", "power")) mrow.appendChild(spinner());
  const msw = document.createElement("button"); msw.className = "switch";
  msw.setAttribute("role", "switch"); msw.setAttribute("aria-label", "All lights");
  msw.setAttribute("aria-checked", allOn ? "true" : "false"); msw.disabled = readOnly();
  msw.onclick = () => command("lighting", "power", allOn ? "off" : "on");
  mrow.appendChild(msw); mc.appendChild(mrow);

  // The app's four favourite tiles A/B/C/D (FAVORITE 1/5/6/7, gv/z0, hi/f; CLI/API take 1-7): one
  // tap activates (SET_PROFILE PN n, dg/h.u0). Filled = the unit reports the slot stored
  // (FavoriteProfileModifiedState); a reported-empty "+" tile can't be activated (the server refuses
  // it too); bits not reported yet (null) -> plain letter, tappable (the server allows unknown).
  // Saving lives under "Functions & settings" below (the app saves by long-press).
  // The app's main page offers only A-D: wake-up (10) has its own card below; interior light (11)
  // is not on any app page we render, so it stays CLI/API-only (`set lighting profile 11`).
  const FAV_TILES = /** @type {[number, string][]} */ ([[1, "A"], [5, "B"], [6, "C"], [7, "D"]]);
  const favs = document.createElement("div"); favs.className = "favs";
  const favSent = pending_is("lighting", "profile") ? optimistic[qKey("lighting", "profile")] : null;
  for (const [n, l] of FAV_TILES) {
    const known = Array.isArray(s.favourites_stored);
    const filled = known && /** @type {number[]} */ (s.favourites_stored).includes(n);   // saved on the unit
    const empty = known && !filled;
    const b = document.createElement("button"); b.className = "fav" + (filled ? " filled" : empty ? "" : " unknown");
    b.textContent = empty ? "+" : l;
    b.setAttribute("aria-label", "Favourite " + l);
    if (empty) b.title = /** @type {string} */ (t("Empty — save it under Functions & settings"));
    b.disabled = readOnly() || empty;
    if (favSent === n) { b.classList.add("pending"); b.appendChild(spinner()); }
    b.onclick = () => command("lighting", "profile", n);
    favs.appendChild(b);
  }
  mc.appendChild(favs);
  app.appendChild(mc);

  // lamp sliders, grouped like the app (always controllable)
  for (const grp of LIGHT_LAMPS) {
    const card = document.createElement("div"); card.className = "card";
    const h = document.createElement("div"); h.className = "note"; h.style.padding = ".6rem 0 0";
    h.textContent = /** @type {string} */ (t(grp.group)); card.appendChild(h);
    for (const lamp of grp.lamps) {
      const row = document.createElement("div"); row.className = "row";
      const lbl = document.createElement("span"); lbl.className = "lbl"; lbl.textContent = /** @type {string} */ (t(lamp.label));
      // roof reading light (L9) is physically unpowered while the pop-top is down — reflect the
      // server-side precondition (control.command_precondition) so the slider isn't a silent no-op.
      const roofPos = (STATE.roof || {}).position_name;
      const roofBlocked = lamp.what === "roof-reading"
        && !(roofPos === "open" || roofPos === "middle" || roofPos == null);
      const hintText = roofBlocked ? "only when the pop-up roof is open" : lamp.hint;
      if (hintText) {
        const hh = document.createElement("span"); hh.className = "lamp-hint"; hh.textContent = /** @type {string} */ (t(hintText));
        lbl.appendChild(hh);
      }
      // brightness_zone_1..16 are read by dynamic key (see FnState note); cast the state to an
      // index map so the computed-key read type-checks (the leaf itself is a nullable number).
      const real = (/** @type {Record<string, number|null|undefined>} */ (s))["brightness_zone_" + lamp.zone];
      const val = optNum("lighting", lamp.what, real != null ? real : 0);
      const isPending = pending_is("lighting", lamp.what);
      row.appendChild(lbl);
      if (isPending) row.appendChild(spinner());
      const inp = document.createElement("input"); inp.type = "range"; inp.min = /** @type {any} */ (0); inp.max = /** @type {any} */ (LIGHT_MAX);
      // readback can carry enum values past the settable range (11=default, 13/14 markers):
      // clamp the thumb (11 ≈ full) but zero it for the no-reading markers; label shows the truth
      inp.value = /** @type {any} */ (val >= 13 ? 0 : Math.min(val, LIGHT_MAX));
      inp.disabled = readOnly() || roofBlocked;
      inp.setAttribute("aria-label", grp.group + " " + lamp.label + " brightness");
      const out = document.createElement("span"); out.className = "sval";
      out.textContent = brightnessText(val);
      inp.oninput = () => (out.textContent = inp.value);
      inp.onchange = () => command("lighting", lamp.what, Number(inp.value));
      row.appendChild(inp); row.appendChild(out);
      card.appendChild(row);
    }
    app.appendChild(card);
  }

  // "Functions & settings" (the app's section at the bottom of its Lighting page): the wake-up
  // light, the sliding-door light and saving favourites — rarely needed, so collapsed by default,
  // its open state remembered per viewer (and across the 2 s poll re-render).
  const fbody = document.createElement("div"); fbody.className = "lfbody";

  // Wake-up light (the app's Lighting > Functions & Settings > Wake-up light; dg/h.m0, Mode 20).
  // The unit reports its config only in a Mode-20 frame (latched by the daemon). Every change
  // resends the whole config like the app; the time picker never flips the switch (app-recorded).
  // The unit-reported config (null until a Mode-20 frame was latched). Until the state confirms,
  // the user's last sent edit overlays it (optimistic), so a re-render never resets a time that was
  // just entered. We never invent a config: with none known the card is disabled, and an edit
  // omits on/off so the daemon carries the enabled state the UNIT reported.
  const wkReal = s.wakeup || null;
  const wkOpt = optimistic[qKey("lighting", "wakeup")];
  /** @type {typeof wkReal} */
  let wk = wkReal;
  if (wkReal && typeof wkOpt === "string") {
    const tk = wkOpt.split(/\s+/);
    const pos = tk.filter((x) => x !== "on" && x !== "off" && !x.includes(":"));
    wk = Object.assign({}, wkReal, {
      time: tk.find((x) => x.includes(":")) || wkReal.time,
      areas: pos[0] ? pos[0].split(",").map(Number) : wkReal.areas,
      brightness: pos[1] != null ? Number(pos[1]) : wkReal.brightness,
      ramp: pos[2] != null ? Number(pos[2]) : wkReal.ramp,
      enabled: tk.includes("on") ? true : tk.includes("off") ? false : wkReal.enabled,
    });
  }
  // The satellite latches the unit's config like the daemon (firmware session) and takes the page's
  // clock (local_now).
  const wkOff = readOnly() || !wk;
  /** @param {{time?: string, areas?: number[], brightness?: number, ramp?: number, on?: boolean}} p */
  const wakeCmd = (p) => {
    // Config unknown: only a time edit, sent alone — the server pulls the unit's config (R5) and
    // fills the rest from what the unit reports, or refuses with the reason. Nothing is invented.
    if (!wk) { if (p.time && p.on == null) command("lighting", "wakeup", p.time); return; }
    const base = `${p.time || wk.time} ${(p.areas || wk.areas).join(",")} ` +
      `${p.brightness != null ? p.brightness : wk.brightness} ${p.ramp != null ? p.ramp : wk.ramp}`;
    command("lighting", "wakeup", p.on == null ? base : `${base} ${p.on ? "on" : "off"}`);
  };
  const wc = document.createElement("div"); wc.className = "card";
  const wh = document.createElement("div"); wh.className = "note"; wh.style.padding = ".6rem 0 0";
  wh.textContent = /** @type {string} */ (t("Wake-up light")); wc.appendChild(wh);
  const wf = document.createDocumentFragment();   // the rows: rows stay direct children of the card
  const wrow = document.createElement("div"); wrow.className = "row";
  const wl = document.createElement("span"); wl.className = "lbl"; wl.textContent = /** @type {string} */ (t("Wake-up light"));
  wrow.appendChild(wl);
  if (pending_is("lighting", "wakeup")) wrow.appendChild(spinner());
  const wsw = document.createElement("button"); wsw.className = "switch";
  wsw.setAttribute("role", "switch"); wsw.setAttribute("aria-label", "Wake-up light");
  wsw.setAttribute("aria-checked", wk && wk.enabled ? "true" : "false"); wsw.disabled = wkOff;
  wsw.onclick = () => wakeCmd({ on: !(wk && wk.enabled) });
  wrow.appendChild(wsw); wf.appendChild(wrow);
  const trow = document.createElement("div"); trow.className = "row";
  const tl = document.createElement("span"); tl.className = "lbl"; tl.textContent = /** @type {string} */ (t("Wake-up time"));
  const tin = document.createElement("input"); tin.type = "time"; tin.value = wk ? wk.time
    : (typeof wkOpt === "string" && wkOpt.split(/\s+/).find((x) => x.includes(":"))) || "";   // empty, never 00:00
  tin.setAttribute("aria-label", "Wake-up time"); tin.disabled = readOnly();   // live even while unknown (R5 pull)
  tin.onchange = () => { if (tin.value) wakeCmd({ time: tin.value }); };
  trow.append(tl, tin); wf.appendChild(trow);
  const rrow = document.createElement("div"); rrow.className = "row";
  const rl = document.createElement("span"); rl.className = "lbl"; rl.textContent = /** @type {string} */ (t("Lead time"));
  const rsel = document.createElement("select"); rsel.disabled = wkOff;
  rsel.setAttribute("aria-label", "Lead time");
  for (const m of [0, 10, 20, 30]) {
    const o = document.createElement("option"); o.value = /** @type {any} */ (m); o.textContent = tf("{n} min", { n: m });
    o.selected = (wk ? wk.ramp : 0) === m; rsel.appendChild(o);
  }
  rsel.onchange = () => wakeCmd({ ramp: Number(rsel.value) });
  rrow.append(rl, rsel); wf.appendChild(rrow);
  const brow = document.createElement("div"); brow.className = "row";
  const bl = document.createElement("span"); bl.className = "lbl"; bl.textContent = /** @type {string} */ (t("Brightness"));
  const bin = document.createElement("input"); bin.type = "range"; bin.min = /** @type {any} */ (0); bin.max = /** @type {any} */ (LIGHT_MAX);
  bin.value = /** @type {any} */ (wk ? wk.brightness : 0); bin.disabled = wkOff;
  bin.setAttribute("aria-label", "Wake-up brightness");
  bin.onchange = () => wakeCmd({ brightness: Number(bin.value) });
  brow.append(bl, bin); wf.appendChild(brow);
  const arow = document.createElement("div"); arow.className = "row areas";
  const al = document.createElement("span"); al.className = "lbl"; al.textContent = /** @type {string} */ (t("Vehicle area"));
  const alist = document.createElement("div"); alist.className = "arealist";
  arow.append(al, alist);
  const curAreas = wk ? wk.areas : [];
  for (let a = 1; a <= 4; a++) {
    const lab = document.createElement("label");
    const cb = document.createElement("input"); cb.type = "checkbox"; cb.checked = curAreas.includes(a);
    // never untick the last area: the unit needs one (the app's "no area chosen" dialog)
    cb.disabled = wkOff || (cb.checked && curAreas.length === 1);
    cb.onchange = () => wakeCmd({ areas: cb.checked ? [...curAreas, a].sort() : curAreas.filter((x) => x !== a) });
    lab.append(cb, document.createTextNode(/** @type {string} */ (t(WAKE_AREAS[a - 1]))));
    alist.appendChild(lab);
  }
  wf.appendChild(arow);
  wc.appendChild(wf);
  if (!wk) {
    const nk = document.createElement("div"); nk.className = "note";
    nk.textContent = /** @type {string} */ (t("Wake-up settings not known yet — the unit has not reported them"));
    wc.appendChild(nk);
  }
  fbody.appendChild(wc);

  // Lighting & sliding door (dg/h.n4: SET_PROFILE PN 8, LightValue 1/0). Not variant-gated (see
  // control.command_precondition): the app's T7 page shows it, and this T7 reads CarVariant=0.
  const dc = document.createElement("div"); dc.className = "card";
  // the app hides the row only on Grand California = raw CarVariant 1 (qg/e.java:357-363: raw 0 T7,
  // 1 GC, 2 California Next — NOT the enum ordinals); no equipment gate (wh/c.h0)
  const gc = !!STATE.vehicle && STATE.vehicle.car_variant === 1;
  const dh = document.createElement("div"); dh.className = "note"; dh.style.padding = ".6rem 0 0";
  dh.textContent = /** @type {string} */ (t("Lighting & sliding door")); dc.appendChild(dh);
  const drow = document.createElement("div"); drow.className = "row";
  const dl = document.createElement("span"); dl.className = "lbl";
  dl.textContent = /** @type {string} */ (t("Opening sliding door activates the rear interior lights."));
  drow.appendChild(dl);
  if (pending_is("lighting", "door_contact")) drow.appendChild(spinner());
  const dOn = optOn("lighting", "door_contact", s.door_contact === true);
  const dsw = document.createElement("button"); dsw.className = "switch";
  dsw.setAttribute("role", "switch"); dsw.setAttribute("aria-label", "Sliding door lighting");
  dsw.setAttribute("aria-checked", dOn ? "true" : "false"); dsw.disabled = readOnly();
  dsw.onclick = () => command("lighting", "door_contact", dOn ? "off" : "on");
  drow.appendChild(dsw); dc.appendChild(drow);
  if (!gc) fbody.appendChild(dc);


  // Save the CURRENT lamp levels into a favourite slot (the app's l3 applyProfileBrightness).
  const sc = document.createElement("div"); sc.className = "card";
  const srow = document.createElement("div"); srow.className = "row";
  const slbl = document.createElement("span"); slbl.className = "lbl"; slbl.textContent = /** @type {string} */ (t("Save current as"));
  srow.appendChild(slbl);
  if (pending_is("lighting", "save_profile")) srow.appendChild(spinner());
  const ssel = document.createElement("select");
  ssel.disabled = readOnly();
  ssel.setAttribute("aria-label", "Save current as");
  const s0 = document.createElement("option"); s0.value = ""; s0.textContent = /** @type {string} */ (t("Profile…")); s0.selected = true; ssel.appendChild(s0);
  for (const [n, l] of FAV_TILES) {
    const o = document.createElement("option"); o.value = /** @type {any} */ (n); o.textContent = t("Profile") + " " + l; ssel.appendChild(o);
  }
  ssel.onchange = () => {
    if (ssel.value === "") return;
    const n = Number(ssel.value);
    ssel.value = "";
    if (!confirm(tf("Overwrite profile {n} with the current lamp levels? This writes to the unit and is not yet verified on the van. Continue?", { n: n }))) return;
    command("lighting", "save_profile", n);
  };
  srow.appendChild(ssel); sc.appendChild(srow);
  fbody.appendChild(sc);

  const det = document.createElement("details"); det.className = "lfuncs";
  det.open = lightFuncsOpen;
  det.ontoggle = () => {
    lightFuncsOpen = det.open;
    try { localStorage.setItem(LFUNCS_KEY, det.open ? "1" : "0"); } catch (e) { /* storage blocked */ }
  };
  const sum = document.createElement("summary");
  const stl = document.createElement("span"); stl.textContent = /** @type {string} */ (t("Functions & settings"));
  // one-line state while collapsed: the unit-reported wake-up + door light (nothing if unknown)
  const bits = [];
  if (wk) bits.push(wk.enabled ? tf("Wake-up light {time}", { time: wk.time }) : t("Wake-up light off"));
  if (!gc && s.door_contact != null) bits.push(t(dOn ? "Sliding door light on" : "Sliding door light off"));
  const ssum = document.createElement("span"); ssum.className = "lfsum"; ssum.textContent = bits.join(" · ");
  sum.append(stl, ssum);
  det.append(sum, fbody);
  app.appendChild(det);
}

/** @param {string} fn */
function renderFeature(fn) {
  const f = FEATURES[fn];
  /** @type {FnState} */
  const s = STATE[fn] || {};
  titleEl.textContent = /** @type {string} */ (t(f.title));
  if (f.warn) {                         // dynamic FAULT banner (e.g. fridge door open) — red/alert
    /** @type {string|null|undefined} */
    let w;
    try { w = f.warn(s); } catch (e) { w = null; }
    if (w) {
      const b = document.createElement("div");
      b.className = "warn"; b.textContent = /** @type {string} */ (t(w));
      app.appendChild(b);
    }
  }
  if (f.note) {                         // soft INFO banner (e.g. water stale) — muted, not a fault
    /** @type {string|null|undefined} */
    let n;
    try { n = f.note(s); } catch (e) { n = null; }
    if (n) {
      const b = document.createElement("div");
      b.className = "note"; b.textContent = /** @type {string} */ (t(n));
      app.appendChild(b);
    }
  }
  if (f.lighting) return renderLighting(s);
  if (f.controls && f.controls.length) {
    const card = document.createElement("div");
    card.className = "card";
    for (const c of f.controls) card.appendChild(renderControl(fn, c, s));
    app.appendChild(card);
  }
  if (f.roof) app.appendChild(roofControls(s));
  if (fn === "campingmode" && hasAutoCamper()) app.appendChild(autoCamperCard());
  if (f.readouts && f.readouts.length) {
    const card = document.createElement("div");
    card.className = "card";
    for (const r of expandReadouts(f.readouts, s)) if (!r.show || r.show(s)) card.appendChild(renderReadout(r, s));
    app.appendChild(card);
  }
}

/**
 * @param {string} fn
 * @param {Control} c
 * @param {FnState} s
 * @returns {HTMLDivElement}
 */
function renderControl(fn, c, s) {
  const row = document.createElement("div");
  row.className = "row";
  const label = document.createElement("span");
  label.className = "lbl";
  label.textContent = /** @type {string} */ (t(c.label));
  row.appendChild(label);
  const isPending = pending_is(fn, c.what);
  if (isPending) row.appendChild(spinner());
  // A control is unavailable when the daemon is read-only, or when its own `disabled(s)`
  // predicate says the current vehicle state forbids toggling it (mirrors the app, which
  // greys the same control in the same state). Grey the whole row and block the input.
  const offReason = c.disabled ? c.disabled(s) : false;
  const ro = readOnly() || !!offReason;
  if (ro) {
    row.classList.add("ctl-off");
    // a string reason (not just `true`) becomes the row's title/tooltip explaining why
    const why = readOnly() ? t("Read-only mode — writes are disabled")
      : (typeof offReason === "string" ? t(offReason) : "");
    if (why) row.title = why;
  }
  // fire() applies a per-control "not verified" confirm (c.confirm) on top of the feature-level
  // one in act(); c.confirm(value) returns the prompt, or null/false to skip.
  /** @param {string|number} val */
  const fire = (val) => {
    const msg = typeof c.confirm === "function" ? c.confirm(val, s) : c.confirm;
    if (msg && !confirm(msg)) return;
    act(fn, c.what, val);
  };
  if (c.kind === "toggle") {
    const on = optOn(fn, c.what, !!s[c.state]);
    const sw = document.createElement("button");
    sw.className = "switch" + (isPending ? " pending" : "");
    sw.setAttribute("role", "switch"); sw.setAttribute("aria-label", c.label);
    sw.setAttribute("aria-checked", on ? "true" : "false");
    sw.disabled = ro;
    sw.onclick = () => fire(on ? "off" : "on");
    row.appendChild(sw);
  } else if (c.kind === "slider") {
    const val = optNum(fn, c.what, /** @type {number} */ (s[c.state] != null ? s[c.state] : c.min));
    const inp = document.createElement("input");
    inp.type = "range";
    inp.min = /** @type {any} */ (c.min);
    inp.max = /** @type {any} */ (c.max);
    if (c.step) inp.step = String(c.step);
    inp.value = /** @type {any} */ (val);
    inp.disabled = ro;
    const out = document.createElement("span");
    out.className = "sval";
    out.textContent = val + (c.unit ? " " + c.unit : "");
    inp.oninput = () => (out.textContent = inp.value + (c.unit ? " " + c.unit : ""));
    inp.onchange = () => fire(Number(inp.value));
    row.appendChild(inp);
    row.appendChild(out);
  } else if (c.kind === "select") {              // segmented multi-choice (e.g. energy/cooler mode)
    const cur = optSel(fn, c.what, c.current ? c.current(s) : null);
    const grp = document.createElement("div");
    grp.className = "segmented";
    for (const opt of c.options) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "seg" + (opt.value === cur ? " on" : "");
      b.textContent = /** @type {string} */ (t(opt.label));
      b.disabled = ro;
      b.setAttribute("aria-pressed", opt.value === cur ? "true" : "false");
      b.onclick = () => fire(opt.value);
      grp.appendChild(b);
    }
    row.appendChild(grp);
  } else if (c.kind === "hour") {                // 0-23 hour picker (cooler night schedule)
    const cur = optNum(fn, c.what, c.current ? c.current(s) : 0);
    const sel = document.createElement("select");
    sel.disabled = ro;
    for (let h = 0; h < 24; h++) {
      const o = document.createElement("option");
      o.value = /** @type {any} */ (h); o.textContent = String(h).padStart(2, "0") + ":00";
      if (h === cur) o.selected = true;
      sel.appendChild(o);
    }
    sel.onchange = () => fire(Number(sel.value));
    row.appendChild(sel);
  } else if (c.kind === "time") {                // HH:MM time-of-day (air-heater start timer)
    const inp = document.createElement("input");
    inp.type = "time";
    inp.disabled = ro;
    if (c.current && c.current(s)) inp.value = /** @type {string} */ (c.current(s));
    inp.onchange = () => { if (inp.value) fire(inp.value); };
    row.appendChild(inp);
  } else if (c.kind === "buttons") {             // momentary action buttons (cooler on/off timer)
    const grp = document.createElement("div");
    grp.className = "segmented";
    for (const b of c.actions) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "seg";
      btn.textContent = /** @type {string} */ (t(b.label));
      btn.disabled = ro;
      btn.onclick = () => {
        const msg = typeof c.confirm === "function" ? c.confirm(b, s) : c.confirm;   // null = no prompt
        if (msg && !confirm(msg)) return;
        act(fn, b.what, b.value != null ? b.value : null);
      };
      grp.appendChild(btn);
    }
    row.appendChild(grp);
  }
  return row;
}

// Live vehicle-clock tick. The van's RTC arrives as a per-poll snapshot ("YYYY-MM-DD HH:MM:SS");
// rather than let it sit frozen between polls, we advance it by real elapsed time. We anchor to the
// instant the reading was actually taken (now - _meta.age_s), so the displayed clock = RTC value +
// wall-time since. Re-synced on every poll (render() rebuilds), smooth in between. Because the RTC
// tracks real time, this stays ~accurate even while the van is asleep — it's cosmetic; liveness is
// shown by the separate online/offline banner.
/** @type {ReturnType<typeof setInterval>|null} */
let clockTimer = null;
function stopClockTicker() { if (clockTimer) { clearInterval(clockTimer); clockTimer = null; } }
/**
 * @param {string|null|undefined} str
 * @returns {number|null}
 */
function parseClock(str) {
  const m = /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2}):(\d{2})/.exec(str || "");
  return m ? new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +m[6]).getTime() : null;  // local time
}
/** @param {number} ms */
function fmtClock(ms) {
  const d = new Date(ms), p = (/** @type {number} */ n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}
/** @param {HTMLElement} el */
function startClockTicker(el) {
  stopClockTicker();
  const base = parseClock(/** @type {FnState} */ (STATE.vehicle || {}).car_clock);
  if (base == null) { el.textContent = "—"; return; }
  const anchor = Date.now() - (STATE._meta && STATE._meta.age_s ? STATE._meta.age_s * 1000 : 0);
  const paint = () => { el.textContent = fmtClock(base + (Date.now() - anchor)); };
  paint();
  clockTimer = setInterval(paint, 1000);
}

/**
 * @param {Readout} r
 * @param {FnState} s
 * @returns {HTMLDivElement}
 */
function renderReadout(r, s) {
  const wrap = document.createElement("div");
  wrap.className = "rowwrap";
  const row = document.createElement("div");
  row.className = "row ro";
  const label = document.createElement("span");
  label.className = "lbl";
  label.textContent = /** @type {string} */ (t(r.label));
  const v = document.createElement("span");
  v.className = "val";
  /** @type {string|number|null|undefined} */
  let val;
  try {
    val = r.get(s);
  } catch (e) {
    val = "—";
  }
  v.textContent = val == null || val === "" ? "—" : /** @type {string} */ (t(String(val)));
  if (r.tick) startClockTicker(v);   // live-advance this value (vehicle clock) between polls
  row.appendChild(label);
  row.appendChild(v);
  wrap.appendChild(row);
  if (r.bar) {
    /** @type {number|null|undefined} */
    let p;
    try {
      p = r.bar(s);
    } catch (e) {
      p = null;
    }
    if (p != null) {
      const bar = document.createElement("div");
      bar.className = "bar";
      const fill = document.createElement("div");
      fill.className = "fill";
      fill.style.width = Math.max(0, Math.min(100, p)) + "%";
      bar.appendChild(fill);
      wrap.appendChild(bar);
    }
  }
  if (r.widget) {                    // optional richer visual (e.g. the leveling bubble)
    /** @type {Node|null} */
    let node;
    try { node = r.widget(s); } catch (e) { node = null; }
    if (node) wrap.appendChild(node);
  }
  return wrap;
}

function spinner() {
  const s = document.createElement("span");
  s.className = "spinner";
  return s;
}

// Auto camper mode toggle — a daemon SETTING (not a per-poll control), rendered on the Camping
// card. Restores camper mode + rear USB once you PARK (the unit refuses camping-on while driving);
// stands down on low battery. `armed` = a restore is owed (the engine shed camping).
function autoCamperCard() {
  const ac = /** @type {{enabled?: boolean, armed?: boolean, notice?: ?{ts:number,msg:string}}} */
    ((STATE._meta && STATE._meta.auto_camper) || {});
  const card = document.createElement("div"); card.className = "card";
  const row = document.createElement("div"); row.className = "row";
  const lbl = document.createElement("span"); lbl.className = "lbl";
  lbl.textContent = t("Restore camping after you park") + (ac.armed ? "  " + t("⟳ will restore on park") : "");
  row.appendChild(lbl);
  const sw = document.createElement("button");
  sw.className = "switch";
  sw.setAttribute("role", "switch"); sw.setAttribute("aria-label", "Auto camper mode");
  sw.setAttribute("aria-checked", ac.enabled ? "true" : "false");
  sw.disabled = readOnly();
  sw.onclick = async () => {
    sw.disabled = true;
    try {
      const r = await api("/api/auto_camper", { method: "POST", headers: { "Content-Type": "application/json" },
                                                body: JSON.stringify({ enabled: !ac.enabled }) });
      if (!r.ok) throw 0;
      toast(ac.enabled ? "Auto camper off" : "Auto camper on", "ok");
    } catch (e) { toast("Couldn't change auto camper", "error"); }
    await refreshState(true);
  };
  row.appendChild(sw); card.appendChild(row);
  return card;
}

// Re-press debounce (app-faithful, cross-check #2 2026-08-30). Without a roof view a move-start
// starts a fresh SafetyCounter; if a release is followed by an immediate re-press the unit
// sees a brand-new counter and withholds the motor another ~3 s (with the view the counter runs on). The app ignores a roof press within
// ~1 s of the last one, so button jitter can't stutter the drive. Module-level (not per-render): the
// state view re-renders every 2 s and would otherwise reset a closure-scoped timestamp. STOP is never
// debounced — a release must always cease the move.
const ROOF_REPRESS_MS = 1000;
let roofLastMoveStart = 0;
// The direction currently being HELD ("open"/"close"), or null. Module-level on purpose: the
// pointerdown handler's command() used to trigger a synchronous render() that rebuilt the whole
// screen and DETACHED the held button, so its closure-scoped pointerup never fired and the
// release never sent STOP — the roof kept moving until the server's Position auto-stop. Now the
// release is caught at document level (below), independent of which node the pointer lands on,
// and render() is suppressed while a move is held.
/** @type {?string} */
let roofHold = null;
/** Send STOP once for the held move (idempotent: a second release is a no-op).
 *
 * OUT-OF-BAND on purpose: the command queue is single-flight, and the "open"/"close" POST stays
 * in flight for as long as the server streams the move — so a STOP enqueued behind it would
 * never be sent until the move it is supposed to interrupt had already ended on its own. The
 * server handles roof STOP lock-free (`_roof_stop`), so it goes straight to the API. */
function roofRelease() {
  if (!roofHold) return;
  roofHold = null;
  api("/api/command", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ function: "roof", what: "stop", value: null, confirm: true }),
  }).catch((e) => toast("Roof STOP failed: " + String(e), "error"));
  render();                        // the held button may now be repainted
}
// Any release anywhere ends the move — the vehicle's own hold-to-move contract.
document.addEventListener("pointerup", roofRelease);
document.addEventListener("pointercancel", roofRelease);

// The roof VIEW (CAPTURE 2026-10-10): while the real app's roof screen is open it streams STOP frames
// with its SafetyCounter, so a press moves on an already-validated counter (no ~3 s withhold). The
// roof page tells the daemon the same: `view` on open, refreshed by the 2 s re-render (throttled to
// ROOF_VIEW_REFRESH_MS; the daemon lets it lapse after ~15 s without one), `leave` when the page is
// left or the tab is hidden. A held move keeps the view open server-side (render() is paused then).
const ROOF_VIEW_REFRESH_MS = 5000;
let roofViewAt = 0;
/** @param {string} action "view" | "leave" */
function roofViewPost(action) {
  api("/api/roof", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action }),
  }).catch(() => {});             // best-effort: the daemon's lapse ends a view we failed to leave
}
function roofViewSync() {
  const on = view === "roof" && isCalictl() && !readOnly() && document.visibilityState === "visible";
  if (on && Date.now() - roofViewAt >= ROOF_VIEW_REFRESH_MS) {
    roofViewAt = Date.now();
    roofViewPost("view");
  } else if (!on && roofViewAt) {
    roofViewAt = 0;
    roofViewPost("leave");
  }
}
document.addEventListener("visibilitychange", roofViewSync);

/** @param {FnState} s the decoded roof state (caller passes `STATE[fn] || {}`) */
function roofControls(s) {
  const card = document.createElement("div");
  card.className = "card";
  const warn = document.createElement("div");
  warn.className = "warn";
  warn.textContent = /** @type {string} */ (t("Roof control is safety-sensitive and not live-verified."));
  card.appendChild(warn);
  // No roof control on the ESP satellite (owner ruling): greyed, with the firmware's own words.
  if (satellite()) {
    const n = document.createElement("div"); n.className = "note";
    n.textContent = /** @type {string} */ (t(ELSEWHERE));
    card.appendChild(n);
  }
  // The app hard-blocks the roof MOVE (open/close) — ig/c.java j() "movable" — when the unit
  // reports InfoPopUp child_lock / error / driving / emergency_locked / not_possible / low_battery,
  // or Position == 15 (error). It does NOT block on sensor_error (that one is warn-only in the
  // app), so gate on an explicit set rather than "any alert". STOP stays available (release).
  // `s.alert` / `s.position_name` are the decoded InfoPopUp + Position (semantics.roof).
  const moveBlocked = ROOF_MOVE_BLOCK.has(/** @type {string} */ (s.alert)) || s.position_name === "error";
  const btns = document.createElement("div");
  btns.className = "btnrow";
  for (const dir of ["open", "close", "stop"]) {
    const b = document.createElement("button");
    b.className = "btn";
    b.textContent = cap(/** @type {string} */ (t(dir)));   // sentence case: "Open", "Öffnen"
    const blocked = dir !== "stop" && moveBlocked;
    b.disabled = readOnly() || blocked || satellite();
    if (blocked) b.title = /** @type {string} */ (t(ROOF_ALERT_MSG[/** @type {string} */ (s.alert)] || "Roof move blocked"));
    if (satellite()) b.title = /** @type {string} */ (t(ELSEWHERE));
    if (pending_is("roof", dir)) b.appendChild(spinner());
    if (dir === "stop") {
      b.onclick = () => command("roof", "stop", null);
    } else {
      // PRESS-AND-HOLD: the pop-top moves only while the button is held; releasing sends STOP
      // (interrupts the in-flight move server-side via _roof_stop). Mirrors the vehicle's own
      // hold-to-move control. Still UNVERIFIED end-to-end on this van.
      /** @param {PointerEvent} ev */
      const start = (ev) => {
        ev.preventDefault();
        if (readOnly() || roofHold || blocked || satellite()) return;
        if (Date.now() - roofLastMoveStart < ROOF_REPRESS_MS) return;  // debounce a too-quick re-press
        if (!confirm(tf("Roof {dir}: hold to move the pop-top (UNVERIFIED on this vehicle). Release to stop. Path clear?", { dir: t(dir) }))) return;
        roofHold = dir;                 // set BEFORE command(): it gates render() and the release
        roofLastMoveStart = Date.now();
        command("roof", dir, null);
      };
      b.addEventListener("pointerdown", start);
      // pointerup/pointercancel are handled ONCE at document level (roofRelease) so a release
      // always sends STOP even if this node was replaced meanwhile; sliding off the button
      // also ends the move, like letting go of the vehicle's own switch.
      b.addEventListener("pointerleave", roofRelease);
    }
    btns.appendChild(b);
  }
  card.appendChild(btns);
  return card;
}

// Live values: poll the state every 2 s while the page is visible. A hidden tab stops polling, so
// buspi's daemon sees the UI go idle and drops back from its watched cadence (5 s reads on a held
// session) to the idle one; coming back refreshes at once instead of showing stale numbers for up
// to a tick.
const STATE_POLL_MS = 2000;

// Offline app (R_UI_PWA_OFFLINE): on a secure origin (the tailnet HTTPS fronts; never plain http on
// the LAN) sw.js serves the cached shell, and the last /api/state lives in localStorage: shown at
// once, marked offline "as of" its poll, until the first live poll answers.
const PWA = window.isSecureContext && "serviceWorker" in navigator;
const LAST_STATE_KEY = "calictl.last_state";

function restoreLastState() {
  try {
    const saved = JSON.parse(localStorage.getItem(LAST_STATE_KEY) || "null");
    if (!saved || !saved.state || !saved.state._meta) return;
    const m = saved.state._meta;
    const seen = m.last_seen != null ? m.last_seen : saved.at / 1000 - (m.age_s || 0);
    m.online = false;   // nothing is live until a poll says so: the offline banner + its age,
    m.read_only = true; // and controls greyed (a write could not reach the daemon anyway)
    m.restored = true;  // the offline banner explains it: no read-only banner on top
    m.last_seen = seen;
    m.age_s = Math.max(0, Math.round(Date.now() / 1000 - seen));
    STATE = saved.state;
    stateAt = Date.now();
    lastAnswerAt = saved.at;   // "not reachable since" the snapshot was saved
    render();
  } catch (e) { /* storage blocked or a corrupt entry: start empty, as without it */ }
}

if (PWA) {
  navigator.serviceWorker.register("/sw.js").catch(() => { /* no offline shell; the page works without */ });
  navigator.serviceWorker.onmessage = (ev) => {
    if (ev.data !== "shell-updated" || document.querySelector(".toast.update")) return;
    const d = toast("New version — tap to reload", "update", 0);
    d.setAttribute("role", "button");
    d.tabIndex = 0;
    d.onclick = () => location.reload();
    d.onkeydown = (k) => { if (k.key === "Enter" || k.key === " ") location.reload(); };
  };
  restoreLastState();
}

// User input keeps the satellite redialling after the unit's power-save hang-up (R_FW_BLE_VIEWER_KEEP:
// 10 min without input -> rest); a poll alone is no input. At most one report per INPUT_REPORT_MS.
const INPUT_REPORT_MS = 30000;
let inputAt = 0;
function reportInput() {
  if (!satellite() || readOnly() || Date.now() - inputAt < INPUT_REPORT_MS) return;
  inputAt = Date.now();
  satFetch("/api/ble", { active: true }).catch(() => { /* the next input reports again */ });
}
addEventListener("pointerdown", reportInput, true);
addEventListener("keydown", reportInput, true);

async function main() {
  paintLink();                // "connecting…" at once, not "…" while a hung first poll waits 8 s
  await refreshState(true);   // also takes the one-off /api/pairing once a calictl `_meta` answers
  render();
  const nav = /** @type {PerformanceNavigationTiming|undefined} */ (performance.getEntriesByType("navigation")[0]);
  ux({ type: "view", screen: uxId(view) });
  const ms = (/** @type {number} */ v) => Math.min(600000, Math.max(0, Math.round(v)));   // the endpoints' range
  ux({ type: "timing", ttfb: ms(nav ? nav.responseStart : 0), dcl: ms(nav ? nav.domContentLoadedEventEnd : 0),
    tiles: ms(performance.now()) });   // first render of the tiles, ms since navigation start
  // the tick repaints the link state even while a poll hangs (refreshState then returns early)
  setInterval(() => { if (!document.hidden) { refreshState(false); paintLink(); } }, STATE_POLL_MS);
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) return;
    if (waitingSince != null) waitingSince = Date.now();   // the hidden time does not count down
    refreshState(true);
    paintLink();
  });
}
main();
