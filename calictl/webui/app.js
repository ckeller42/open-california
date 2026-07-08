// Renders the camper control web UI from /api/screens + /api/state.
const app = document.getElementById("app");
const titleEl = document.getElementById("title");
const backEl = document.getElementById("back");
let SCREENS = {order: [], screens: {}};
let STATE = {};
let view = "home";
let poll;

// The roof screen carries no single `function` in screens.json (its widgets span
// a multi-action control set), but the backend targets it as function "roof"
// (see calictl/web.py ROOF_FUNCTION). Keep the literal in sync with that.
const ROOF_FUNCTION = "roof";

// Explicit widget -> command map. Only these widgets actuate. Keyed by the
// screen's `function` and the widget's real `id` (verified against
// calictl/webui/screens.json), mapped to the control API's `what` token
// (calictl/control.py BUILDERS) and the interpreted state key it reflects
// (calictl/semantics.py). Everything not listed here renders read-only.
const CONTROL_MAP = {
  cooler: {
    refrigeratorBoxWidget: {kind: "toggle", what: "power", stateKey: "on"},
    coolingLevelSlider: {kind: "slider", what: "level", stateKey: "level"},
  },
  campingmode: {
    powerToggle: {kind: "toggle", what: "master", stateKey: "master_on"},
    usbChargerToggle: {kind: "toggle", what: "usb", stateKey: "usb_charger"},
    allLightsToggle: {kind: "toggle", what: "lights", stateKey: "lights_on"},
  },
  airheater: {
    imediateHeatingWidget: {kind: "toggle", what: "power", stateKey: "running"},
    heatingSlider: {kind: "slider", what: "level", stateKey: "level"},
  },
};

async function api(path, opts) {
  const r = await fetch(path, opts);
  return r.json();
}
async function refreshState() { STATE = await api("/api/state"); render(); }

async function command(function_, what, value, confirm) {
  const body = {function: function_, what, value, confirm: !!confirm};
  const res = await api("/api/command", {method: "POST",
    headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
  // `applied` is true/false/null (no readback / unknown). Both false and null mean
  // "don't assume it worked" — a dead/unmapped command must not fail silently.
  if (res.applied === false || res.applied == null) toast("Sent — but the unit did not apply it (known gap).");
  await refreshState();
  return res;
}

function installed(fn) { return !fn || !STATE[fn] || STATE[fn].installed !== false; }
function toast(msg){ const d=document.createElement("div"); d.className="card"; d.textContent=msg;
  d.style.position="fixed"; d.style.bottom="1rem"; d.style.left="50%"; d.style.transform="translateX(-50%)";
  document.body.appendChild(d); setTimeout(()=>d.remove(), 3500); }

function goto(v){ view=v; render(); }

function render() {
  backEl.hidden = (view === "home");
  backEl.onclick = () => goto("home");
  app.innerHTML = "";
  if (view === "home") return renderDashboard();
  renderScreen(view);
}

function renderDashboard() {
  titleEl.textContent = "Vehicle";
  const grid = document.createElement("div"); grid.className = "tilegrid";
  for (const name of SCREENS.order) {
    const scr = SCREENS.screens[name];
    if (name === "home" || !installed(scr.function)) continue;
    const tile = document.createElement("button"); tile.className = "tile";
    tile.innerHTML = `<h3>${scr.title || name}</h3><div class="v">${summary(scr)}</div>`;
    tile.onclick = () => goto(name);
    grid.appendChild(tile);
  }
  app.appendChild(grid);
}

function summary(scr) {
  const st = STATE[scr.function]; if (!st) return "";
  if ("on" in st) return st.on ? "On" : "Off";
  if ("percent" in st && st.percent != null) return st.percent + "%";
  return "";
}

function renderScreen(name) {
  const scr = SCREENS.screens[name]; titleEl.textContent = scr.title || name;
  // Roof has no `function` in screens.json (see ROOF_FUNCTION above); every other
  // screen's function (if any) already names its state-cache key directly.
  const fn = scr.function || (name === "roof" ? ROOF_FUNCTION : null);
  const st = (fn && STATE[fn]) || {};
  for (const w of scr.widgets) app.appendChild(renderWidget(fn, w, st));
  // Per-screen side content: rendered ONCE after the widget list, not per-widget.
  if (name === "roof") app.appendChild(roofControls(ROOF_FUNCTION));
  if (name === "lighting") app.appendChild(lightingNote());
}

function renderWidget(fn, w, st) {
  const card = document.createElement("div"); card.className = "card";
  const row = document.createElement("div"); row.className = "row";
  const label = document.createElement("span"); label.textContent = w.label || w.id;
  row.appendChild(label);
  const mapped = fn && CONTROL_MAP[fn] && CONTROL_MAP[fn][w.id];
  if (mapped && mapped.kind === "toggle") {
    const sw = document.createElement("button"); sw.className = "switch";
    const on = !!st[mapped.stateKey];
    sw.setAttribute("aria-checked", on ? "true" : "false");
    sw.onclick = () => command(fn, mapped.what, on ? "off" : "on");
    row.appendChild(sw);
  } else if (mapped && mapped.kind === "slider" && w.range) {
    const [lo, hi] = w.range.split("..").map(Number);
    const inp = document.createElement("input"); inp.type = "range";
    inp.min = lo; inp.max = hi; inp.value = st[mapped.stateKey] ?? lo;
    inp.onchange = () => command(fn, mapped.what, Number(inp.value));
    row.appendChild(inp);
  } else {
    // Not an actuating widget: render read-only. Show the interpreted value if we
    // can find a matching state key, otherwise just the label (never a broken toggle).
    const val = readonlyValue(st, w);
    const b = document.createElement("span"); b.className = "badge";
    b.textContent = val != null ? String(val) : w.type;
    row.appendChild(b);
  }
  card.appendChild(row);
  return card;
}

// Best-effort read-only display value: try the interpreted-state key that matches
// this widget's raw protocol field name. Never used to drive a control (informational
// only) — if nothing matches, the caller just falls back to the widget type/label.
function readonlyValue(st, w) {
  const field = w.controls && w.controls.field;
  if (!field) return null;
  const first = field.split("/")[0].split(" ")[0];
  const snake = first.replace(/([a-z0-9])([A-Z])/g, "$1_$2").toLowerCase();
  if (snake in st) return st[snake];
  const lower = first.toLowerCase();
  if (lower in st) return st[lower];
  return null;
}

function roofControls(fn) {
  const box = document.createElement("div"); box.className = "card";
  for (const dir of ["open", "close", "stop"]) {
    const b = document.createElement("button"); b.textContent = dir; b.className = "badge";
    b.style.marginRight = ".5rem";
    b.onclick = () => { if (confirm("Roof "+dir+": this physically moves the pop-top and is "
        +"UNVERIFIED on this vehicle. Ensure the roof path is clear. Continue?"))
        command(fn, dir, null, true); };
    box.appendChild(b);
  }
  const warn = document.createElement("div"); warn.className = "warn";
  warn.textContent = "Roof control is safety-sensitive and not live-verified."; box.appendChild(warn);
  return box;
}

function lightingNote() {
  const card = document.createElement("div"); card.className = "card";
  const n = document.createElement("div"); n.className = "muted";
  n.textContent = "Note: lighting actuation is not yet supported by the unit over BLE.";
  card.appendChild(n);
  return card;
}

async function main() {
  SCREENS = await api("/api/screens");
  await refreshState();
  poll = setInterval(refreshState, 1500);
}
main();
