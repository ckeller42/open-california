// @ts-check
// OpenCalifornia web UI — curated camper Vehicle-tab controls, driven by /api/state.
//
// Rather than mechanically render the reverse-engineered screen spec, each feature is a
// small hand-authored spec: the actual controls (mapped to the /api/command `what` tokens)
// plus the data readouts pulled from the interpreted /api/state. Labels are our own neutral
// English (no VW app text). Actuation is slow (BLE), so every command shows a spinner on the
// tapped control and a result toast; idle polls don't re-render (no flicker).
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
 * `serve.state()::_meta`. `session`/`session_mode` are `"off"` unless the persistent session is on.
 * @typedef {Object} Meta
 * @property {number|null} last_seen        epoch seconds of the last successful poll (null = none yet)
 * @property {number|null} age_s            seconds since last_seen (null = none yet)
 * @property {boolean} online               van currently reachable
 * @property {boolean} read_only            daemon rejects control writes
 * @property {'off'|'connecting'|'up'|'degraded'|'asleep'} session   persistent BLE session state
 * @property {'off'|'auto'|'release'} session_mode                    activity-scoped vs user-disconnected
 * @property {{enabled:boolean, armed:boolean, notice:?{ts:number,msg:string}}} [auto_camper] auto-camper toggle + notice
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
 * @property {string|null} [fault]
 * @property {boolean} [door_open]
 * @property {boolean} [error]
 * // campingmode (semantics.campingmode)
 * @property {boolean} [master_on]
 * @property {boolean} [usb_charger]
 * @property {boolean} [lights_on]
 * @property {boolean} [outputs_controllable]
 * @property {boolean} [enable]
 * // lighting (semantics.lighting): brightness_zone_1..16 read dynamically -> see note at renderLighting
 * @property {number|null} [profile]
 * @property {boolean} [any_on]
 * // airheater (semantics.airheater)
 * @property {boolean} [running]
 * @property {boolean} [permanent]
 * @property {number|null} [error_code]
 * @property {number|null} [air_distribution]
 * @property {number|null} [running_time]
 * @property {number|null} [running_time_remaining]
 * // water (semantics.water) + serve.state() stale-hold
 * @property {Tank} [fresh]
 * @property {Tank} [waste]
 * @property {number|null} [stale_since]
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
 * @property {number|null} [level_popup]
 * @property {number|null} [level_roll]
 * @property {number|null} [level_pitch]
 * @property {string|null} [car_clock]
 */
/**
 * Full `/api/state` payload: one `FnState` per function, plus the non-function `_meta` block.
 * @typedef {{ [fn: string]: FnState } & { _meta?: Meta }} State
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
 * @typedef {{ ok?: boolean, applied?: boolean|null, refused?: string, state?: FnState|null, error?: string|null, function?: string }} CommandResponse
 * @typedef {{ samples: number[][], gap_s: number, now: number, hours: number, error?: string }} BattHistory
 * @typedef {{ idx: number, cls: string, pad: number, lblCls: string, name: string, unit?: string }} SeriesCfg
 */
//
// --- FEATURES config shapes -------------------------------------------------------------------
/**
 * A control's per-control confirm gate: a function returning the prompt (or null/false to skip),
 * or a bare string. The callback arg varies by kind (value for most, the action object for buttons).
 * @typedef {((v?: any) => (string|null|false|undefined)) | string} ConfirmSpec
 * @typedef {{ value: string, label: string }} SelectOption
 * @typedef {{ what: string, label: string, value?: string|number|null }} ButtonAction
 * @typedef {{ what: string, label: string, confirm?: ConfirmSpec, disabled?: (s: FnState) => (boolean|undefined) }} ControlBase
 * @typedef {ControlBase & { kind: 'toggle', state: keyof FnState }} ToggleControl
 * @typedef {ControlBase & { kind: 'slider', state: keyof FnState, min: number, max: number, unit?: string }} SliderControl
 * @typedef {ControlBase & { kind: 'select', options: SelectOption[], current?: (s: FnState) => (string|null|undefined) }} SelectControl
 * @typedef {ControlBase & { kind: 'hour', current?: (s: FnState) => number }} HourControl
 * @typedef {ControlBase & { kind: 'time', current?: (s: FnState) => (string|null) }} TimeControl
 * @typedef {ControlBase & { kind: 'buttons', actions: ButtonAction[] }} ButtonsControl
 * @typedef {ToggleControl|SliderControl|SelectControl|HourControl|TimeControl|ButtonsControl} Control
 */
/**
 * A live readout row. `get` formats the value; `bar` (0-100) draws a fill; `widget` returns a
 * richer node (the leveling bubble); `tick` live-advances the value (vehicle clock).
 * @typedef {{ label: string, get: (s: FnState) => (string|number|null|undefined), bar?: (s: FnState) => (number|null|undefined), widget?: (s: FnState) => (Node|null), tick?: boolean }} Readout
 */
/**
 * A curated feature card. `lighting`/`roof` swap in a custom renderer; `chart` appends the battery
 * chart; `warn`/`note` are dynamic banners; `confirm` is a feature-level actuation gate.
 * @typedef {Object} Feature
 * @property {string} title
 * @property {string} [icon]
 * @property {Control[]} [controls]
 * @property {Readout[]} [readouts]
 * @property {(s: FnState) => string} [summary]
 * @property {(s: FnState) => (string|null)} [warn]
 * @property {(s: FnState) => (string|null)} [note]
 * @property {(w: string) => string} [confirm]
 * @property {boolean} [lighting]
 * @property {boolean} [roof]
 * @property {boolean} [chart]
 */

const app = /** @type {HTMLElement} */ (document.getElementById("app"));
const titleEl = /** @type {HTMLElement} */ (document.getElementById("title"));
const backEl = /** @type {HTMLElement} */ (document.getElementById("back"));
const statusEl = /** @type {HTMLElement} */ (document.getElementById("status"));
const menuEl = /** @type {HTMLElement} */ (document.getElementById("menu"));

// --- topbar context menu (UX: pairing chrome lives here, not in the main flow) ---------------
let menuPop = /** @type {?HTMLElement} */ (null);

function closeMenu() {
  if (menuPop) { menuPop.remove(); menuPop = null; }
}

menuEl.onclick = (ev) => {
  ev.stopPropagation();
  if (menuPop) { closeMenu(); return; }
  menuPop = document.createElement("div");
  menuPop.className = "menupop";
  const pair = document.createElement("button");
  pair.type = "button";
  pair.textContent = "Bluetooth pairing…";
  pair.onclick = () => { closeMenu(); openPairingWizard(); };
  menuPop.appendChild(pair);
  // Unpair only makes sense once a bond exists (PAIRING is fetched at load + while the wizard polls).
  if (PAIRING && PAIRING.address) {
    const unpair = document.createElement("button");
    unpair.type = "button";
    unpair.textContent = "Unpair…";
    unpair.onclick = async () => {
      closeMenu();
      if (!confirm("Unpair removes the working bond; telemetry stops until re-paired. Continue?")) return;
      await openPairingWizard();               // show the flow while the reset runs
      pairingAction("reset", undefined, true); // then guides straight into re-pairing (idle step)
    };
    menuPop.appendChild(unpair);
  }
  document.body.appendChild(menuPop);
  // close on any outside click/tap (registered after this event finishes bubbling)
  setTimeout(() => document.addEventListener("click", closeMenu, { once: true }), 0);
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
let lastAcNotice = 0;    // ts of the last auto-camper notice shown as a toast (dedupe)

/** @type {(fn: string, what: string) => string} */
const qKey = (fn, what) => fn + "·" + what;
const hasWork = () => !!inflight || queue.length > 0;
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
const yn = (b) => (b ? "yes" : "no");
/** @type {(b: unknown) => string} */
const onoff = (b) => (b ? "On" : "Off");
// Format a numeric value with a unit, but never print "null V"/"undefined %": a null/undefined
// reading (e.g. starter battery when engine-off) renders as an em-dash instead.
/** @type {(v: number|null|undefined, u: string) => string} */
const withUnit = (v, u) => (v == null ? "—" : `${v} ${u}`);
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
  door_open: "⚠ Fridge door is open",
  emergency: "⚠ Cooler in emergency operation",
  error: "⚠ Cooler error",
};

// Roof InfoPopUp alert -> banner text (see semantics.roof / ig/c.java).
/** @type {Record<string, string>} */
const ROOF_ALERT_MSG = {
  child_lock: "⚠ Roof child lock active",
  error: "⚠ Roof error",
  sensor_error: "⚠ Roof sensor error",
  emergency_locked: "⚠ Roof emergency-locked",
  not_possible: "⚠ Roof operation not possible right now",
  low_battery: "⚠ Battery too low to operate roof",
};

const ORDER = ["cooler", "campingmode", "lighting", "airheater", "water", "energy", "roof", "vehicle"];
/** @type {Record<string, Feature>} */
const FEATURES = {
  cooler: {
    title: "Cooler", icon: "❄️",
    controls: [
      { what: "power", kind: "toggle", label: "Refrigerator", state: "on" },
      { what: "level", kind: "slider", label: "Cooling level", state: "level", min: 1, max: 5 },
      { what: "mode", kind: "select", label: "Quiet mode",
        options: [{ value: "normal", label: "Normal" }, { value: "quiet", label: "Quiet" }, { value: "timer_quiet", label: "Timer quiet" }],
        current: (s) => (s.mode === 2 ? "quiet" : s.mode === 4 ? "timer_quiet" : "normal"),
        confirm: () => "Set the cooler's quiet mode? Not yet verified on the van. Continue?" },
      { what: "night_on", kind: "hour", label: "Quiet from", current: (s) => s.quiet_from ?? 0,
        confirm: (h) => `Set quiet-schedule start to ${String(h).padStart(2, "0")}:00? Not verified on the van. Continue?` },
      { what: "night_off", kind: "hour", label: "Quiet until", current: (s) => s.quiet_to ?? 0,
        confirm: (h) => `Set quiet-schedule end to ${String(h).padStart(2, "0")}:00? Not verified on the van. Continue?` },
      { what: "timer_set", kind: "time", label: "Timer start at",
        current: (s) => (s.timer_hour != null && s.timer_min != null)
          ? String(s.timer_hour).padStart(2, "0") + ":" + String(s.timer_min).padStart(2, "0") : null,
        confirm: (t) => `Set the cooling-timer start to ${t}? Not yet verified on the van. Continue?` },
      { what: "__cooltimer", kind: "buttons", label: "Cooling timer",
        actions: [{ what: "timer_start", label: "Arm" }, { what: "timer_cancel", label: "Cancel" }],
        confirm: (b) => `${b.label} the cooling timer? Not yet verified on the van. Continue?` },
    ],
    readouts: [
      { label: "Fridge door", get: (s) => (s.door_open ? "⚠ Open" : "Closed") },
      { label: "Timer", get: (s) => onoff(s.timer_active) },
      { label: "Quiet schedule", get: (s) => (s.quiet_scheduled || s.quiet_from || s.quiet_to)
        ? `${String(s.quiet_from ?? 0).padStart(2, "0")}:00 → ${String(s.quiet_to ?? 0).padStart(2, "0")}:00`
          + (s.quiet_scheduled ? "" : " (off)") : "off" },
    ],
    // `fault` is the cooler's 2-bit Error enum, only meaningful while powered on (vf/c.java):
    // door_open | emergency | error. Surface it as a banner so a fault is obvious at a glance.
    warn: (s) => (COOLER_FAULT_MSG[/** @type {string} */ (s.fault)] || null),
    summary: (s) => (COOLER_FAULT_MSG[/** @type {string} */ (s.fault)] || (s.on ? `On · level ${s.level}` : "Off")),
  },
  campingmode: {
    title: "Camping mode", icon: "🏕️",
    controls: [
      { what: "master", kind: "toggle", label: "Camping mode", state: "master_on" },
      { what: "lights", kind: "toggle", label: "Interior + outside lights", state: "lights_on" },
      { what: "usb", kind: "toggle", label: "Rear USB ports", state: "usb_charger" },
    ],
    readouts: [{ label: "Ignition (terminal-15)", get: (s) => onoff(s.enable) }],
    summary: (s) => onoff(s.master_on),
  },
  lighting: {
    title: "Lighting", icon: "💡", lighting: true,   // custom renderer (renderLighting)
    summary: (s) => onoff(s.any_on),
  },
  airheater: {
    title: "Air heater", icon: "🔥", confirm: (w) => `Start the fuel-burning parking heater (${w})? It is not live-verified. Continue?`,
    controls: [
      { what: "power", kind: "toggle", label: "Parking heater", state: "running" },
      { what: "level", kind: "slider", label: "Heating level (10 = HI)", state: "level", min: 1, max: 10 },
      { what: "runtime", kind: "slider", label: "Run time", state: "running_time", min: 0, max: 120, unit: "min" },
      { what: "timer", kind: "time", label: "Start at",
        current: (s) => (s.timer_hour != null && s.timer_min != null)
          ? String(s.timer_hour).padStart(2, "0") + ":" + String(s.timer_min).padStart(2, "0") : null },
    ],
    readouts: [
      { label: "Level", get: (s) => (s.level == null ? "—" : (s.level >= 10 ? "HI" : s.level)) },
      { label: "Running time", get: (s) => withUnit(s.running_time, "min") },
      { label: "Timer start", get: (s) => (s.timer_hour != null && s.timer_min != null && (s.timer_hour || s.timer_min))
        ? String(s.timer_hour).padStart(2, "0") + ":" + String(s.timer_min).padStart(2, "0") : "off" },
      { label: "Error code", get: (s) => s.error_code ?? "—" },
    ],
    summary: (s) => (s.running ? `Running${s.level != null ? " · " + (s.level >= 10 ? "HI" : s.level) : ""}` : "Off"),
  },
  water: {
    title: "Water", icon: "💧",
    readouts: [
      { label: "Fresh water", get: (s) => tank(s.fresh) + (s.fresh && s.fresh.stale ? "  🕒 last measured" : ""),
        bar: (s) => s.fresh && s.fresh.percent },
      { label: "Waste water", get: (s) => tank(s.waste) + (s.waste && s.waste.stale ? "  🕒 last measured" : ""),
        bar: (s) => s.waste && s.waste.percent },
    ],
    // Water is READ-ONLY on BLE (char 1302) and the unit only re-measures while the van's water
    // system runs — confirmed 2026-08-19: no BLE activity (not even the 1003 heartbeat) triggers a
    // refresh, so the char just holds the LAST MEASURED level until the pump/system next runs. This
    // is a soft `note` (informational, expected while parked) — NOT a `warn`/fault. See
    // docs/business-logic/value-freshness.md.
    note: (s) => ((s.fresh && s.fresh.stale) || (s.waste && s.waste.stale)
      ? "🕒 Showing the LAST MEASURED water level"
        + (s.stale_since ? " (" + agoText(Math.round(Date.now() / 1000 - s.stale_since)) + " ago)" : "")
        + " — the BLE level only refreshes while the van's water system is running, so it lags until "
        + "the pump next runs. It's read correctly, just not live."
      : null),
    summary: (s) => (s.fresh ? `Fresh ${s.fresh.percent}%${s.fresh.stale ? " (last meas.)" : ""}` : ""),
  },
  energy: {
    title: "Energy", icon: "🔋", chart: true,
    controls: [
      { what: "mode", kind: "select", label: "Energy mode",
        options: [{ value: "normal", label: "Normal" }, { value: "max_charge", label: "Max charge" }, { value: "eco", label: "Eco" }],
        current: (s) => (/** @type {Record<number, string>} */ ({ 0: "normal", 1: "max_charge", 2: "eco" }))[/** @type {number} */ (s.energy_mode)],
        disabled: (s) => !!s.energy_mode_locked,
        confirm: () => "Set the energy management mode? This control is derived from the app and not yet verified on the van. Continue?" },
    ],
    readouts: [
      { label: "Living battery", get: (s) => (s.soc2_pct != null ? `${s.soc2_pct}%` : "—"), bar: (s) => s.soc2_pct },
      { label: "Living voltage", get: (s) => withUnit(s.batt2_v, "V") },
      { label: "Living current", get: (s) => withUnit(s.batt2_current, "A") },
      { label: "Time remaining", get: (s) => (s.batt2_remaining_h != null ? `${s.batt2_remaining_h} h` : "—") },
      { label: "Starter battery", get: (s) => (s.soc1_pct != null ? `${s.soc1_pct}%` : "—"), bar: (s) => s.soc1_pct },
      { label: "Starter voltage", get: (s) => withUnit(s.batt1_v, "V") },
      { label: "Starter current", get: (s) => withUnit(s.batt1_current, "A") },
      { label: "DC-DC charger", get: (s) => (s.dcdc_installed ? `${s.dcdc_state} (${s.dcdc_power} W · ${s.dcdc_current} A)` : "—") },
      { label: "Shore power", get: (s) => (s.shore_installed ? `${s.shore_state} (${s.shore_power} W · ${s.shore_current} A)` : "—") },
      { label: "Solar", get: (s) => (s.solar_installed ? `${s.solar_state} (${s.solar_power} W · ${s.solar_current} A)` : "not installed") },
      { label: "Warnings", get: (s) => (s.faults && s.faults.length ? s.faults.join(", ") : "none") },
      // The unit reports an age ONLY for the STARTER battery (AgeOneBattValuesMinutes); 255 = the
      // stale sentinel (starter subsystem asleep). The leisure battery has no such field — it is
      // measured continuously, so its freshness is just the daemon's read age. Label it as STARTER
      // so it isn't mistaken for the leisure data.
      { label: "Starter data age", get: (s) => (s.stale ? "🕒 stale (starter asleep)"
        : s.age_min != null ? `${s.age_min} min` : "—") },
    ],
    // Soft note (not a fault): the starter battery is only measured engine-on, so its values latch
    // stale when parked. The leisure battery stays live. Mirrors the water stale-hold pattern.
    note: (s) => (s.stale ? "🕒 Starter-battery values are stale — that subsystem only measures "
      + "with the engine on, so it holds the last reading while parked. The leisure battery stays live."
      : null),
    summary: (s) => (s.soc2_pct != null ? `Battery ${s.soc2_pct}%` : ""),
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
    summary: (s) => (s.alert ? `⚠ ${s.alert.replace(/_/g, " ")}`
                     : (s && s.position_name ? cap(s.position_name) : "")),
  },
  vehicle: {
    title: "Vehicle", icon: "🚗",
    readouts: [
      { label: "Ignition", get: (s) => onoff(s.ignition_on) },
      { label: "Leveling (roll / pitch)",
        get: (s) => (s.level_roll == null || s.level_pitch == null
          ? "—" : `${fmtDeg(s.level_roll)} / ${fmtDeg(s.level_pitch)}`),
        widget: (s) => bubbleLevel(s.level_roll, s.level_pitch) },
      { label: "Vehicle clock", get: (s) => s.car_clock ?? "—", tick: true },
      { label: "Firmware (amb · cm · comm)",
        get: () => {
          const fw = (STATE._meta && STATE._meta.firmware) || {};
          if (fw.amb_sw_version == null && fw.comm_version == null) return "—";
          return `${fw.amb_sw_version ?? "?"} · ${fw.cm_sw_version ?? "?"} · ${fw.comm_version ?? "?"}`
            + (fw.untested ? "  ⚠ untested" : "  ✓ tested");
        } },
    ],
    summary: (s) => (s.ignition_on ? "Ignition on" : "Parked"),
  },
};

/**
 * @param {string} path
 * @param {RequestInit} [opts]
 * @returns {Promise<any>}
 */
async function api(path, opts) {
  const r = await fetch(path, opts);
  return r.json();
}

/**
 * @param {string} text
 * @param {string} [kind]
 */
function setStatus(text, kind) {
  statusEl.textContent = text;
  statusEl.className = "pill" + (kind ? " " + kind : "");
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
const readOnly = () => !!(STATE._meta && STATE._meta.read_only);

/**
 * @param {string} msg
 * @param {string} [kind]
 */
function toast(msg, kind) {
  const d = document.createElement("div");
  d.className = "toast" + (kind ? " " + kind : "");
  d.textContent = msg;
  (/** @type {HTMLElement} */ (document.getElementById("toasts"))).appendChild(d);
  setTimeout(() => d.remove(), 4000);
}

/** @param {boolean} [force] */
async function refreshState(force) {
  let next;
  try {
    next = await api("/api/state");
  } catch (e) {
    setStatus("offline", "error");
    return;
  }
  STATE = next;
  // Auto-camper give-up/stand-down notice: the daemon stamps a one-time {ts,msg} when it stands
  // down (low battery) or gives up (keeps dropping). Show it as a toast once.
  const acn = STATE._meta && STATE._meta.auto_camper && STATE._meta.auto_camper.notice;
  if (acn && acn.ts && acn.ts > lastAcNotice) { lastAcNotice = acn.ts; toast(acn.msg, "warn"); }
  // "live" = the van is currently reachable; "offline" = daemon can't reach it (van asleep),
  // matching the banner. The web UI itself is up either way — this reflects the vehicle link.
  if (!hasWork()) {
    const vanOnline = !STATE._meta || STATE._meta.online;
    setStatus(vanOnline ? "live" : "offline", vanOnline ? "ok" : "warn");
  }
  // don't repaint out from under a control the user is actively using — a poll-driven re-render
  // rebuilds the DOM (app.innerHTML="") and would slam an OPEN <select> dropdown shut, or interrupt
  // a slider drag / time entry. Skip while such a control is focused; optimistic values + the queue
  // keep the rest fresh, and a forced refresh after a command still repaints.
  const ae = /** @type {HTMLInputElement} */ (document.activeElement);
  if (!force && ae && (ae.type === "range" || ae.type === "time" || ae.tagName === "SELECT"
      || ae.id === "pairing-passkey")) return;   // don't clobber a mid-typed pairing passkey either
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
  const key = qKey(fn, what);
  optimistic[key] = value;
  const i = queue.findIndex((c) => c.key === key);   // prune a superseded queued command
  if (i >= 0) queue.splice(i, 1);
  queue.push({ fn, what, value, key });
  render();
  processQueue();
}

// Drain the queue one command at a time (BLE is serial). Always sends confirm:true — the
// frontend already showed any confirm dialog; the backend only needs it for roof/airheater.
async function processQueue() {
  if (inflight || !queue.length) return;
  inflight = /** @type {CommandItem} */ (queue.shift());
  setStatus("Sending…", "busy");
  render();
  /** @type {CommandResponse} */
  let res;
  try {
    res = await api("/api/command", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ function: inflight.fn, what: inflight.what, value: inflight.value, confirm: true }),
    });
  } catch (e) {
    res = { ok: false, error: String(e) };
  }
  const done = inflight;
  inflight = null;
  if (res && res.ok && res.refused) toast(res.refused, "warn");   // physical precondition not met
  else if (res && res.ok && res.applied === true) toast("✓ Applied", "ok");
  // applied === null: sent + acknowledged but not verifiable remotely. For lighting the state
  // char is a write-through echo, so only the lamp itself is proof — say so. Other functions
  // that return null (e.g. roof, which has no readback check) keep the neutral phrasing.
  else if (res && res.ok && res.applied == null && res.function === "lighting") toast("Sent — check the lamp", "ok");
  else if (res && res.ok) toast("Sent — the unit didn't confirm it", "warn");
  else toast("Command failed" + (res && res.error ? `: ${res.error}` : ""), "error");
  // drop the optimistic value only if no newer change for this control is still queued
  if (!queue.some((c) => c.key === done.key)) delete optimistic[done.key];
  await refreshState(true);
  processQueue();
}

// Actuate a control, gating fuel/motion features behind a confirm() dialog.
/**
 * @param {string} fn
 * @param {string} what
 * @param {string|number|null} value
 */
function act(fn, what, value) {
  const f = FEATURES[fn];
  if (f && f.confirm && !confirm(f.confirm(what))) return;
  command(fn, what, value);
}

/** @param {string} v */
function goto(v) {
  view = v;
  lastRender = "";
  render();
}

/**
 * @param {number|null|undefined} sec
 * @returns {string}
 */
function agoText(sec) {
  if (sec == null) return "unknown";
  if (sec < 90) return "just now";
  const m = Math.round(sec / 60);
  if (m < 90) return m + " min ago";
  const h = Math.round(m / 60);
  return h < 48 ? h + " h ago" : Math.round(h / 24) + " d ago";
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
  const t = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const sameDay = d.toDateString() === new Date().toDateString();
  return sameDay ? t : `${d.toLocaleDateString([], { month: "short", day: "numeric" })} ${t}`;
}

// The camper unit deep-sleeps when the van is parked and becomes unreachable — so when the daemon
// hasn't polled recently, show the LAST known values behind a clear "offline — last seen X" banner
// rather than a blank screen or a stale number pretending to be live.
function offlineBanner() {
  const m = STATE._meta;
  if (!m || m.online) return null;
  const b = document.createElement("div");
  b.className = "offline";
  b.textContent = m.last_seen
    ? `Offline — van asleep. Last data ${clockText(m.last_seen)} (${agoText(m.age_s)}).`
    : `Offline — no data yet (van asleep since the monitor started). Checked ${clockText(Date.now() / 1000)}.`;
  return b;
}

// Connection toggle for the persistent BLE session. The van has ONE BLE slot shared with the
// phone app, so this both SHOWS the state and lets you Disconnect (hand the slot back to the app
// immediately, instead of waiting out the idle release) or reconnect after you have.
// It is shown ONLY when a session is actually held/connecting, the van is asleep, or you chose to
// disconnect. When the session is merely idle in auto mode the daemon is still polling normally —
// showing a "Connect" button there falsely reads as "no connection", so we hide it (the fast path
// warms automatically the moment you interact). The main status pill still shows live/offline.
function sessionToggle() {
  const m = /** @type {Meta} */ (STATE._meta || {});
  const mode = m.session_mode, st = m.session;
  if (mode == null || mode === "off") return null;      // persistent session disabled -> no toggle
  let spec, action;
  if (mode === "release") { spec = { cls: "", text: "🔌 Disconnected" }; action = "connect"; }
  else if (st === "up") { spec = { cls: "ok", text: "🟢 Live · fast" }; action = "disconnect"; }
  else if (st === "connecting" || st === "degraded") { spec = { cls: "busy", text: "🟡 Connecting" }; action = "disconnect"; }
  else if (st === "asleep") { spec = { cls: "", text: "⚪ Asleep — tap to wake" }; action = "connect"; }
  else return null;   // idle in auto mode while polling normally — not a disconnection, don't imply one
  const el = document.createElement("button");
  el.type = "button";
  el.className = "session-pill" + (spec.cls ? " " + spec.cls : "");
  el.textContent = spec.text;
  el.title = action === "connect"
    ? "Connect the fast BLE session (warm it before controlling)"
    : "Disconnect — free the BLE slot for the phone app";
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

/** @type {(d: number) => string} */
const fmtDeg = (d) => (d > 0 ? "+" : "") + d + "°";

// A spirit-level "bubble" for the 2-axis van leveling: a target with a bubble offset by roll (x)
// and pitch (y). Returns null when there's no reading (ignition off) so the row just shows "—".
/**
 * @param {number|null|undefined} roll
 * @param {number|null|undefined} pitch
 * @returns {HTMLDivElement|null}
 */
function bubbleLevel(roll, pitch) {
  if (roll == null || pitch == null) return null;
  const MAX = 6, R = 70, ring = 64, br = 13, reach = ring - br;   // ±6° maps to the outer ring
  /** @type {(v: number) => number} */
  const clamp = (v) => Math.max(-1, Math.min(1, v / MAX));
  const bx = (R + clamp(roll) * reach).toFixed(1);
  const by = (R - clamp(pitch) * reach).toFixed(1);               // +pitch (nose up) => bubble up
  const level = Math.abs(roll) < 1 && Math.abs(pitch) < 1;
  const wrap = document.createElement("div");
  wrap.className = "bubble-wrap";
  wrap.innerHTML =
    `<svg viewBox="0 0 ${2 * R} ${2 * R}" class="bubble-level${level ? " level" : ""}" role="img" aria-label="leveling">` +
    `<circle cx="${R}" cy="${R}" r="${ring}" class="bl-ring"/>` +
    `<circle cx="${R}" cy="${R}" r="42" class="bl-ring"/>` +
    `<circle cx="${R}" cy="${R}" r="20" class="bl-ring"/>` +
    `<line x1="${R}" y1="6" x2="${R}" y2="${2 * R - 6}" class="bl-cross"/>` +
    `<line x1="6" y1="${R}" x2="${2 * R - 6}" y2="${R}" class="bl-cross"/>` +
    `<circle cx="${bx}" cy="${by}" r="${br}" class="bl-bubble"/></svg>` +
    `<div class="bl-nums">roll ${fmtDeg(roll)} · pitch ${fmtDeg(pitch)}${level ? " · level ✓" : ""}</div>`;
  return wrap;
}

// --- 24 h leisure-battery chart ------------------------------------------------------------
// The series comes from the daemon's own append-only history (/api/history), NOT from InfluxDB:
// the GUI is Influx-free by design, so the chart must still draw with Influx/Grafana down.
// History is cached client-side because renderState runs every 2 s and the window is ~2880
// samples -- refetching that on every tick would be pointless traffic.
const CHART = { w: 320, h: 132, l: 34, r: 34, t: 10, b: 18 };
const HISTORY_TTL_MS = 60000;
/** @type {BattHistory|null} */
let HISTORY = null, HISTORY_TS = 0;

async function loadHistory() {
  if (HISTORY && Date.now() - HISTORY_TS < HISTORY_TTL_MS) return HISTORY;
  HISTORY = await api("/api/history?h=24");
  HISTORY_TS = Date.now();
  return HISTORY;
}

// Split samples into runs that are contiguous in time. A jump longer than `gapS` means the van
// was asleep (or the daemon was down), so the line BREAKS there. Joining across it would draw a
// voltage the battery never actually held -- the chart would be inventing data.
/**
 * @param {number[][]} samples
 * @param {number} gapS
 * @returns {number[][][]}
 */
function contiguousRuns(samples, gapS) {
  const runs = [];
  let cur = [];
  for (const s of samples) {
    if (cur.length && s[0] - cur[cur.length - 1][0] > gapS) { runs.push(cur); cur = []; }
    cur.push(s);
  }
  if (cur.length) runs.push(cur);
  return runs;
}

// Min/max of column `i`, widened to at least `pad` so a dead-flat line renders mid-plot
// instead of dividing by a zero range.
/**
 * @param {number[][]} samples
 * @param {number} i
 * @param {number} pad
 * @returns {number[]|null}
 */
function extent(samples, i, pad) {
  let lo = Infinity, hi = -Infinity;
  for (const s of samples) {
    const v = s[i];
    if (typeof v === "number" && isFinite(v)) { lo = Math.min(lo, v); hi = Math.max(hi, v); }
  }
  if (!isFinite(lo)) return null;
  if (hi - lo < pad) { const mid = (hi + lo) / 2; lo = mid - pad / 2; hi = mid + pad / 2; }
  return [lo, hi];
}

// One series (voltage OR current) on its own axis — the two are split into separate diagrams
// because they live on very different scales (V ~13-14, A can swing -2..+8), so overlaying them
// squashes the voltage line flat. cfg: {idx, cls, pad, lblCls, name, unit}.
/**
 * @param {BattHistory} h
 * @param {SeriesCfg} cfg
 * @returns {SVGSVGElement|null}
 */
function seriesSvg(h, cfg) {
  const samples = (h && h.samples) || [];
  const e = extent(samples, cfg.idx, cfg.pad);
  if (!samples.length || !e) return null;
  const C = CHART, hours = h.hours || 24;
  const t1 = h.now, t0 = t1 - hours * 3600;          // a TRUE 24 h axis: gaps stay gaps
  const plotW = C.w - C.l - C.r, plotH = C.h - C.t - C.b;
  /** @type {(ts: number) => number} */
  const x = (ts) => C.l + ((ts - t0) / (t1 - t0)) * plotW;
  /** @type {(val: number) => number} */
  const y = (val) => C.t + plotH - ((val - e[0]) / (e[1] - e[0])) * plotH;
  const line = contiguousRuns(samples, h.gap_s || 120).map((run) => {
    const pts = run.filter((s) => typeof s[cfg.idx] === "number" && isFinite(s[cfg.idx]));
    if (!pts.length) return "";
    if (pts.length === 1)                            // a lone sample has no line -- show the point
      return `<circle class="${cfg.cls}-dot" cx="${x(pts[0][0]).toFixed(1)}" cy="${y(pts[0][cfg.idx]).toFixed(1)}" r="1.6"/>`;
    return `<polyline class="${cfg.cls}" points="${
      pts.map((s) => `${x(s[0]).toFixed(1)},${y(s[cfg.idx]).toFixed(1)}`).join(" ")}"/>`;
  }).join("");
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", `0 0 ${C.w} ${C.h}`);
  svg.setAttribute("class", "echart");
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", `Leisure battery ${cfg.name}, last ${hours} hours`);
  svg.innerHTML =
    `<line class="ec-axis" x1="${C.l}" y1="${C.t + plotH}" x2="${C.l + plotW}" y2="${C.t + plotH}"/>` +
    line +
    `<text class="ec-lbl ${cfg.lblCls}" x="${C.l - 4}" y="${C.t + 4}" text-anchor="end">${e[1].toFixed(1)}</text>` +
    `<text class="ec-lbl ${cfg.lblCls}" x="${C.l - 4}" y="${C.t + plotH}" text-anchor="end">${e[0].toFixed(1)}</text>` +
    `<text class="ec-lbl" x="${C.l}" y="${C.h - 4}">−${hours} h</text>` +
    `<text class="ec-lbl" x="${C.l + plotW}" y="${C.h - 4}" text-anchor="end">now</text>`;
  return svg;
}

// Returns the card synchronously (render() is sync); the series fills in when the fetch lands.
function energyChart() {
  const card = document.createElement("div");
  card.className = "card echart-card";
  const head = document.createElement("div");
  head.className = "echart-head";
  head.innerHTML = `<span class="echart-title">Leisure battery — last 24 h</span>`;
  const body = document.createElement("div");
  body.className = "echart-body";
  card.append(head, body);
  /** @param {BattHistory|null|undefined} h */
  const paint = (h) => {
    body.innerHTML = "";
    const empty = document.createElement("div");
    empty.className = "echart-empty";
    // api() resolves the body even on a 500, so an error must be told apart from an empty
    // window -- otherwise a broken endpoint reports itself as "van asleep", blaming the van
    // for a bug on our side.
    if (!h || h.error) {
      empty.textContent = "History unavailable.";
      return body.appendChild(empty);
    }
    // two separate diagrams: voltage and current live on very different scales
    const vSvg = seriesSvg(h, { idx: 1, cls: "ec-v", pad: 0.4, lblCls: "ec-v-lbl", name: "voltage" });
    const aSvg = seriesSvg(h, { idx: 2, cls: "ec-a", pad: 2, lblCls: "ec-a-lbl", name: "current" });
    if (vSvg || aSvg) {
      /** @param {string} title @param {SVGSVGElement|null} svg */
      const sub = (title, svg) => {
        const w = document.createElement("div"); w.className = "echart-sub";
        const t = document.createElement("div"); t.className = "echart-subtitle"; t.textContent = title;
        w.append(t); if (svg) w.append(svg);
        return w;
      };
      body.append(sub("Voltage (V)", vSvg), sub("Current (A)", aSvg));
      return;
    }
    const seen = STATE._meta && STATE._meta.last_seen;
    empty.textContent = seen
      ? `No data in the last 24 h — van asleep since ${clockText(seen)}.`
      : "No data yet — history builds while the van is awake.";
    body.appendChild(empty);
  };
  if (HISTORY && Date.now() - HISTORY_TS < HISTORY_TTL_MS) paint(HISTORY);
  else {
    body.textContent = "Loading…";
    loadHistory().then(paint).catch(() => { body.textContent = "History unavailable."; });
  }
  return card;
}

// --- Guided BLE pairing wizard --------------------------------------------------------------
// Client owns no state machine of its own: `PAIRING` is just the last polled `/api/pairing`
// snapshot ({state, attempts, error, address} — calictl.pairing.STATE_NAMES/ERR_NAMES), and the
// card renders whichever step that names. `pairingOpen`/`pairingReady` are pure UI toggles.
// Poll at 1 s ONLY while the card is open (a wizard flow expects snappy feedback); the ambient
// dashboard poll stays at 2 s. See .superpowers/sdd/2026-08-31-guided-pairing.
let pairingOpen = false;
let pairingReady = false;              // "I'm on that screen" checkbox
/** @type {{state:string, attempts:number, error:string|null, address:string|null}|null} */
let PAIRING = null;
/** @type {ReturnType<typeof setInterval>|null} */
let pairingTimer = null;

async function pairingFetch() {
  try { PAIRING = await api("/api/pairing"); } catch (e) { /* transient -- keep the last snapshot */ }
}

// Terminal snapshot states: no flow is mid-flight, so nothing will change again until the user
// acts (Start/Retry/reset). Polling every 1 s forever after that would just be a background
// fetch loop running even once the user has navigated away.
const PAIRING_TERMINAL_STATES = new Set(["bonded", "error", "idle"]);

function startPairingPoll() {
  if (pairingTimer) return;
  pairingTimer = setInterval(async () => {
    await pairingFetch();
    const ae = document.activeElement;
    if (!(ae && ae.id === "pairing-passkey")) {      // don't clobber the user mid-type
      if (view === "home") render();                 // the card only shows on the dashboard
    }
    if (PAIRING && PAIRING_TERMINAL_STATES.has(PAIRING.state)) stopPairingPoll();
  }, 1000);
}
function stopPairingPoll() {
  if (pairingTimer) { clearInterval(pairingTimer); pairingTimer = null; }
}

/** @type {Record<string, string>} */
const PAIRING_ERROR_MSG = {
  timeout: "Timed out waiting for the camper unit.",
  pairing_failed: "Pairing failed.",
  verify_failed: "Could not verify the bond.",
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
  else toast("Pairing: " + ((res && res.error) || "request failed"), "warn");
  render();
}

// Open the wizard on demand (from the topbar context menu, or auto on true first-run).
async function openPairingWizard() {
  pairingOpen = true;
  if (view !== "home") goto("home");   // the card renders on the dashboard only
  await pairingFetch();
  startPairingPoll();
  render();
}

// The guided-pairing card. UX: invisible in the normal paired+online flow — it renders ONLY
// while open (entry point: the ⋮ menu) or auto-prominently on true first-run (offline + no
// bond), so a working installation never shows pairing chrome.
function pairingCard() {
  if (!pairingOpen) return null;
  const card = document.createElement("div");
  card.className = "card";
  const head = document.createElement("div"); head.className = "row";
  const hlbl = document.createElement("span"); hlbl.className = "lbl"; hlbl.textContent = "Bluetooth setup";
  head.appendChild(hlbl);
  const closeBtn = document.createElement("button"); closeBtn.type = "button"; closeBtn.className = "btn";
  closeBtn.textContent = "Close";
  closeBtn.onclick = () => { pairingOpen = false; stopPairingPoll(); render(); };
  head.appendChild(closeBtn);
  card.appendChild(head);

  const p = PAIRING || { state: "idle", attempts: 0, error: null, address: null };
  if (p.state === "idle") {
    const instr = document.createElement("div"); instr.className = "note";
    instr.textContent = "On the camper panel open Bluetooth → ‘Gerät verbinden’.";
    card.appendChild(instr);
    const crow = document.createElement("label"); crow.className = "row";
    const cb = document.createElement("input"); cb.type = "checkbox"; cb.id = "pairing-ready";
    cb.checked = pairingReady;
    cb.onchange = () => { pairingReady = cb.checked; render(); };
    const clbl = document.createElement("span"); clbl.textContent = "I'm on that screen";
    crow.append(cb, clbl);
    card.appendChild(crow);
    const btns = document.createElement("div"); btns.className = "btnrow";
    const startBtn = document.createElement("button"); startBtn.type = "button"; startBtn.className = "btn";
    startBtn.textContent = "Start"; startBtn.disabled = !pairingReady;
    startBtn.onclick = () => pairingAction("start");
    btns.appendChild(startBtn);
    card.appendChild(btns);
  } else if (p.state === "scanning" || p.state === "connecting") {
    const row = document.createElement("div"); row.className = "row";
    row.appendChild(spinner());
    const lbl = document.createElement("span"); lbl.className = "lbl";
    lbl.textContent = p.state === "scanning" ? "Searching for the camper unit…" : "Connecting…";
    row.appendChild(lbl);
    card.appendChild(row);
    const btns = document.createElement("div"); btns.className = "btnrow";
    const cancelBtn = document.createElement("button"); cancelBtn.type = "button"; cancelBtn.className = "btn";
    cancelBtn.textContent = "Cancel";
    cancelBtn.onclick = () => pairingAction("cancel");
    btns.appendChild(cancelBtn);
    card.appendChild(btns);
  } else if (p.state === "waiting_passkey") {
    const instr = document.createElement("div"); instr.className = "note";
    instr.textContent = "Read it from the camper's screen — a fresh code each attempt.";
    card.appendChild(instr);
    const row = document.createElement("div"); row.className = "row";
    const inp = document.createElement("input");
    inp.type = "text"; inp.inputMode = "numeric"; inp.pattern = "[0-9]{6}"; inp.maxLength = 6;
    inp.id = "pairing-passkey";
    inp.placeholder = "123456";
    row.appendChild(inp);
    const sendBtn = document.createElement("button"); sendBtn.type = "button"; sendBtn.className = "btn";
    sendBtn.textContent = "Send";
    sendBtn.onclick = () => {
      const v = inp.value.trim();
      if (!/^[0-9]{6}$/.test(v)) { toast("Enter exactly 6 digits", "warn"); return; }
      pairingAction("passkey", v);
    };
    row.appendChild(sendBtn);
    card.appendChild(row);
  } else if (p.state === "pairing" || p.state === "verifying" || p.state === "resetting") {
    const row = document.createElement("div"); row.className = "row";
    row.appendChild(spinner());
    const lbl = document.createElement("span"); lbl.className = "lbl";
    lbl.textContent = p.state === "pairing" ? "Pairing…" : p.state === "verifying" ? "Verifying…" : "Resetting…";
    row.appendChild(lbl);
    card.appendChild(row);
  } else if (p.state === "bonded") {
    const ok = document.createElement("div"); ok.className = "note";
    ok.textContent = p.address ? ("✓ Paired — " + p.address) : "bonded — address cache unavailable (see logs)";
    card.appendChild(ok);
    if (p.address) {
      const envRow = document.createElement("div"); envRow.className = "row";
      const code = document.createElement("code"); code.textContent = "CALICTL_ADDR=" + p.address;
      envRow.appendChild(code);
      card.appendChild(envRow);
      const note = document.createElement("div"); note.className = "note";
      note.textContent = "The daemon is already using this address live — add that line to /etc/buspi/calictl.env only to make it permanent across reinstalls (pairing.json already survives a plain restart).";
      card.appendChild(note);
    }
  } else if (p.state === "error") {
    const errRow = document.createElement("div"); errRow.className = "warn";
    errRow.textContent = "Error: " + (PAIRING_ERROR_MSG[/** @type {string} */ (p.error)] || p.error || "unknown");
    card.appendChild(errRow);
    const btns = document.createElement("div"); btns.className = "btnrow";
    const retryBtn = document.createElement("button"); retryBtn.type = "button"; retryBtn.className = "btn";
    retryBtn.textContent = "Retry";
    retryBtn.onclick = () => pairingAction("start");
    btns.appendChild(retryBtn);
    card.appendChild(btns);
  }
  // reset/re-pair: available whenever a bond exists to lose, on top of whatever step is showing
  // above (e.g. still visible next to an "error" from a re-pair attempt on an already-bonded unit).
  if (p.state !== "resetting" && (p.state === "bonded" || p.address)) {
    const rbtns = document.createElement("div"); rbtns.className = "btnrow";
    const resetBtn = document.createElement("button"); resetBtn.type = "button"; resetBtn.className = "btn";
    resetBtn.textContent = "Bluetooth reset / re-pair";
    resetBtn.onclick = () => {
      if (!confirm("This removes the working bond; telemetry stops until re-paired. Continue?")) return;
      pairingAction("reset", undefined, true);
    };
    rbtns.appendChild(resetBtn);
    card.appendChild(rbtns);
  }
  return card;
}

function render() {
  backEl.hidden = view === "home";
  backEl.onclick = () => goto("home");
  stopClockTicker();          // any live vehicle-clock ticker belongs to the old DOM; restarted on re-render
  app.innerHTML = "";
  const sp = sessionToggle();
  if (sp) app.appendChild(sp);
  const ob = offlineBanner();
  if (ob) app.appendChild(ob);
  if (readOnly()) {
    const b = document.createElement("div");
    b.className = "readonly";
    b.textContent = "🔒 Read-only — control is disabled on this daemon.";
    app.appendChild(b);
  }
  const fw = STATE._meta && STATE._meta.firmware;
  if (fw && fw.untested) {
    const b = document.createElement("div");
    b.className = "fwwarn";
    b.textContent = `⚠ Untested firmware — unit reports amb ${fw.amb_sw_version ?? "?"} · comm ${fw.comm_version ?? "?"} `
      + `(this project was validated on ${fw.tested}). Decode/semantics may have drifted; treat readings with care.`;
    app.appendChild(b);
  }
  const anc = (STATE._meta && STATE._meta.anchors) || [];
  if (anc.length) {
    const b = document.createElement("div");
    b.className = "fwwarn";
    b.textContent = "⚠ Implausible reading(s): " + anc.join("; ") + " — possible decode drift.";
    app.appendChild(b);
  }
  if (view === "home") renderDashboard();
  else renderFeature(view);
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
    if (f && f.liters != null) rows.push(sumRow("Fresh water", `${f.liters} / ${f.capacity_l} l${f.stale ? " 🕒" : ""}`));
    if (g && g.liters != null) rows.push(sumRow("Grey water", `${g.liters} / ${g.capacity_l} l${g.stale ? " 🕒" : ""}`));
  }
  /** @type {FnState} */
  const e = STATE.energy || {};
  if (e.soc2_pct != null) {
    const h = e.batt2_remaining_h;
    rows.push(sumRow("Second battery", `${e.soc2_pct}%` + (h ? ` · ${h} h` : "")));
  }
  if (!rows.length) return null;
  const card = document.createElement("div"); card.className = "card summary";
  rows.forEach((r) => card.appendChild(r));
  return card;
}

function renderDashboard() {
  titleEl.textContent = "Vehicle";
  // Prominent = the daemon can't reach the van AND no bond exists yet -- the exact moment a
  // fresh/re-paired install needs the wizard most (main() auto-opens it then). Any other time
  // the card exists only while opened from the ⋮ menu, at the end of the dashboard.
  const prominent = !!(STATE._meta && STATE._meta.online === false && PAIRING && PAIRING.address == null);
  const pc = pairingCard();   // null unless open
  if (prominent && pc) app.appendChild(pc);
  const summary = renderSummary();
  if (summary) app.appendChild(summary);
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
      `<div class="ti">${f.icon || ""}</div><div class="tt">${f.title}</div>` +
      `<div class="tv">${f.summary ? f.summary(s) || "" : ""}</div>`;
    if (alertMsg) tile.title = alertMsg;
    tile.onclick = () => goto(fn);
    grid.appendChild(tile);
  }
  app.appendChild(grid);
  if (!prominent && pc) app.appendChild(pc);
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
    { label: "Front", what: "reading-3", zone: 4 } ] },
  { group: "Kitchen", lamps: [
    { label: "Ambient", what: "kitchen-ambient", zone: 5 },
    { label: "Cabinet", what: "kitchen-cabinet", zone: 6 },
    { label: "Cooking", what: "kitchen", zone: 7 } ] },
  { group: "Pop-roof", lamps: [
    { label: "Ambient", what: "roof-ambient", zone: 8 },
    { label: "Reading", what: "roof-reading", zone: 9, hint: "roof open only" } ] },
  { group: "Ambient / outside", lamps: [
    { label: "Rear surround", what: "outside-rear", zone: 3 },
    { label: "Entrance", what: "entrance", zone: 12 } ] },
];
const LIGHT_MAX = 10;   // dg/i enum: 0=off, 1-10 = 10%..100% (11=default; 13=NOT_EQUIPPED — never send)

/** @param {FnState} s */
function renderLighting(s) {
  titleEl.textContent = "Lighting";
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
  const mlbl = document.createElement("span"); mlbl.className = "lbl"; mlbl.textContent = "All lights";
  mrow.appendChild(mlbl);
  if (pending_is("lighting", "power")) mrow.appendChild(spinner());
  const msw = document.createElement("button"); msw.className = "switch";
  msw.setAttribute("role", "switch"); msw.setAttribute("aria-label", "All lights");
  msw.setAttribute("aria-checked", allOn ? "true" : "false"); msw.disabled = readOnly();
  msw.onclick = () => command("lighting", "power", allOn ? "off" : "on");
  mrow.appendChild(msw); mc.appendChild(mrow);

  // Profile activator (the app's profileSelector -> SET_PROFILE / ProfileNumber). Favorites are
  // user-saved scenes on the unit (content unknown to us; we can only activate by number) + the
  // wake-up light. LIGHTS_ON/OFF (12/0) are the master toggle above, so they're not listed here.
  const prow = document.createElement("div"); prow.className = "row";
  const plbl = document.createElement("span"); plbl.className = "lbl"; plbl.textContent = "Activate profile";
  prow.appendChild(plbl);
  if (pending_is("lighting", "profile")) prow.appendChild(spinner());
  const psel = document.createElement("select");
  psel.disabled = readOnly();
  const opt0 = document.createElement("option");
  opt0.value = ""; opt0.textContent = "Choose…"; opt0.selected = true; psel.appendChild(opt0);
  /** @type {[number, string][]} */
  const PROFILES = [[1, "Favorite 1"], [2, "Favorite 2"], [3, "Favorite 3"], [4, "Favorite 4"],
                    [5, "Favorite 5"], [6, "Favorite 6"], [7, "Favorite 7"],
                    [11, "Interior light"], [10, "Wake-up light"]];
  for (const [n, lab] of PROFILES) {
    const o = document.createElement("option"); o.value = /** @type {any} */ (n); o.textContent = lab; psel.appendChild(o);
  }
  psel.onchange = () => {
    if (psel.value === "") return;
    command("lighting", "profile", Number(psel.value));
    psel.value = "";   // it's a momentary action, not a persistent selection
  };
  prow.appendChild(psel); mc.appendChild(prow);

  // Save the CURRENT lamp levels into a favorite slot (the app's l3 applyProfileBrightness).
  const srow = document.createElement("div"); srow.className = "row";
  const slbl = document.createElement("span"); slbl.className = "lbl"; slbl.textContent = "Save current as";
  srow.appendChild(slbl);
  if (pending_is("lighting", "save_profile")) srow.appendChild(spinner());
  const ssel = document.createElement("select");
  ssel.disabled = readOnly();
  const s0 = document.createElement("option"); s0.value = ""; s0.textContent = "Favorite…"; s0.selected = true; ssel.appendChild(s0);
  for (let n = 1; n <= 7; n++) {
    const o = document.createElement("option"); o.value = /** @type {any} */ (n); o.textContent = "Favorite " + n; ssel.appendChild(o);
  }
  ssel.onchange = () => {
    if (ssel.value === "") return;
    const n = Number(ssel.value);
    ssel.value = "";
    if (!confirm(`Overwrite Favorite ${n} with the current lamp levels? This writes to the unit and is not yet verified on the van. Continue?`)) return;
    command("lighting", "save_profile", n);
  };
  srow.appendChild(ssel); mc.appendChild(srow);
  app.appendChild(mc);

  // lamp sliders, grouped like the app (always controllable)
  for (const grp of LIGHT_LAMPS) {
    const card = document.createElement("div"); card.className = "card";
    const h = document.createElement("div"); h.className = "note"; h.style.padding = ".6rem 0 0";
    h.textContent = grp.group; card.appendChild(h);
    for (const lamp of grp.lamps) {
      const row = document.createElement("div"); row.className = "row";
      const lbl = document.createElement("span"); lbl.className = "lbl"; lbl.textContent = lamp.label;
      // roof reading light (L9) is physically unpowered while the pop-top is down — reflect the
      // server-side precondition (control.command_precondition) so the slider isn't a silent no-op.
      const roofPos = (STATE.roof || {}).position_name;
      const roofBlocked = lamp.what === "roof-reading"
        && !(roofPos === "open" || roofPos === "middle" || roofPos == null);
      const hintText = roofBlocked ? "roof must be open" : lamp.hint;
      if (hintText) {
        const hh = document.createElement("span"); hh.className = "lamp-hint"; hh.textContent = hintText;
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
}

/** @param {string} fn */
function renderFeature(fn) {
  const f = FEATURES[fn];
  /** @type {FnState} */
  const s = STATE[fn] || {};
  titleEl.textContent = f.title;
  if (f.warn) {                         // dynamic FAULT banner (e.g. fridge door open) — red/alert
    /** @type {string|null} */
    let w;
    try { w = f.warn(s); } catch (e) { w = null; }
    if (w) {
      const b = document.createElement("div");
      b.className = "warn"; b.textContent = w;
      app.appendChild(b);
    }
  }
  if (f.note) {                         // soft INFO banner (e.g. water stale) — muted, not a fault
    /** @type {string|null} */
    let n;
    try { n = f.note(s); } catch (e) { n = null; }
    if (n) {
      const b = document.createElement("div");
      b.className = "note"; b.textContent = n;
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
  if (f.roof) app.appendChild(roofControls());
  if (fn === "campingmode") app.appendChild(autoCamperCard());
  if (f.readouts && f.readouts.length) {
    const card = document.createElement("div");
    card.className = "card";
    for (const r of f.readouts) card.appendChild(renderReadout(r, s));
    app.appendChild(card);
  }
  if (f.chart) app.appendChild(energyChart());
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
  label.textContent = c.label;
  row.appendChild(label);
  const isPending = pending_is(fn, c.what);
  if (isPending) row.appendChild(spinner());
  const ro = readOnly() || (c.disabled ? !!c.disabled(s) : false);
  // fire() applies a per-control "not verified" confirm (c.confirm) on top of the feature-level
  // one in act(); c.confirm(value) returns the prompt, or null/false to skip.
  /** @param {string|number} val */
  const fire = (val) => {
    const msg = typeof c.confirm === "function" ? c.confirm(val) : c.confirm;
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
      b.textContent = opt.label;
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
      btn.textContent = b.label;
      btn.disabled = ro;
      btn.onclick = () => {
        if (c.confirm && !confirm(/** @type {string} */ (typeof c.confirm === "function" ? c.confirm(b) : c.confirm))) return;
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
  label.textContent = r.label;
  const v = document.createElement("span");
  v.className = "val";
  /** @type {string|number|null|undefined} */
  let val;
  try {
    val = r.get(s);
  } catch (e) {
    val = "—";
  }
  v.textContent = val == null || val === "" ? "—" : String(val);
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
  const ac = (STATE._meta && STATE._meta.auto_camper) || {};
  const card = document.createElement("div"); card.className = "card";
  const row = document.createElement("div"); row.className = "row";
  const lbl = document.createElement("span"); lbl.className = "lbl";
  lbl.textContent = "Restore camping after you park" + (ac.armed ? "  ⟳ will restore on park" : "");
  row.appendChild(lbl);
  const sw = document.createElement("button");
  sw.className = "switch";
  sw.setAttribute("role", "switch"); sw.setAttribute("aria-label", "Auto camper mode");
  sw.setAttribute("aria-checked", ac.enabled ? "true" : "false");
  sw.disabled = readOnly();
  sw.onclick = async () => {
    sw.disabled = true;
    try {
      await api("/api/auto_camper", { method: "POST", headers: { "Content-Type": "application/json" },
                                      body: JSON.stringify({ enabled: !ac.enabled }) });
      toast(ac.enabled ? "Auto camper off" : "Auto camper on", "ok");
    } catch (e) { toast("Couldn't change auto camper", "error"); }
    await refreshState(true);
  };
  row.appendChild(sw); card.appendChild(row);
  const sub = document.createElement("div");
  sub.style.cssText = "padding: 0 1rem .7rem; color: var(--muted); font-size: .8rem; line-height: 1.35;";
  sub.textContent = "The unit drops camper mode when the engine starts and won't allow it back on "
    + "while driving. This turns camper mode + rear USB back on once you park (ignition off), if the "
    + "engine had shed it. Respects a manual off, and stands down on low battery so it never fights "
    + "the unit's power saving.";
  card.appendChild(sub);
  return card;
}

// Re-press debounce (app-faithful, cross-check #2 2026-08-30). A move-start restarts the app's
// SafetyCounter from a fresh random seed; if a release is followed by an immediate re-press the unit
// sees a brand-new counter and withholds the motor another ~3 s. The app ignores a roof press within
// ~1 s of the last one, so button jitter can't stutter the drive. Module-level (not per-render): the
// state view re-renders every 2 s and would otherwise reset a closure-scoped timestamp. STOP is never
// debounced — a release must always cease the move.
const ROOF_REPRESS_MS = 1000;
let roofLastMoveStart = 0;

function roofControls() {
  const card = document.createElement("div");
  card.className = "card";
  const warn = document.createElement("div");
  warn.className = "warn";
  warn.textContent = "Roof control is safety-sensitive and not live-verified.";
  card.appendChild(warn);
  const btns = document.createElement("div");
  btns.className = "btnrow";
  for (const dir of ["open", "close", "stop"]) {
    const b = document.createElement("button");
    b.className = "btn";
    b.textContent = dir;
    b.disabled = readOnly();
    if (pending_is("roof", dir)) b.appendChild(spinner());
    if (dir === "stop") {
      b.onclick = () => command("roof", "stop", null);
    } else {
      // PRESS-AND-HOLD: the pop-top moves only while the button is held; releasing sends STOP
      // (interrupts the in-flight move server-side via _roof_stop). Mirrors the vehicle's own
      // hold-to-move control. Still UNVERIFIED end-to-end on this van.
      let armed = false;
      const start = (ev) => {
        ev.preventDefault();
        if (readOnly() || armed) return;
        if (Date.now() - roofLastMoveStart < ROOF_REPRESS_MS) return;  // debounce a too-quick re-press
        if (!confirm(`Roof ${dir}: hold to move the pop-top (UNVERIFIED on this vehicle). Release to stop. Path clear?`)) return;
        armed = true;
        roofLastMoveStart = Date.now();
        command("roof", dir, null);
      };
      const end = () => { if (armed) { armed = false; command("roof", "stop", null); } };
      b.addEventListener("pointerdown", start);
      b.addEventListener("pointerup", end);
      b.addEventListener("pointerleave", end);
      b.addEventListener("pointercancel", end);
    }
    btns.appendChild(b);
  }
  card.appendChild(btns);
  return card;
}

async function main() {
  await refreshState(true);
  await pairingFetch();      // one-off: needed just to decide the setup card's prominence (see
                              // renderDashboard); the recurring 1 s poll only runs while it's open.
  if (STATE._meta && STATE._meta.online === false && PAIRING && PAIRING.address == null) {
    pairingOpen = true;
    startPairingPoll();
  }
  render();
  setInterval(() => refreshState(false), 2000);
}
main();
