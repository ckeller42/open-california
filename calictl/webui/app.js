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
let busy = false;        // a command is in flight — BLE is slow, one at a time
let pending = null;      // {fn, what} currently actuating -> show a spinner there
let lastRender = "";     // signature of the last paint, to skip idle re-renders

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
const ORDER = ["cooler", "campingmode", "lighting", "airheater", "water", "energy", "roof", "vehicle"];
const FEATURES = {
  cooler: {
    title: "Cooler", icon: "❄️",
    controls: [
      { what: "power", kind: "toggle", label: "Refrigerator", state: "on" },
      { what: "level", kind: "slider", label: "Cooling level", state: "level", min: 1, max: 5 },
    ],
    readouts: [{ label: "Timer", get: (s) => onoff(s.timer_active) }, { label: "Error", get: (s) => yn(s.error) }],
    summary: (s) => (s.on ? `On · level ${s.level}` : "Off"),
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
      { what: "level", kind: "slider", label: "Heating level", state: "level", min: 0, max: 15 },
    ],
    readouts: [{ label: "Running time", get: (s) => withUnit(s.running_time, "min") }, { label: "Error code", get: (s) => s.error_code ?? "—" }],
    summary: (s) => (s.running ? "Running" : "Off"),
  },
  water: {
    title: "Water", icon: "💧",
    readouts: [
      { label: "Fresh water", get: (s) => tank(s.fresh), bar: (s) => s.fresh && s.fresh.percent },
      { label: "Estimated water left",
        get: (s) => (s.fresh && s.fresh.days_left != null
          ? `≈ ${s.fresh.days_left} days` + (s.fresh.drain_lpd ? ` · ${s.fresh.drain_lpd} L/day` : "")
          : "—") },
      { label: "Waste water", get: (s) => tank(s.waste), bar: (s) => s.waste && s.waste.percent },
    ],
    summary: (s) => (s.fresh ? `Fresh ${s.fresh.percent}%` : ""),
  },
  energy: {
    title: "Energy", icon: "🔋",
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
    readouts: [{ label: "Position", get: (s) => s.position ?? "—" }, { label: "Safety valid", get: (s) => yn(s.safety_valid) }],
    summary: (s) => (s && s.position != null ? `Position ${s.position}` : ""),
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
  if (!busy) {
    const vanOnline = !STATE._meta || STATE._meta.online;
    setStatus(vanOnline ? "live" : "offline", vanOnline ? "ok" : "warn");
  }
  if (busy && !force) return; // an in-flight interaction owns the screen
  const sig = view + "|" + JSON.stringify(next);
  if (!force && sig === lastRender) return; // nothing changed -> no flicker
  lastRender = sig;
  render();
}

// Send a command: optimistic spinner on the tapped control, then a result toast. Always
// sends confirm:true (the frontend already showed any confirm dialog; the backend only
// requires it for roof/airheater, ignores it otherwise).
async function command(fn, what, value) {
  if (busy) return;
  busy = true;
  pending = { fn, what };
  setStatus("Sending…", "busy");
  render();
  let res;
  try {
    res = await api("/api/command", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ function: fn, what, value, confirm: true }),
    });
  } catch (e) {
    res = { ok: false, error: String(e) };
  }
  busy = false;
  pending = null;
  if (res && res.ok && res.applied === true) toast("✓ Applied", "ok");
  else if (res && res.ok) toast("Sent — the unit didn't apply it (known gap)", "warn");
  else toast("Command failed" + (res && res.error ? `: ${res.error}` : ""), "error");
  await refreshState(true);
}

// Actuate a control, gating fuel/motion features behind a confirm() dialog.
function act(fn, what, value) {
  if (busy) return;
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

function render() {
  backEl.hidden = view === "home";
  backEl.onclick = () => goto("home");
  app.innerHTML = "";
  const ob = offlineBanner();
  if (ob) app.appendChild(ob);
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
    tile.className = "tile";
    tile.innerHTML = `<div class="ti">${f.icon || ""}</div><div class="tt">${f.title}</div>` +
      `<div class="tv">${f.summary ? f.summary(s) || "" : ""}</div>`;
    tile.onclick = () => goto(fn);
    grid.appendChild(tile);
  }
  app.appendChild(grid);
}

// Lighting lamps, grouped like the app (from the HCI capture + screenshots). `what` is the
// control API zone key (control.LIGHT_ZONES); `zone` is the interpreted brightness_zone_<N>.
const LIGHT_LAMPS = [
  { group: "Reading lights", lamps: [
    { label: "Left", what: "reading-1", zone: 2 },
    { label: "Right", what: "reading-2", zone: 1 },
    { label: "Passenger", what: "reading-3", zone: 4 } ] },
  { group: "Kitchen", lamps: [{ label: "Kitchen", what: "kitchen", zone: 7 }] },
  { group: "Pop-roof", lamps: [{ label: "Ambient", what: "roof-ambient", zone: 8 }] },
  { group: "Outside", lamps: [{ label: "Rear", what: "outside-rear", zone: 3 }] },
];
const LIGHT_MAX = 13;   // brightness 0..13 (14 = the leave-unchanged sentinel)

function renderLighting(s) {
  titleEl.textContent = "Lighting";
  // "All lights" master toggle (the app's "Alle Lichter") — on = any real lamp lit;
  // tapping sends power on/off (every zone). Needs an active profile to apply, like the zones.
  // A profile must be active before ANY lamp control applies (verified on-device) — so the
  // all-lights toggle + zone sliders are disabled + dimmed until one is; only Activate is live.
  const active = !!(s.profile && s.profile !== 0);
  const allOn = !!s.any_on;
  const mc = document.createElement("div"); mc.className = "card" + (active ? "" : " dim");
  const mrow = document.createElement("div"); mrow.className = "row";
  const mlbl = document.createElement("span"); mlbl.className = "lbl"; mlbl.textContent = "All lights";
  mrow.appendChild(mlbl);
  if (busy && pending_is("lighting", "power")) mrow.appendChild(spinner());
  const msw = document.createElement("button"); msw.className = "switch";
  msw.setAttribute("aria-checked", allOn ? "true" : "false"); msw.disabled = busy || !active;
  msw.onclick = () => command("lighting", "power", allOn ? "off" : "on");
  mrow.appendChild(msw); mc.appendChild(mrow); app.appendChild(mc);

  // profile card — a profile must be active for zone changes to apply (verified on-device)
  const pc = document.createElement("div"); pc.className = "card";
  const prow = document.createElement("div"); prow.className = "row";
  const plabel = document.createElement("span"); plabel.className = "lbl";
  plabel.textContent = active ? "Profile " + s.profile + " active" : "No profile active";
  prow.appendChild(plabel);
  const pbtn = document.createElement("button"); pbtn.className = "btn"; pbtn.style.flex = "0 0 auto";
  pbtn.textContent = active ? "Reactivate" : "Activate";
  pbtn.disabled = busy;
  pbtn.onclick = () => command("lighting", "profile", 9);   // profile 9 = A (verified)
  prow.appendChild(pbtn);
  pc.appendChild(prow);
  app.appendChild(pc);
  if (!active) {
    const n = document.createElement("div"); n.className = "note";
    n.textContent = "Activate a lighting profile to control the lamps.";
    app.appendChild(n);
  }
  // lamp sliders, grouped (disabled + dimmed until a profile is active)
  for (const grp of LIGHT_LAMPS) {
    const card = document.createElement("div"); card.className = "card" + (active ? "" : " dim");
    const h = document.createElement("div"); h.className = "note"; h.style.padding = ".6rem 0 0";
    h.textContent = grp.group; card.appendChild(h);
    for (const lamp of grp.lamps) {
      const row = document.createElement("div"); row.className = "row";
      const lbl = document.createElement("span"); lbl.className = "lbl"; lbl.textContent = lamp.label;
      const val = s["brightness_zone_" + lamp.zone];
      const isPending = busy && pending_is("lighting", lamp.what);
      row.appendChild(lbl);
      if (isPending) row.appendChild(spinner());
      const inp = document.createElement("input"); inp.type = "range"; inp.min = 0; inp.max = LIGHT_MAX;
      inp.value = val != null ? val : 0; inp.disabled = busy || !active;
      const out = document.createElement("span"); out.className = "sval";
      out.textContent = isPending ? "…" : (val != null ? val : 0);
      inp.oninput = () => (out.textContent = inp.value);
      inp.onchange = () => command("lighting", lamp.what, Number(inp.value));
      row.appendChild(inp); row.appendChild(out);
      card.appendChild(row);
    }
    app.appendChild(card);
  }
}

function pending_is(fn, what) {
  return pending && pending.fn === fn && pending.what === what;
}

function renderFeature(fn) {
  const f = FEATURES[fn];
  const s = STATE[fn] || {};
  titleEl.textContent = f.title;
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
  if (f.note) {
    const n = document.createElement("div");
    n.className = "note";
    n.textContent = f.note;
    app.appendChild(n);
  }
}

function renderControl(fn, c, s) {
  const row = document.createElement("div");
  row.className = "row";
  const label = document.createElement("span");
  label.className = "lbl";
  label.textContent = c.label;
  row.appendChild(label);
  const isPending = busy && pending && pending.fn === fn && pending.what === c.what;
  if (isPending) row.appendChild(spinner());
  if (c.kind === "toggle") {
    const on = !!s[c.state];
    const sw = document.createElement("button");
    sw.className = "switch" + (isPending ? " pending" : "");
    sw.setAttribute("aria-checked", on ? "true" : "false");
    sw.disabled = busy;
    sw.onclick = () => act(fn, c.what, on ? "off" : "on");
    row.appendChild(sw);
  } else if (c.kind === "slider") {
    const val = s[c.state] != null ? s[c.state] : c.min;
    const inp = document.createElement("input");
    inp.type = "range";
    inp.min = c.min;
    inp.max = c.max;
    inp.value = val;
    inp.disabled = busy;
    const out = document.createElement("span");
    out.className = "sval";
    out.textContent = isPending ? "…" : val;
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
    b.disabled = busy;
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
