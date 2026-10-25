# What calictl does today

This page lists, for each function of the VW California camper unit, what calictl shows, what it
can control, where, and how well that is proven. It is generated from the code (the signal catalog,
the command builders, the Home Assistant entities and the satellite's write list), so it changes
when the code changes.

**Where** has three columns:

- **buspi** — the calictl daemon on the Raspberry Pi: its web UI, the `calictl set` command line
  and the HTTP API. The web UI shows only the functions fitted to the van.
- **Home Assistant** — the entity calictl publishes over MQTT, with its name.
- **Satellite** — the ESP32 satellite's web UI (the same web UI, served by the satellite). It writes
  to five functions only; for anything else it answers "Only via buspi or the app".

Both web UIs show the same values. A value without a unit is a level, a mode or an on/off flag by
nature, not a measurement. Labels are the web UI's, with the German term in parentheses. The
**Value** column gives the value's name in the daemon's state (`/api/state`); a value without a web
UI label is in that state, but the web UI does not show it in a row of its own.

**Proof** says how far a function is checked, in one of four steps:

- *live-verified on the real unit*
- *readings live-verified; controls verified against the app only*
- *verified against the app's code only*
- *not fitted on this van*

The evidence for each claim is in the [evidence ledger](https://ckeller42.github.io/open-california/business-logic/evidence-ledger.html).

## Cooler (Kühlbox)

**Proof:** live-verified on the real unit ([evidence](https://ckeller42.github.io/open-california/business-logic/evidence-ledger.html)). Power, cooling level and the quiet-mode hours are confirmed on the unit; the unit stores the cooling start time, but switching on at that time is not yet observed.

**You can see:**

| Web UI label | Value | Unit |
|---|---|---|
| — | `error` | — |
| Cooling level (Kühlstufe) | `level` | — |
| Quiet mode (Flüstermodus) | `mode` | — |
| Refrigerator box (Kühlbox) | `on` | — |
| Timer | `timer_active` | — |

**You can control** (`calictl set cooler <command> <value>`):

| Command | Label | Values | buspi | Home Assistant | Satellite | Note |
|---|---|---|---|---|---|---|
| `power` | Refrigerator box (Kühlbox) | on / off | yes | Fridge Power | yes | — |
| `level` | Cooling level (Kühlstufe) | 1–5 | yes | Fridge Set Level | yes | — |
| `mode` | Quiet mode (Flüstermodus) | normal / quiet / timer_quiet | yes | — | yes | off / manual / automatic (by the hours below); only while the box is on |
| `night_on` | Quiet mode starts at (Flüstermodus startet um) | hour 0–23 | yes | — | yes | only while the box is on and its state is known |
| `night_off` | Quiet mode ends at (Flüstermodus endet um) | hour 0–23 | yes | — | yes | only while the box is on and its state is known |
| `timer_set` | Cooling starts at (Kühlen startet um) | HH:MM | yes | — | yes | only while the box is off |
| `timer_start` | Start timer (Timer starten) | — | yes | — | yes | only while the box is off |
| `timer_cancel` | Cancel timer (Timer abbrechen) | — | yes | — | yes | — |

## Camping mode (Campingmodus)

**Proof:** live-verified on the real unit ([evidence](https://ckeller42.github.io/open-california/business-logic/evidence-ledger.html)). Camping mode, its lights and the rear USB ports are confirmed on the unit.

**You can see:**

| Web UI label | Value | Unit |
|---|---|---|
| — | `enable` | — |
| Exterior and interior lighting (Außen- und Innenbeleuchtung) | `lights_on` | — |
| Camping mode (Campingmodus) | `master_on` | — |
| Rear USB ports (Hintere USB-Anschlüsse) | `usb_charger` | — |

**You can control** (`calictl set campingmode <command> <value>`):

| Command | Label | Values | buspi | Home Assistant | Satellite | Note |
|---|---|---|---|---|---|---|
| `master` | Camping mode (Campingmodus) | on / off | yes | Camping Mode | yes | the web UI greys it while the ignition is on |
| `lights` | Exterior and interior lighting (Außen- und Innenbeleuchtung) | on / off | yes | Camping Lights Switch | yes | only while camping mode is on |
| `usb` | Rear USB ports (Hintere USB-Anschlüsse) | on / off | yes | Rear USB Ports Switch | yes | only while camping mode is on |

## Lighting (Beleuchtung)

**Proof:** live-verified on the real unit ([evidence](https://ckeller42.github.io/open-california/business-logic/evidence-ledger.html)). Lamps, all lights, the sliding-door light and the wake-up light are confirmed on the unit; favourites match the app's own frames; a favourite's colour is from the app's code only.

**You can see:**

| Web UI label | Value | Unit |
|---|---|---|
| — | `brightness_zone_N` (N = 1–16) | — |
| All lights (Alle Lichter) | `any_on` | — |
| — | `mode` | — |
| — | `profile` | — |
| Wake-up light (Wecklicht) | `wakeup` | — |

**You can control** (`calictl set lighting <command> <value>`):

| Command | Label | Values | buspi | Home Assistant | Satellite | Note |
|---|---|---|---|---|---|---|
| `power` | All lights (Alle Lichter) | on / off | yes | Interior Lights | yes | restores the lamps' saved levels |
| `reading-1` | Reading lights (Leselichter) · Left (Links) | 0–11 | yes | — | yes | — |
| `reading-2` | Reading lights (Leselichter) · Right (Rechts) | 0–11 | yes | — | yes | — |
| `reading-3` | Reading lights (Leselichter) · Front passenger (Beifahrer) | 0–11 | yes | — | yes | — |
| `kitchen` | Kitchen (Küche) · Cooking (Kochen) | 0–11 | yes | — | yes | — |
| `roof-ambient` | Pop-up roof (Aufstelldach) · Background lighting (Ambientelicht) | 0–11 | yes | — | yes | — |
| `outside-rear` | Exterior light (Außenlicht) · Rear surroundings (Umgebung hinten) | 0–11 | yes | — | yes | — |
| `kitchen-ambient` | Kitchen (Küche) · Background lighting (Ambientelicht) | 0–11 | yes | — | yes | — |
| `kitchen-cabinet` | Kitchen (Küche) · Cabinet (Schrank) | 0–11 | yes | — | yes | — |
| `roof-reading` | Pop-up roof (Aufstelldach) · Reading light (Leselicht) | 0–11 | yes | — | yes | only while the pop-up roof is open |
| `entrance` | Exterior light (Außenlicht) · Entrance (Eingang) | 0–11 | yes | — | yes | — |
| `all` | Every lamp | 0–11 | yes | — | yes | sets every fitted lamp to one level |
| `brightness` | Every lamp | 0–11 | yes | All Lights Brightness | — | the same as `all`; the Home Assistant slider uses it |
| `profile` | Favourite | 0–13 except 8 | yes | — | yes | 1–7 = a stored favourite, 0 = all off, 12 = all on; a favourite the unit reports empty is refused |
| `save_profile` | Save favourite | N [colour] | yes | — | yes | N = 1–7; colour = one of warm-white, blood-orange, amber, pistachio, peppermint, mint, azure, dark-blue, red, salmon |
| `door_contact` | Sliding door light | on / off | yes | — | yes | the rear interior lights come on when the sliding door opens |
| `wakeup` | Wake-up light (Wecklicht) | HH:MM [areas] [brightness] [ramp] [on/off] | yes | — | yes, with the web page's clock | areas 1–4 (comma list), brightness 0–10, ramp 0/10/20/30 min |

Lamp levels: 0 = off, 1–10 = 10–100 %, 11 = the lamp's default.

## Air heater (Luftstandheizung)

**Proof:** live-verified on the real unit ([evidence](https://ckeller42.github.io/open-california/business-logic/evidence-ledger.html)). Calictl has run the heater on the unit (level, run time, start time, timer); the unit starting an armed timer at its time is not yet observed.

**You can see:**

| Web UI label | Value | Unit |
|---|---|---|
| — | `air_distribution` | — |
| Error (Fehler) | `error_code` | — |
| Heating temperature (Heizstufe) | `level` | — |
| — | `running` | — |
| Timer | `mode` | — |
| Continuous heating (Dauerbetrieb) | `permanent` | — |
| Run time (Laufzeit) | `running_time` | min |

**You can control** (`calictl set airheater <command> <value>`):

| Command | Label | Values | buspi | Home Assistant | Satellite | Note |
|---|---|---|---|---|---|---|
| `power` | Immediate heating (Sofortheizen) | on / off | yes | Air Heater Power | yes | on is refused while the unit reports the heater not possible (engine running or water heater on) |
| `permanent` | Continuous heating (Dauerbetrieb) | off | yes | — | yes | can only be started from inside the vehicle |
| `level` | Heating temperature (Heizstufe) | 1–10 | yes | Air Heater Set Level | yes | 10 = HI |
| `runtime` | Run time (Laufzeit) | 0–120 min | yes | — | yes | not during continuous heating |
| `timer` | Start heating at (Heizen startet um) | HH:MM | yes | — | yes | not while the timer is on |
| `timer_start` | Start timer (Timer starten) | — | yes | — | yes | the heater then starts at the set time for the run time |
| `timer_cancel` | Stop timer (Timer stoppen) | — | yes | — | yes | — |

## Water (Wasser)

**Proof:** live-verified on the real unit ([evidence](https://ckeller42.github.io/open-california/business-logic/evidence-ledger.html)). The unit measures only while its water system is on; when parked, calictl keeps the last plausible reading and marks it stale.

**You can see:**

| Web UI label | Value | Unit |
|---|---|---|
| — | `fresh_alert` | — |
| Fresh water (Frischwasser) | `fresh_percent` | % |
| Fresh water (Frischwasser) | `fresh_capacity_l` | L |
| — | `waste_alert` | — |
| Waste water (Grauwasser) | `waste_percent` | % |
| Waste water (Grauwasser) | `waste_capacity_l` | L |

Nothing to control.

## Energy (Energie)

**Proof:** readings live-verified; controls verified against the app only ([evidence](https://ckeller42.github.io/open-california/business-logic/evidence-ledger.html)). The readings match the app on the real unit; the energy-mode command matches the app's own frames. Like the app, calictl offers ECO only when the unit reports a solar panel.

**You can see:**

| Web UI label | Value | Unit |
|---|---|---|
| Starter data age (Alter der Starterdaten) | `age_min` | min |
| — | `derating_temp_active` | — |
| — | `dcdc_installed` | — |
| Energy mode (Energiemodus) | `energy_mode` | — |
| — | `energy_mode_locked` | — |
| Vehicle power (Fahrzeugstrom) | `dcdc_current` | A |
| Shore power (Landstrom) | `shore_current` | A |
| Starter power (Starter-Leistung) | `batt1_current` | A |
| Solar power (Solarstrom) | `solar_current` | A |
| Second battery power (Zweitbatterie-Leistung) | `batt2_current` | A |
| — | `shore_installed` | — |
| Vehicle power (Fahrzeugstrom) | `dcdc_power` | W |
| Shore power (Landstrom) | `shore_power` | W |
| Solar power (Solarstrom) | `solar_power` | W |
| — | `solar_installed` | — |
| Issues (Probleme) | `sleep_warning` | — |
| Starter battery (Starterbatterie) | `soc1_level` | — |
| Second battery (Zweitbatterie) | `soc2_level` | — |
| Vehicle power (Fahrzeugstrom) | `dcdc_charging` | — |
| Starter power (Starter-Leistung) | `batt1_v` | V |
| Second battery power (Zweitbatterie-Leistung) | `batt2_v` | V |
| Time remaining (Restzeit) | `batt2_remaining_h` | h |
| — | `batt2_remaining_min` | min |

**You can control** (`calictl set energy <command> <value>`):

| Command | Label | Values | buspi | Home Assistant | Satellite | Note |
|---|---|---|---|---|---|---|
| `mode` | Energy mode (Energiemodus) | normal / max_charge / eco | yes | — | yes | refused while the unit locks the energy mode |

## Roof (Aufstelldach)

**Proof:** readings live-verified; controls verified against the app only ([evidence](https://ckeller42.github.io/open-california/business-logic/evidence-ledger.html)). The pop-top roof is fitted and its position is confirmed; calictl has never driven the roof motor. A move needs the ignition on and is press-and-hold.

**You can see:**

| Web UI label | Value | Unit |
|---|---|---|
| Position | `position` | — |
| Safety valid (Sicherheit gültig) | `safety_valid` | — |

**You can control** (`calictl set roof <command>`):

| Command | Label | Values | buspi | Home Assistant | Satellite | Note |
|---|---|---|---|---|---|---|
| `open` | Open (Öffnen) | — | yes | — | — | press and hold; release stops |
| `close` | Close (Schließen) | — | yes | — | — | press and hold; release stops |
| `stop` | Stop (Stopp) | — | yes | — | — | calictl never refuses it |

## Vehicle (Fahrzeug)

**Proof:** live-verified on the real unit ([evidence](https://ckeller42.github.io/open-california/business-logic/evidence-ledger.html)). Roll and pitch show only while the ignition is on and the van is not driving.

**You can see:**

| Web UI label | Value | Unit |
|---|---|---|
| Ignition (Zündung) | `ignition_on` | — |
| Model (Modell) | `car_variant` | — |
| — | `level_popup` | — |
| Level indicator (roll / pitch) (Niveauanzeige (Roll / Nick)) | `level_roll` | ° |
| Level indicator (roll / pitch) (Niveauanzeige (Roll / Nick)) | `level_pitch` | ° |

Nothing to control.

## Unit firmware (Firmware der Einheit)

**Proof:** live-verified on the real unit ([evidence](https://ckeller42.github.io/open-california/business-logic/evidence-ledger.html)). The software versions the unit reports.

**You can see:**

| Web UI label | Value | Unit |
|---|---|---|
| Firmware (amb · cm · comm) (Firmware (Umg. · CM · Komm)) | `amb_sw_version` | — |
| Firmware (amb · cm · comm) (Firmware (Umg. · CM · Komm)) | `cm_sw_version` | — |
| Firmware (amb · cm · comm) (Firmware (Umg. · CM · Komm)) | `comm_version` | — |

Nothing to control.

## General-purpose signals

**Proof:** verified against the app's code only ([evidence](https://ckeller42.github.io/open-california/business-logic/evidence-ledger.html)). Calictl decodes this function but shows none of its values.

Nothing to control.

## Stairs

**Proof:** not fitted on this van ([evidence](https://ckeller42.github.io/open-california/business-logic/evidence-ledger.html)). Frames and values are checked against the app's code only.

**You can see:**

| Web UI label | Value | Unit |
|---|---|---|
| — | `mode` | — |
| — | `obstacle_sensor` | — |
| — | `extended` | — |

**You can control** (`calictl set stairs <command> <value>`):

| Command | Label | Values | buspi | Home Assistant | Satellite | Note |
|---|---|---|---|---|---|---|
| `move` | Move | extend / retract / stop | yes | — | — | — |
| `mode` | Mode | 0–3 | yes | — | — | — |

## Living-room heater

**Proof:** not fitted on this van ([evidence](https://ckeller42.github.io/open-california/business-logic/evidence-ledger.html)). Frames and values are checked against the app's code only.

**You can see:**

| Web UI label | Value | Unit |
|---|---|---|
| — | `error` | — |
| — | `mode` | — |
| — | `air_on` | — |
| — | `water_on` | — |
| — | `air_temp` | — |
| — | `water_temp_flag` | — |

**You can control** (`calictl set livingroomheater <command> <value>`):

| Command | Label | Values | buspi | Home Assistant | Satellite | Note |
|---|---|---|---|---|---|---|
| `air` | Air heating | on / off | yes | — | — | — |
| `water` | Water heating | on / off | yes | — | — | — |
| `temperature` | Temperature (Temperatur) | 0–255 | yes | — | — | the unit's raw value; its scale is unknown |
| `temperatureair` | Temperature (Temperatur) | 0–255 | yes | — | — | the same as `temperature` |

## Roof air conditioning

**Proof:** not fitted on this van ([evidence](https://ckeller42.github.io/open-california/business-logic/evidence-ledger.html)). Frames and values are checked against the app's code only.

**You can see:**

| Web UI label | Value | Unit |
|---|---|---|
| — | `error` | — |
| — | `fan_speed` | — |
| — | `mode` | — |
| — | `on` | — |
| — | `target_temp` | — |

**You can control** (`calictl set roofaircondition <command> <value>`):

| Command | Label | Values | buspi | Home Assistant | Satellite | Note |
|---|---|---|---|---|---|---|
| `power` | Power | on / off | yes | — | — | — |
| `mode` | Mode | 0–3 | yes | — | — | automatic / cooling / heating / venting |
| `fanspeed` | Fan speed | 0–4 | yes | — | — | 4 = automatic |
| `temperature` | Temperature (Temperatur) | 0–255 | yes | — | — | the unit's raw value; its scale is unknown |

## Satellite dish

**Proof:** not fitted on this van ([evidence](https://ckeller42.github.io/open-california/business-logic/evidence-ledger.html)). Calictl decodes it but sends no commands.

**You can see:**

| Web UI label | Value | Unit |
|---|---|---|
| — | `dish` | — |
| — | `error` | — |
| — | `satellite` | — |
| — | `signal_level` | — |
| — | `system_on` | — |

Nothing to control.
