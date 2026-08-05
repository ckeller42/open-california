// OpenCalifornia web UI — curated camper Vehicle-tab controls, driven by /api/state.
//
// Rather than mechanically render the reverse-engineered screen spec, each feature is a
// small hand-authored spec: the actual controls (mapped to the /api/command `what` tokens)
// plus the data readouts pulled from the interpreted /api/state. Labels are our own neutral
// English (no VW app text). Actuation is slow (BLE), so every command shows a spinner on the
// tapped control and a result toast; idle polls don't re-render (no flicker).
const app = document.getElementById("app");
const titleEl = document.getElementById("title");
const backEl = document.getElementById("back");
const statusEl = document.getElementById("status");

let STATE = {};
let view = "home";
// Command queue: BLE writes are serial, so we send one at a time but keep the GUI fluid.
// Tapping a control enqueues it and shows an OPTIMISTIC value immediately; re-changing the
// same control prunes its still-queued command so rapid changes coalesce to the latest value.
let queue = [];          // [{fn, what, value, key}] waiting to send (prunable)
let inflight = null;     // {fn, what, value, key} currently POSTing (NOT prunable)
const optimistic = {};   // key -> intended value, shown until the real state catches up
let lastRender = "";     // signature of the last paint, to skip idle re-renders

const qKey = (fn, what) => fn + "·" + what;
const hasWork = () => !!inflight || queue.length > 0;
// a control has a command queued or in flight -> show a pending badge
function pending_is(fn, what) {
  const k = qKey(fn, what);
  return (inflight && inflight.key === k) || queue.some((c) => c.key === k);
}
// optimistic overlay: the user's intended value wins until the command settles + state refreshes
const optOn = (fn, what, realOn) => {
  const k = qKey(fn, what);
  return k in optimistic ? truthy(optimistic[k]) : realOn;
};
const optNum = (fn, what, realNum) => {
  const k = qKey(fn, what);
  return k in optimistic ? Number(optimistic[k]) : realNum;
};
const truthy = (v) => v === true || v === 1 || v === "on" || v === "1";

const yn = (b) => (b ? "yes" : "no");
const onoff = (b) => (b ? "On" : "Off");
// Format a numeric value with a unit, but never print "null V"/"undefined %": a null/undefined
// reading (e.g. starter battery when engine-off) renders as an em-dash instead.
const withUnit = (v, u) => (v == null ? "—" : `${v} ${u}`);
const tank = (t) => (t && t.percent != null ? `${t.percent}%  (${t.liters}/${t.capacity_l} L)` : "—");
const zonesLit = (s) => Object.keys(s).filter((k) => k.startsWith("brightness_zone_") && s[k]).length;

// Feature specs. `controls[].what` are /api/command tokens (calictl/control.py BUILDERS);
// `state` is the interpreted /api/state key (calictl/semantics.py). `confirm` gates fuel/
// motion actuators behind a dialog. `readouts[].get(state)` formats a live value.
const cap = (s) => (s ? s.charAt(0).toUpperCase() + s.slice(1).replace(/_/g, " ") : s);

// Cooler fault-enum -> banner text (see semantics.cooler / vf/c.java). Absent/"" = no fault.
const COOLER_FAULT_MSG = {
  door_open: "⚠ Fridge door is open",
  emergency: "⚠ Cooler in emergency operation",
  error: "⚠ Cooler error",
};

// Roof InfoPopUp alert -> banner text (see semantics.roof / ig/c.java).
const ROOF_ALERT_MSG = {
  child_lock: "⚠ Roof child lock active",
  error: "⚠ Roof error",
  sensor_error: "⚠ Roof sensor error",
  emergency_locked: "⚠ Roof emergency-locked",
  not_possible: "⚠ Roof operation not possible right now",
  low_battery: "⚠ Battery too low to operate roof",
};

const ORDER = ["cooler", "campingmode", "lighting", "airheater", "water", "energy", "roof", "vehicle"];
const FEATURES = {
  cooler: {
    title: "Cooler", icon: "❄️",
    controls: [
      { what: "power", kind: "toggle", label: "Refrigerator", state: "on" },
      { what: "level", kind: "slider", label: "Cooling level", state: "level", min: 1, max: 5 },
    ],
    readouts: [
      { label: "Fridge door", get: (s) => (s.door_open ? "⚠ Open" : "Closed") },
      { label: "Timer", get: (s) => onoff(s.timer_active) },
    ],
    // `fault` is the cooler's 2-bit Error enum, only meaningful while powered on (vf/c.java):
    // door_open | emergency | error. Surface it as a banner so a fault is obvious at a glance.
    warn: (s) => (COOLER_FAULT_MSG[s.fault] || null),
    summary: (s) => (COOLER_FAULT_MSG[s.fault] || (s.on ? `On · level ${s.level}` : "Off")),
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
    ],
    readouts: [
      { label: "Level", get: (s) => (s.level == null ? "—" : (s.level >= 10 ? "HI" : s.level)) },
      { label: "Running time", get: (s) => withUnit(s.running_time, "min") },
      { label: "Error code", get: (s) => s.error_code ?? "—" },
    ],
    summary: (s) => (s.running ? `Running${s.level != null ? " · " + (s.level >= 10 ? "HI" : s.level) : ""}` : "Off"),
  },
  water: {
    title: "Water", icon: "💧",
    readouts: [
      { label: "Fresh water", get: (s) => tank(s.fresh) + (s.fresh && s.fresh.stale ? "  🕒 stale" : ""),
        bar: (s) => s.fresh && s.fresh.percent },
      { label: "Waste water", get: (s) => tank(s.waste), bar: (s) => s.waste && s.waste.percent },
    ],
    // The fresh-water sensor only measures while the van's water system is on; parked/asleep it
    // latches a stale low value. This is a soft `note` (informational) — NOT a `warn` (fault), so it
    // does not raise the dashboard alert badge; a parked van is normal, not a fault.
    note: (s) => (s.fresh && s.fresh.stale ? "🕒 Fresh-water level is stale — the van's water system is off (last measured while active)" : null),
    summary: (s) => (s.fresh ? `Fresh ${s.fresh.percent}%${s.fresh.stale ? " (stale)" : ""}` : ""),
  },
  energy: {
    title: "Energy", icon: "🔋", chart: true,
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
    ],
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
      { label: "Vehicle clock", get: (s) => s.car_clock ?? "—" },
    ],
    summary: (s) => (s.ignition_on ? "Ignition on" : "Parked"),
  },
};

async function api(path, opts) {
  const r = await fetch(path, opts);
  return r.json();
}

function setStatus(text, kind) {
  statusEl.textContent = text;
  statusEl.className = "pill" + (kind ? " " + kind : "");
}

function installed(fn) {
  const s = STATE[fn];
  return !s || s.installed !== false;
}

// read-only = the daemon rejects control writes (the safe default; enable with --enable-writes /
// CALICTL_ENABLE_WRITES=1). The UI disables every control and shows a banner when true.
const readOnly = () => !!(STATE._meta && STATE._meta.read_only);

function toast(msg, kind) {
  const d = document.createElement("div");
  d.className = "toast" + (kind ? " " + kind : "");
  d.textContent = msg;
  document.getElementById("toasts").appendChild(d);
  setTimeout(() => d.remove(), 4000);
}

async function refreshState(force) {
  let next;
  try {
    next = await api("/api/state");
  } catch (e) {
    setStatus("offline", "error");
    return;
  }
  STATE = next;
  // "live" = the van is currently reachable; "offline" = daemon can't reach it (van asleep),
  // matching the banner. The web UI itself is up either way — this reflects the vehicle link.
  if (!hasWork()) {
    const vanOnline = !STATE._meta || STATE._meta.online;
    setStatus(vanOnline ? "live" : "offline", vanOnline ? "ok" : "warn");
  }
  // don't repaint out from under a slider the user is actively dragging (optimistic values +
  // the queue keep the rest fresh; a forced refresh after a command still repaints).
  if (!force && document.activeElement && document.activeElement.type === "range") return;
  const sig = view + "|" + JSON.stringify(next);
  if (!force && sig === lastRender) return; // nothing changed -> no flicker
  lastRender = sig;
  render();
}

// Enqueue a command. Shows the intended value immediately (optimistic) and coalesces rapid
// changes to the same control: any still-queued command for it is pruned so only the latest
// value is sent (the in-flight one can't be un-sent). Then the queue drains one at a time.
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
  inflight = queue.shift();
  setStatus("Sending…", "busy");
  render();
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
  if (res && res.ok && res.applied === true) toast("✓ Applied", "ok");
  else if (res && res.ok) toast("Sent — the unit didn't apply it (known gap)", "warn");
  else toast("Command failed" + (res && res.error ? `: ${res.error}` : ""), "error");
  // drop the optimistic value only if no newer change for this control is still queued
  if (!queue.some((c) => c.key === done.key)) delete optimistic[done.key];
  await refreshState(true);
  processQueue();
}

// Actuate a control, gating fuel/motion features behind a confirm() dialog.
function act(fn, what, value) {
  const f = FEATURES[fn];
  if (f && f.confirm && !confirm(f.confirm(what))) return;
  command(fn, what, value);
}

function goto(v) {
  view = v;
  lastRender = "";
  render();
}

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

// Read-only session-state pill (persistent BLE session). Displayed, never toggled — same
// precedent as the read-only lock: the unauthenticated LAN UI must not flip daemon infra state.
const SESSION_PILL = {
  up: { cls: "ok", text: "🟢 Live · fast" },
  connecting: { cls: "busy", text: "🟡 Connecting" },
  degraded: { cls: "busy", text: "🟡 Reconnecting" },
  asleep: { cls: "", text: "⚪ Asleep" },
};
function sessionPill() {
  const st = STATE._meta && STATE._meta.session;
  if (!st || st === "off") return null;
  const spec = SESSION_PILL[st] || SESSION_PILL.asleep;
  const el = document.createElement("div");
  el.className = "session-pill" + (spec.cls ? " " + spec.cls : "");
  el.textContent = spec.text;
  return el;
}

const fmtDeg = (d) => (d > 0 ? "+" : "") + d + "°";

// A spirit-level "bubble" for the 2-axis van leveling: a target with a bubble offset by roll (x)
// and pitch (y). Returns null when there's no reading (ignition off) so the row just shows "—".
function bubbleLevel(roll, pitch) {
  if (roll == null || pitch == null) return null;
  const MAX = 6, R = 70, ring = 64, br = 13, reach = ring - br;   // ±6° maps to the outer ring
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

function chartSvg(h) {
  const samples = (h && h.samples) || [];
  const vE = extent(samples, 1, 0.4), aE = extent(samples, 2, 2);
  if (!samples.length || !vE || !aE) return null;
  const C = CHART, hours = h.hours || 24;
  const t1 = h.now, t0 = t1 - hours * 3600;          // a TRUE 24 h axis: gaps stay gaps
  const plotW = C.w - C.l - C.r, plotH = C.h - C.t - C.b;
  const x = (ts) => C.l + ((ts - t0) / (t1 - t0)) * plotW;
  const yFor = (e) => (val) => C.t + plotH - ((val - e[0]) / (e[1] - e[0])) * plotH;
  const yV = yFor(vE), yA = yFor(aE);
  const draw = (idx, y, cls) => contiguousRuns(samples, h.gap_s || 120).map((run) => {
    const pts = run.filter((s) => typeof s[idx] === "number" && isFinite(s[idx]));
    if (!pts.length) return "";
    if (pts.length === 1)                            // a lone sample has no line -- show the point
      return `<circle class="${cls}-dot" cx="${x(pts[0][0]).toFixed(1)}" cy="${y(pts[0][idx]).toFixed(1)}" r="1.6"/>`;
    return `<polyline class="${cls}" points="${
      pts.map((s) => `${x(s[0]).toFixed(1)},${y(s[idx]).toFixed(1)}`).join(" ")}"/>`;
  }).join("");
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", `0 0 ${C.w} ${C.h}`);
  svg.setAttribute("class", "echart");
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", `Leisure battery, last ${hours} hours`);
  svg.innerHTML =
    `<line class="ec-axis" x1="${C.l}" y1="${C.t + plotH}" x2="${C.l + plotW}" y2="${C.t + plotH}"/>` +
    draw(2, yA, "ec-a") + draw(1, yV, "ec-v") +
    `<text class="ec-lbl ec-v-lbl" x="${C.l - 4}" y="${C.t + 4}" text-anchor="end">${vE[1].toFixed(1)}</text>` +
    `<text class="ec-lbl ec-v-lbl" x="${C.l - 4}" y="${C.t + plotH}" text-anchor="end">${vE[0].toFixed(1)}</text>` +
    `<text class="ec-lbl ec-a-lbl" x="${C.l + plotW + 4}" y="${C.t + 4}">${aE[1].toFixed(1)}</text>` +
    `<text class="ec-lbl ec-a-lbl" x="${C.l + plotW + 4}" y="${C.t + plotH}">${aE[0].toFixed(1)}</text>` +
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
  head.innerHTML = `<span class="echart-title">Leisure battery — last 24 h</span>` +
    `<span class="echart-key"><i class="ec-key-v"></i>V <i class="ec-key-a"></i>A</span>`;
  const body = document.createElement("div");
  body.className = "echart-body";
  card.append(head, body);
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
    const svg = chartSvg(h);
    if (svg) return body.appendChild(svg);
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

function render() {
  backEl.hidden = view === "home";
  backEl.onclick = () => goto("home");
  app.innerHTML = "";
  const sp = sessionPill();
  if (sp) app.appendChild(sp);
  const ob = offlineBanner();
  if (ob) app.appendChild(ob);
  if (readOnly()) {
    const b = document.createElement("div");
    b.className = "readonly";
    b.textContent = "🔒 Read-only — control is disabled on this daemon.";
    app.appendChild(b);
  }
  if (view === "home") renderDashboard();
  else renderFeature(view);
}

// The app's "California Status" overview card: fresh/grey water + leisure battery, at a glance.
// A row is emitted only when its data is present (installed + non-null), so a truncated poll or
// an uninstalled tank never renders a "— / — l" ghost row.
function sumRow(label, value) {
  const row = document.createElement("div"); row.className = "row";
  const l = document.createElement("span"); l.className = "lbl"; l.textContent = label;
  const v = document.createElement("span"); v.className = "val"; v.textContent = value;
  row.append(l, v);
  return row;
}

function renderSummary() {
  const rows = [];
  const w = STATE.water || {};
  if (installed("water")) {
    const f = w.fresh, g = w.waste;
    if (f && f.liters != null) rows.push(sumRow("Fresh water", `${f.liters} / ${f.capacity_l} l`));
    if (g && g.liters != null) rows.push(sumRow("Grey water", `${g.liters} / ${g.capacity_l} l`));
  }
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
  const summary = renderSummary();
  if (summary) app.appendChild(summary);
  const grid = document.createElement("div");
  grid.className = "tilegrid";
  for (const fn of ORDER) {
    if (!installed(fn)) continue;
    const f = FEATURES[fn];
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
}

// Lighting lamps, grouped like the app (from the HCI capture + screenshots). `what` is the
// control API zone key (control.LIGHT_ZONES); `zone` is the interpreted brightness_zone_<N>.
// Matches the app's 8-lamp layout (screenshots 2026-07-15). `hint` marks zones whose lamp
// identity is INFERRED by elimination, not confirmed by capture (L5/L6): dragging one on-device
// is the experiment that confirms it. See control.LIGHT_ZONES.
const LIGHT_LAMPS = [
  { group: "Reading lights", lamps: [
    { label: "Left", what: "reading-1", zone: 2 },
    { label: "Right", what: "reading-2", zone: 1 },
    { label: "Passenger", what: "reading-3", zone: 4 } ] },
  { group: "Kitchen", lamps: [
    { label: "Ambient", what: "kitchen-ambient", zone: 5, hint: "unverified" },
    { label: "Cooking", what: "kitchen", zone: 7 } ] },
  { group: "Pop-roof", lamps: [
    { label: "Ambient", what: "roof-ambient", zone: 8 },
    { label: "Reading", what: "roof-reading", zone: 6, hint: "roof open only · unverified" } ] },
  { group: "Outside", lamps: [{ label: "Rear", what: "outside-rear", zone: 3 }] },
];
const LIGHT_MAX = 13;   // brightness 0..13 (14 = the leave-unchanged sentinel)

function renderLighting(s) {
  titleEl.textContent = "Lighting";
  // Honesty banner: physical actuation is UNVERIFIED (owner-confirmed lamps stay dark). The state
  // char is a write-through ECHO, so a readback ALWAYS "confirms" a change even when no lamp lit —
  // the sliders below may not drive the real load. Persistent (not just the post-write toast) so the
  // caveat is visible BEFORE the user drags anything. See docs/business-logic/control-and-actuation.md.
  const cav = document.createElement("div"); cav.className = "caveat";
  cav.textContent = "⚠ Lighting control is unverified — the unit echoes commands back, so a slider "
    + "may look applied while the lamp stays dark. Treat these as best-effort, not confirmed.";
  app.appendChild(cav);
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
  msw.setAttribute("aria-checked", allOn ? "true" : "false"); msw.disabled = readOnly();
  msw.onclick = () => command("lighting", "power", allOn ? "off" : "on");
  mrow.appendChild(msw); mc.appendChild(mrow); app.appendChild(mc);

  // lamp sliders, grouped like the app (always controllable)
  for (const grp of LIGHT_LAMPS) {
    const card = document.createElement("div"); card.className = "card";
    const h = document.createElement("div"); h.className = "note"; h.style.padding = ".6rem 0 0";
    h.textContent = grp.group; card.appendChild(h);
    for (const lamp of grp.lamps) {
      const row = document.createElement("div"); row.className = "row";
      const lbl = document.createElement("span"); lbl.className = "lbl"; lbl.textContent = lamp.label;
      if (lamp.hint) {
        const hh = document.createElement("span"); hh.className = "lamp-hint"; hh.textContent = lamp.hint;
        lbl.appendChild(hh);
      }
      const real = s["brightness_zone_" + lamp.zone];
      const val = optNum("lighting", lamp.what, real != null ? real : 0);
      const isPending = pending_is("lighting", lamp.what);
      row.appendChild(lbl);
      if (isPending) row.appendChild(spinner());
      const inp = document.createElement("input"); inp.type = "range"; inp.min = 0; inp.max = LIGHT_MAX;
      inp.value = val; inp.disabled = readOnly();
      const out = document.createElement("span"); out.className = "sval";
      out.textContent = val;
      inp.oninput = () => (out.textContent = inp.value);
      inp.onchange = () => command("lighting", lamp.what, Number(inp.value));
      row.appendChild(inp); row.appendChild(out);
      card.appendChild(row);
    }
    app.appendChild(card);
  }
}

function renderFeature(fn) {
  const f = FEATURES[fn];
  const s = STATE[fn] || {};
  titleEl.textContent = f.title;
  if (f.warn) {                         // dynamic FAULT banner (e.g. fridge door open) — red/alert
    let w;
    try { w = f.warn(s); } catch (e) { w = null; }
    if (w) {
      const b = document.createElement("div");
      b.className = "warn"; b.textContent = w;
      app.appendChild(b);
    }
  }
  if (f.note) {                         // soft INFO banner (e.g. water stale) — muted, not a fault
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
  if (f.readouts && f.readouts.length) {
    const card = document.createElement("div");
    card.className = "card";
    for (const r of f.readouts) card.appendChild(renderReadout(r, s));
    app.appendChild(card);
  }
  if (f.chart) app.appendChild(energyChart());
}

function renderControl(fn, c, s) {
  const row = document.createElement("div");
  row.className = "row";
  const label = document.createElement("span");
  label.className = "lbl";
  label.textContent = c.label;
  row.appendChild(label);
  const isPending = pending_is(fn, c.what);
  if (isPending) row.appendChild(spinner());
  if (c.kind === "toggle") {
    const on = optOn(fn, c.what, !!s[c.state]);
    const sw = document.createElement("button");
    sw.className = "switch" + (isPending ? " pending" : "");
    sw.setAttribute("aria-checked", on ? "true" : "false");
    sw.disabled = readOnly();
    sw.onclick = () => act(fn, c.what, on ? "off" : "on");
    row.appendChild(sw);
  } else if (c.kind === "slider") {
    const val = optNum(fn, c.what, s[c.state] != null ? s[c.state] : c.min);
    const inp = document.createElement("input");
    inp.type = "range";
    inp.min = c.min;
    inp.max = c.max;
    inp.value = val;
    inp.disabled = readOnly();
    const out = document.createElement("span");
    out.className = "sval";
    out.textContent = val;
    inp.oninput = () => (out.textContent = inp.value);
    inp.onchange = () => act(fn, c.what, Number(inp.value));
    row.appendChild(inp);
    row.appendChild(out);
  }
  return row;
}

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
  let val;
  try {
    val = r.get(s);
  } catch (e) {
    val = "—";
  }
  v.textContent = val == null || val === "" ? "—" : String(val);
  row.appendChild(label);
  row.appendChild(v);
  wrap.appendChild(row);
  if (r.bar) {
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
    b.onclick = () => {
      if (confirm(`Roof ${dir}: this physically moves the pop-top (UNVERIFIED on this vehicle). Ensure the path is clear. Continue?`))
        command("roof", dir, null);
    };
    btns.appendChild(b);
  }
  card.appendChild(btns);
  return card;
}

async function main() {
  await refreshState(true);
  setInterval(() => refreshState(false), 2000);
}
main();
