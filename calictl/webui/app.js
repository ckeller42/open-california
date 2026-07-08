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
    title: "Lighting", icon: "💡",
    note: "Lighting actuation over BLE isn't supported yet — status only.",
    controls: [],
    readouts: [
      { label: "Any zone on", get: (s) => yn(s.any_on) },
      { label: "Zones lit", get: (s) => zonesLit(s) },
      { label: "Profile", get: (s) => s.profile },
    ],
    summary: (s) => onoff(s.any_on),
  },
  airheater: {
    title: "Air heater", icon: "🔥", confirm: (w) => `Start the fuel-burning parking heater (${w})? It is not live-verified. Continue?`,
    controls: [
      { what: "power", kind: "toggle", label: "Parking heater", state: "running" },
      { what: "level", kind: "slider", label: "Heating level", state: "level", min: 0, max: 15 },
    ],
    readouts: [{ label: "Running time", get: (s) => `${s.running_time} min` }, { label: "Error code", get: (s) => s.error_code }],
    summary: (s) => (s.running ? "Running" : "Off"),
  },
  water: {
    title: "Water", icon: "💧",
    readouts: [
      { label: "Fresh water", get: (s) => tank(s.fresh), bar: (s) => s.fresh && s.fresh.percent },
      { label: "Waste water", get: (s) => tank(s.waste), bar: (s) => s.waste && s.waste.percent },
    ],
    summary: (s) => (s.fresh ? `Fresh ${s.fresh.percent}%` : ""),
  },
  energy: {
    title: "Energy", icon: "🔋",
    readouts: [
      { label: "Living battery", get: (s) => (s.soc2_pct != null ? `${s.soc2_pct}%` : "—"), bar: (s) => s.soc2_pct },
      { label: "Living voltage", get: (s) => `${s.batt2_v} V` },
      { label: "Time remaining", get: (s) => (s.batt2_remaining_h != null ? `${s.batt2_remaining_h} h` : "—") },
      { label: "Starter battery", get: (s) => (s.soc1_pct != null ? `${s.soc1_pct}%` : "—"), bar: (s) => s.soc1_pct },
      { label: "Starter voltage", get: (s) => `${s.batt1_v} V` },
      { label: "DC-DC charger", get: (s) => (s.dcdc_installed ? `${s.dcdc_state} (${s.dcdc_power} W)` : "—") },
      { label: "Shore power", get: (s) => (s.shore_installed ? `${s.shore_state} (${s.shore_power} W)` : "—") },
      { label: "Solar", get: (s) => (s.solar_installed ? s.solar_state : "not installed") },
      { label: "Warnings", get: (s) => (s.faults && s.faults.length ? s.faults.join(", ") : "none") },
    ],
    summary: (s) => (s.soc2_pct != null ? `Battery ${s.soc2_pct}%` : ""),
  },
  roof: {
    title: "Roof", icon: "🚐", roof: true,
    readouts: [{ label: "Position", get: (s) => s.position }, { label: "Safety valid", get: (s) => yn(s.safety_valid) }],
    summary: (s) => (s && s.position != null ? `Position ${s.position}` : ""),
  },
  vehicle: {
    title: "Vehicle", icon: "🚗",
    readouts: [
      { label: "Ignition", get: (s) => onoff(s.ignition_on) },
      { label: "Leveling (roll / pitch)", get: (s) => `${s.level_roll} / ${s.level_pitch}` },
      { label: "Vehicle clock", get: (s) => s.car_clock },
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
  if (!busy) setStatus("live", "ok");
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

function render() {
  backEl.hidden = view === "home";
  backEl.onclick = () => goto("home");
  app.innerHTML = "";
  if (view === "home") renderDashboard();
  else renderFeature(view);
}

function renderDashboard() {
  titleEl.textContent = "Vehicle";
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

function renderFeature(fn) {
  const f = FEATURES[fn];
  const s = STATE[fn] || {};
  titleEl.textContent = f.title;
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
