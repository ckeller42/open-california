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
| GET | `/api/history?h=24` | Leisure-battery samples for the last `h` hours (1–48) |
| GET | `/api/screens` | UI screen specs |
| POST | `/api/command` | Actuate a control (the only writing endpoint) |
| POST | `/api/session` | Connect/disconnect the BLE session |
| POST | `/api/auto_camper` | Toggle the restore-camping-after-park setting |
