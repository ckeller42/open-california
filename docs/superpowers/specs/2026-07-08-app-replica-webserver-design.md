# App-replica webserver — design

## Context

`calictl` controls the camper over BLE from the CLI, MQTT/Home Assistant, and (soon) a web
UI. The owner wants a **standalone web UI that mirrors the CaliforniaOnTour app's Vehicle-tab
UX** — the same dashboard → remote-control tiles → per-feature screens, the same widgets and
labels — so the van can be driven from any browser without the vendor's iOS/Android app.

The app's UX is already reverse-engineered into data: `ui/screens/*.yaml` (18 screens: widget
tree, `controls:{field,action}` → dictionary fields, `label_key`s) plus `ui/prototype.html` +
`ui/build_prototype.py` that render them, and `ui/README.md`'s nav map. This turns those specs
into a **live** UI wired to the unit.

## Decisions (from brainstorming)

1. **Labels/assets: neutral committed + local overlay.** The app's real label TEXT and icons
   are VW copyrighted content (icons were already purged from the repo, regenerated locally,
   gitignored). We commit our own neutral labels; an optional runtime overlay swaps in the real
   VW text/icons from the owner's local APK — never committed. Same pattern as the icons.
2. **Web embedded in `serve`.** `http.server` is stdlib, so the web server runs *inside* the
   one BLE-owning `serve` process, reusing `on_command` + the state cache. No second BLE
   connection, no MQTT broker. `calictl serve --web [PORT]`.
3. **Scope: the Vehicle-tab control flow.** Dashboard (home) + per-installed-feature control
   screens (coolbox, camping-mode, lighting, air-heater, roof, water, energy, vehicle-info).
   Uninstalled features are `Installed`-gated hidden, as in the app. Out of scope: the
   Discover/Plan/Travel/Account tabs, onboarding, settings, notifications, self-check, pairing.

## Constraints

- **`serve` is the single BLE owner.** The web layer never opens BLE; state comes from serve's
  cache, commands go through `on_command` under the existing `asyncio.Lock` (the same
  `run_coroutine_threadsafe` bridge the MQTT command path uses, `serve.py`).
- **Runtime `calictl/*` is stdlib-only at import.** `web.py` uses only `http.server`/`json`
  (both stdlib); it is imported lazily from `serve` like `bleak`/`mqtt`. It loads **pre-built
  JSON** assets (`screens.json`) — it does NOT parse YAML at runtime (screen YAML is compiled to
  JSON by the build step, keeping the runtime free of a PyYAML dependency).
- **No VW copyrighted content in the repo.** Committed assets carry only neutral labels and
  placeholder icons; a CI check enforces it. Real text/icons load locally at build time only.

## Architecture

### 1. Build step — `tools/build_web.py`
Compiles the UI **data** into one committed JSON file: `ui/screens/*.yaml` + `ui/labels.yaml`
(neutral) → `calictl/webui/screens.json` (widget tree with labels resolved). The HTML/CSS/JS in
`calictl/webui/` are **authored static files**, not generated — they fetch `screens.json` and
render it client-side; the JS renderer mirrors `ui/build_prototype.py`'s widget visuals for
parity, but is hand-written, not emitted by the build. `build_web` only produces the data (and,
with the local overlay, the icon bundle).
- **Neutral labels**: `ui/labels.yaml` maps `label_key → text` for the primary controls;
  unmapped keys fall back to a mechanical humanization (strip the `screenPage_`/`_text`
  affixes, split camelCase → "Cooling level"). No VW strings.
- **Local overlay (never committed)**: `--strings <apk .cvr>` resolves `label_key → real VW
  text`; `--icons ui/assets/svg/` embeds the real drawables. Produces the exact-looking app on
  the owner's machine; the gitignored output is the owner's only.
- A `PostToolUse`/CI reminder to rebuild when `ui/screens/*` or `ui/labels.yaml` change (like
  the dashboard-sync hook).

### 2. Runtime — `calictl/web.py`
A stdlib `http.server.ThreadingHTTPServer` handler, lazy-imported from `serve`:
- `GET /` and `/static/*` → serve `calictl/webui/` (index + css + js).
- `GET /api/state` → `{function: interpreted_state}` from serve's `_last` cache + `semantics`,
  including each function's `installed` flag. Cheap, no BLE (reads the poll cache).
- `GET /api/screens` → the compiled `screens.json` (widget tree + resolved labels).
- `POST /api/command` `{function, what, value}` → validates against the known `control.BUILDERS`
  targets, then `asyncio.run_coroutine_threadsafe(serve.on_command(...), loop)`; returns
  `{ok, applied|not_applied, state}`. Honors `--read-only` (405) and the roof guard.
Bound to the serve event loop; the HTTP server runs in serve's thread pool so a slow BLE write
never blocks the poll loop beyond the existing lock.

### 3. Frontend (`calictl/webui/`, authored static HTML/CSS/JS + generated `screens.json`)
- **Dashboard** (`home.yaml`): battery/water summary tiles + the remote-control grid; each tile
  is shown only if that function's `installed` is true. Tap → feature screen.
- **Feature screens**: render the spec's widget tree — `toggle`→switch, `slider`→range,
  `section`/`drawer`→disclosure, `timer`→time controls — bound to `/api/state`, actions POST to
  `/api/command`. Same layout/flow as `prototype.html`.
- Polls `/api/state` ~1.5 s to reflect live values (and other controllers, e.g. the phone app).
- Styling: the prototype's clean card/toggle theme (light/dark), no VW branding.

## Data flow

```
browser ──GET /api/state──▶ web.py ──▶ serve._last (poll cache) ──▶ JSON
browser ──POST /api/command─▶ web.py ──run_coroutine_threadsafe──▶ serve.on_command
                                          └─ under asyncio.Lock ─▶ device.actuate ─▶ unit
```

## Safety / honesty

- **Cooler + camping** actuate for real. **Lighting** renders but `/api/command` reports
  `not_applied` (the real, unsolved gate — never faked green).
- **Roof** is safety-sensitive: an extra confirm step + the CLI's warnings; the POST carries an
  explicit `confirm` field the UI only sets after the dialog. Off by default behind the gesture.
- **`--read-only`** serves the dashboard with controls disabled and rejects `/api/command`.
- Uninstalled features never render (Installed gate), matching the app.

## Files
- New: `calictl/web.py` (runtime server + API), `tools/build_web.py` (build step),
  `ui/labels.yaml` (neutral labels), `calictl/webui/{index.html, app.css, app.js}` (authored)
  + `calictl/webui/screens.json` (generated by build_web), `tests/test_web.py`,
  `tests/test_build_web.py`.
- Touch: `calictl/serve.py` (`--web` wiring: start the HTTP server in the loop, pass the
  command bridge), `calictl/cli.py` (the `serve --web PORT`/`--read-only` flags),
  `.github/workflows/ci.yml` (assert `calictl/webui/` has no VW strings/icons; run build_web),
  `.gitignore` (local overlay outputs), `README.md` (a "Web UI" line).

## Testing
- `tools/build_web`: screens→`screens.json` shape; neutral-label humanization; **no VW text in
  committed output** (assert against a small denylist / the label-key set). Runs in CI.
- `calictl/web.py`: drive the API against a fake serve backed by the **mock unit**
  (`tools/mock_unit.py`) — `GET /api/state` shape + installed flags, `POST /api/command` routes
  to `on_command` (cooler on→state flips; lighting→not_applied), `--read-only` rejects, roof
  requires `confirm`. All stdlib, no BLE.
- Import-clean: importing `calictl.web` pulls no non-stdlib module.

## Out of scope
Discover/Plan/Travel/Account tabs, onboarding/settings/notifications/self-check, the pairing
wizard (that's `install.sh`), pixel-identical VW branding (no committed VW assets), auth/multi-
user (bind to localhost / the LAN like the existing stack; document not exposing it publicly).
