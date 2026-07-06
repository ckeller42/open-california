# Signal scales — what's verified vs UNVERIFIED

The catalog (`protocol/signals.yaml`) records a `scale`/`unit` per surfaced signal.
Several are **`UNVERIFIED`**: we decode the raw field correctly, but the multiplier
(and therefore the human unit) has **not** been confirmed against a live reference.
Treat those as *trends*, not calibrated readings — and do **not** label them with a
unit that implies precision (this is the "fake percentage" trap).

## Verified

| signal(s) | scale | unit | basis |
|---|---|---|---|
| `batt1_v`, `batt2_v` | ×0.1 | V | app getter + live read (~13.x V) |
| water `fresh/waste_percent` | derived (`Level×100/Volume`) | % | live-verified (11 L / 29 L = 38 %) |

## UNVERIFIED — decode correct, scale not confirmed

| signal(s) | what we know | do NOT assume |
|---|---|---|
| `soc1_level`, `soc2_level` | **4-bit field, range 0–15** — a *coarse* state-of-charge level. The app shows it as a **bar in the battery-capacity symbol** (VW T7 manual), not a precise %. | Not a percentage. `10` ≈ "⅔ full", not "67 %". Grafana shows it as a **0–15 bar gauge**, matching the van. |
| `batt2_current`, `dcdc_current`, `shore_current`, `solar_current` | raw **signed** integers (8- or 16-bit). Sign/direction plausible; magnitude scale unknown. | Not confirmed amps. Grafana labels them **"raw, unverified scale"**, not "A". |
| `air_temp`, `water_temp` (LR-heater), `target_temp` (roof-A/C) | raw integers; likely °C-ish but multiplier/offset unconfirmed. | Not confirmed °C. |
| `energy_mode` | 0/1/2 → eco/normal/max **order inferred** from the GUI string keys (`energyMode_eco/normal/max`), not observed live. | The 0↔eco mapping order is a guess until confirmed. |

## How to verify a scale

1. Drive a known state (e.g. a measured shore-charging current, a thermometer next
   to the heater sensor, a battery monitor's %).
2. `python3 -m calictl get energy` (raw + interpreted) at that moment.
3. Solve for the multiplier; update the catalog `scale`/`unit`, flip `confidence` to
   `high`, and (per the dashboard-sync hook) update the Grafana panel unit + push.

The coverage guardrail (`tests/test_signal_coverage.py`) enforces that these signals
stay surfaced; it does **not** assert the scale — that's what this doc + a live check
are for.
