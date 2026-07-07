# Signal scales — what's verified vs UNVERIFIED

The catalog (`protocol/signals.yaml`) records a `scale`/`unit` per surfaced signal.
Several are **`UNVERIFIED`**: we decode the raw field correctly, but the multiplier
(and therefore the human unit) has **not** been confirmed against a live reference.
Treat those as *trends*, not calibrated readings — and do **not** label them with a
unit that implies precision (this is the "fake percentage" trap).

## Verified

| signal(s) | scale | unit | basis |
|---|---|---|---|
| `batt1_v`, `batt2_v` | ×0.1 | V | app getter + live read; **confirmed over 14 d telemetry**: batt1 12.2–14.8 V (mean 13.08), batt2 ~13.5 V nominal — realistic AGM/lead-acid band |
| `soc1_level`, `soc2_level` | raw | **0–15 level (NOT %)** | **confirmed over 14 d / 2472 samples**: soc1 ranged 6–14, soc2 10–14, mean ~9–10; **never approached 100 and capped at 14** (the 4-bit max). If it were a percentage a parked-van drain would show 60–100; instead it behaves as a coarse level. Grafana shows a 0–15 bar gauge. |
| water `fresh/waste_percent` | derived (`Level×100/Volume`) | % | live-verified (11 L / 29 L = 38 %) |

### Sentinels found in the telemetry (raw fields carry "no-data" markers)

Confirmed from 14 d of `calictl serve` history — a raw field is only meaningful when its
subsystem is **installed and awake**, otherwise it reports a sentinel:

| sentinel | fields | meaning |
|---|---|---|
| `0x81` (129) | `IOneBattBemAfs` (starter current) | engine-off / no measurement (already handled in `semantics.energy`) |
| `511` (0x1FF) | `solar_current` (constant 511, solar **not fitted**), `shore_current` (tops out at 511) | not-installed / no-data marker for the source-current fields |
| constant default | `air_temp`≡20, `water_temp`≡0 (LR-heater **not fitted**) | uninstalled-function placeholder, **not a reading** — scale unlearnable here |

Takeaway: **never verify a raw scale against an uninstalled/asleep subsystem** — its
"values" are sentinels/defaults. `air_temp`/`water_temp`/`target_temp` and the solar/shore
currents stay `UNVERIFIED` because this van can't exercise them live.

## UNVERIFIED — decode correct, scale not confirmed

| signal(s) | what we know | do NOT assume |
|---|---|---|
| `batt2_current`, `dcdc_current`, `shore_current`, `solar_current` | raw **signed** integers (8- or 16-bit). Sign/direction plausible; magnitude scale unknown. 14 d telemetry: batt2_current −49…318 (mean ~0, balanced), dcdc −2…29, dcdc_power 0…45; **solar/shore read the `511` sentinel** when not fitted (see above). | Not confirmed amps. Grafana labels them **"raw, unverified scale"**, not "A". A `511` reading is *no data*, not a huge current. |
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

## Open review items (inverted readback, not installed → unverifiable)

The `SEMANTIC-REVIEW-NEEDED` auditor check (`tools/app_setters.py`) flags functions whose
app code inverts a field (`!on?1:0` setter or `!((Boolean)…)` readback getter) but whose
surfaced fields don't mark it inverted. After the camping fix, two remain — **not
installed on this van, so their polarity cannot be live-verified**:

| function | evidence | status |
|---|---|---|
| `satelliteantenna` | inverted readback in `mg/f.java` | boolean polarity UNVERIFIED — resolve when a sat-equipped vehicle is available |
| `stairs` | inverted setter + readback in `og/b.java` | `extended`/`obstacle_sensor` polarity UNVERIFIED — resolve when a stairs-equipped vehicle is available |

These stay flagged **on purpose** — the check is honestly reporting real, unresolved risk.
Do not "acknowledge" them by marking a scale `inverted` without verifying which field, or
you re-create the camping mistake in the opposite direction.
