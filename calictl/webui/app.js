// Renders the CaliforniaOnTour Vehicle-tab replica from /api/screens + /api/state.
const app = document.getElementById("app");
const titleEl = document.getElementById("title");
const backEl = document.getElementById("back");
let SCREENS = {order: [], screens: {}};
let STATE = {};
let view = "home";
let poll;

async function api(path, opts) {
  const r = await fetch(path, opts);
  return r.json();
}
async function refreshState() { STATE = await api("/api/state"); render(); }

async function command(function_, what, value, confirm) {
  const body = {function: function_, what, value, confirm: !!confirm};
  const res = await api("/api/command", {method: "POST",
    headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
  if (res.applied === false) toast("Sent — but the unit did not apply it (known gap).");
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
  const st = STATE[scr.function] || {};
  for (const w of scr.widgets) app.appendChild(renderWidget(scr, w, st));
}

function renderWidget(scr, w, st) {
  const card = document.createElement("div"); card.className = "card";
  const row = document.createElement("div"); row.className = "row";
  const label = document.createElement("span"); label.textContent = w.label || w.id;
  row.appendChild(label);
  const fn = scr.function;
  if (w.type === "toggle") {
    const sw = document.createElement("button"); sw.className = "switch";
    const on = !!(st.on ?? st[fieldToState(w)] );
    sw.setAttribute("aria-checked", on ? "true" : "false");
    sw.onclick = () => command(fn, toggleWhat(scr, w), on ? "off" : "on");
    row.appendChild(sw);
  } else if (w.type === "slider" && w.range) {
    const [lo, hi] = w.range.split("..").map(Number);
    const inp = document.createElement("input"); inp.type = "range";
    inp.min = lo; inp.max = hi; inp.value = st.level ?? lo;
    inp.onchange = () => command(fn, "level", Number(inp.value));
    row.appendChild(inp);
  } else {
    const b = document.createElement("span"); b.className = "badge"; b.textContent = w.type;
    row.appendChild(b);
  }
  card.appendChild(row);
  if (fn === "roof") card.appendChild(roofControls(fn));
  if (fn === "lighting") { const n=document.createElement("div"); n.className="muted";
    n.textContent="Note: lighting actuation is not yet supported by the unit over BLE."; card.appendChild(n); }
  return card;
}

// map a widget to the on/off "what" the command API expects (cooler power, camping master/lights/usb...)
function toggleWhat(scr, w) {
  if (scr.function === "cooler") return "power";
  const id = (w.id || "").toLowerCase();
  if (id.includes("usb")) return "usb";
  if (id.includes("light")) return "lights";
  if (id.includes("master") || id.includes("camping")) return "master";
  return "power";
}
function fieldToState(w){ return (w.controls && w.controls.field || "").toLowerCase(); }

function roofControls(fn) {
  const box = document.createElement("div");
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

async function main() {
  SCREENS = await api("/api/screens");
  await refreshState();
  poll = setInterval(refreshState, 1500);
}
main();
