# calictl web API

The `calictl serve` daemon embeds a small **unauthenticated JSON API** (LAN-only) that backs the
web UI. It never opens BLE: reads come from the poll cache, writes bridge to the BLE control path
under the daemon's lock.

- **Spec:** [`openapi.yaml`](openapi.yaml) — OpenAPI 3.1, the complete contract (routes, request/
  response schemas, status codes, the `confirm:true` rule for safety-sensitive functions).
- **View it:** `npx @redocly/cli preview docs/api/openapi.yaml` (or any Swagger UI / Redoc / VS Code
  OpenAPI extension).
- **Kept honest by:** `tests/test_openapi_coverage.py` — fails CI if the routes, POST allowlist,
  `CONFIRM_REQUIRED` set, or error tokens drift from `calictl/web.py`.

Per-function state fields (cooler/energy/water/…) live in the signal catalog
(`protocol/signals.yaml`, `docs/business-logic/signals.md`); this spec fixes the API envelope only.

## Endpoints at a glance

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/state` | Full interpreted state of every function + `_meta` |
| GET | `/api/screens` | UI screen specs |
| POST | `/api/command` | Actuate a control (the only writing endpoint) |
| POST | `/api/session` | Connect/disconnect the BLE session |
| POST | `/api/auto_camper` | Toggle the restore-camping-after-park setting |
| GET | `/api/pairing` | Guided-pairing wizard state |
| POST | `/api/pairing` | Drive the pairing wizard (`start`/`passkey`/`cancel`/`reset`; not a vehicle write) |

## ESP32 satellite only

The satellite serves the same envelope plus its own settings endpoints (contract:
`docs/firmware.md`, "Endpoints"). They are not in `openapi.yaml`, which
`tests/test_openapi_coverage.py` holds to `calictl/web.py`'s routes.

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/pi_wake` | Wake the halted Raspberry Pi: GPIO9 low 200 ms (`{}` or no body; 409 `busy` while a pulse runs; station mode only) |
| POST | `/api/pi_halt` | Shut the Raspberry Pi down: GPIO8 low 500 ms (`{}` or no body; 409 `busy` while either line is low; station mode only) |
| GET | `/api/ble` | The BLE link mode, the probe and the night experiment: `{"mode":"hold"\|"quiet"\|"off","probing":<bool>,"exp":null\|{…}}` (= `/api/state` `device.ble`) |
| POST | `/api/ble` | `{"mode":"hold"\|"quiet"\|"off"}`, `{"probe":true}`, `{"exp_min":1..720,"from"?:0..23,"to"?:0..23}` or `{"exp_min":0}`; answers `device.ble`; 409 `no_time` (no wall clock), 409 `busy` (probe running); station mode only |
| POST | `/api/pi_wake_setting` | Any non-empty subset of `{"enabled":<bool>,"halt_after_min":0..1440,"halt_soc_pct":0..100}`: wake on the ignition edge (default on), shut down after parking (default 30 min, 0 off), the battery guard (default 30 %, 0 off) |
