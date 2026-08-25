# Signal provenance / coverage matrix

GENERATED from `protocol/signals.yaml` — do not hand-edit; see [README.md](README.md) for how to regenerate.

Every catalogued field, its surface/omit decision, and the evidence behind it. Sorted by function, then state/control, then field name for a stable diff.

- **confidence**: signals.yaml's own evidence-tier tag (`high`/`medium`); absent on most `omit` entries.
- **provenance**: which of `app` (decompiled getter/setter), `gui` (UI screen spec key), `vwdoc` (vendor manual), `live` (a captured live sample) back this entry — signals.yaml's `sources` map.
- **semantic-review**: signals.yaml persists **no** dedicated review-status field — `tools/audit_signals.py --report` computes `SEMANTIC-REVIEW-NEEDED` live against a decompile source tree, it isn't stored here. This column is a best-effort proxy: entries whose `scale` mentions "inverted" or "combined" (the only persisted marker of a non-trivial transform, e.g. the camping-lights case) are flagged `needs-review`; run the real auditor for anything authoritative.

Totals: 228 fields catalogued, 91 surfaced, 137 omitted, 1 flagged for review.

| function | category | field | decision | surfaced-name | confidence | provenance | semantic-review | omit-reason |
|---|---|---|---|---|---|---|---|---|
| airheater | control | AirDistribution | omit | — | — | — | — | control command field — definition captured in dictionary/overrides; not surfaced because roof-A/C is not installed on this van / set is not wired (control writes work — issue #2 solved 2026-07-07) |
| airheater | control | HeatingLevel | omit | — | — | — | — | control command field — definition captured in dictionary/overrides; not surfaced because roof-A/C is not installed on this van / set is not wired (control writes work — issue #2 solved 2026-07-07) |
| airheater | control | NormalOperationRequest | omit | — | — | — | — | control command field — definition captured in dictionary/overrides; not surfaced because roof-A/C is not installed on this van / set is not wired (control writes work — issue #2 solved 2026-07-07) |
| airheater | control | OperationModeAirHeater | omit | — | — | — | — | control command field — definition captured in dictionary/overrides; not surfaced because roof-A/C is not installed on this van / set is not wired (control writes work — issue #2 solved 2026-07-07) |
| airheater | control | OperationModeCombined | omit | — | — | — | — | control command field — definition captured in dictionary/overrides; not surfaced because roof-A/C is not installed on this van / set is not wired (control writes work — issue #2 solved 2026-07-07) |
| airheater | control | PermanentOperationConfirmation | omit | — | — | — | — | control command field — definition captured in dictionary/overrides; not surfaced because roof-A/C is not installed on this van / set is not wired (control writes work — issue #2 solved 2026-07-07) |
| airheater | control | PermanentOperationRequest | omit | — | — | — | — | control command field — definition captured in dictionary/overrides; not surfaced because roof-A/C is not installed on this van / set is not wired (control writes work — issue #2 solved 2026-07-07) |
| airheater | control | RunningTime | omit | — | — | — | — | control command field — definition captured in dictionary/overrides; not surfaced because roof-A/C is not installed on this van / set is not wired (control writes work — issue #2 solved 2026-07-07) |
| airheater | control | TimerHour | omit | — | — | — | — | control command field — definition captured in dictionary/overrides; not surfaced because roof-A/C is not installed on this van / set is not wired (control writes work — issue #2 solved 2026-07-07) |
| airheater | control | TimerMin | omit | — | — | — | — | control command field — definition captured in dictionary/overrides; not surfaced because roof-A/C is not installed on this van / set is not wired (control writes work — issue #2 solved 2026-07-07) |
| airheater | state | AirDistribution | surface | air_distribution | high | — | — | — |
| airheater | state | ErrorCode | surface | error_code | high | — | — | — |
| airheater | state | FaultTriggerBit | omit | — | — | — | — | fault/warning/source-state bit — aggregated into the faults[] / source status, not standalone |
| airheater | state | HeatingLevel | surface | level | high | — | — | — |
| airheater | state | Installed | omit | — | — | — | — | per-function feature-availability flag — surfaced as the 'installed' key |
| airheater | state | NormalOperation | surface | running | high | — | — | — |
| airheater | state | OperationModeAirHeater | surface | mode | high | — | — | — |
| airheater | state | OperationModeCombined | omit | — | — | — | — | granular timer/mode detail — surfaced via the aggregate state, not standalone |
| airheater | state | PermanentOperation | surface | permanent | high | — | — | — |
| airheater | state | PermanentOperationRequestConfirmation | omit | — | — | — | — | granular timer/mode detail — surfaced via the aggregate state, not standalone |
| airheater | state | RunningTime | surface | running_time | high | — | — | — |
| airheater | state | RunningTimeinAction | omit | — | — | — | — | granular timer/mode detail — surfaced via the aggregate state, not standalone |
| airheater | state | TimerHour | omit | — | — | — | — | control/telemetry field with no GUI or getter evidence; not surfaced (revisit if needed) |
| airheater | state | TimerMin | omit | — | — | — | — | control/telemetry field with no GUI or getter evidence; not surfaced (revisit if needed) |
| campingmode | control | InteriorLight | omit | — | — | — | — | control command field — definition captured in dictionary/overrides; not surfaced because roof-A/C is not installed on this van / set is not wired (control writes work — issue #2 solved 2026-07-07) |
| campingmode | control | OutsideLight | omit | — | — | — | — | control command field — definition captured in dictionary/overrides; not surfaced because roof-A/C is not installed on this van / set is not wired (control writes work — issue #2 solved 2026-07-07) |
| campingmode | control | State | omit | — | — | — | — | control command field — definition captured in dictionary/overrides; not surfaced because roof-A/C is not installed on this van / set is not wired (control writes work — issue #2 solved 2026-07-07) |
| campingmode | control | UsbCharger | omit | — | — | — | — | control command field — definition captured in dictionary/overrides; not surfaced because roof-A/C is not installed on this van / set is not wired (control writes work — issue #2 solved 2026-07-07) |
| campingmode | state | Enable | surface | enable | high | — | — | — |
| campingmode | state | Installed | omit | — | — | — | — | per-function feature-availability flag — surfaced as the 'installed' key |
| campingmode | state | InteriorLight | surface | lights_on | high | — | needs-review (inverted-combined) | — |
| campingmode | state | OutsideLight | omit | — | — | — | — | tied to InteriorLight — the app's single Lights toggle (tf/a K0) writes both together (inverted); surfaced via the combined lights_on |
| campingmode | state | State | surface | master_on | high | — | — | — |
| campingmode | state | UsbCharger | surface | usb_charger | high | — | — | — |
| cooler | control | Level | omit | — | — | — | — | control command field — definition captured in dictionary/overrides; not surfaced because roof-A/C is not installed on this van / set is not wired (control writes work — issue #2 solved 2026-07-07) |
| cooler | control | Mode | omit | — | — | — | — | control command field — definition captured in dictionary/overrides; not surfaced because roof-A/C is not installed on this van / set is not wired (control writes work — issue #2 solved 2026-07-07) |
| cooler | control | NightTimerHourOff | omit | — | — | — | — | control command field — definition captured in dictionary/overrides; not surfaced because roof-A/C is not installed on this van / set is not wired (control writes work — issue #2 solved 2026-07-07) |
| cooler | control | NightTimerHourOn | omit | — | — | — | — | control command field — definition captured in dictionary/overrides; not surfaced because roof-A/C is not installed on this van / set is not wired (control writes work — issue #2 solved 2026-07-07) |
| cooler | control | NightTimerSet | omit | — | — | — | — | control command field — definition captured in dictionary/overrides; not surfaced because roof-A/C is not installed on this van / set is not wired (control writes work — issue #2 solved 2026-07-07) |
| cooler | control | State | omit | — | — | — | — | control command field — definition captured in dictionary/overrides; not surfaced because roof-A/C is not installed on this van / set is not wired (control writes work — issue #2 solved 2026-07-07) |
| cooler | control | TimerCancel | omit | — | — | — | — | control command field — definition captured in dictionary/overrides; not surfaced because roof-A/C is not installed on this van / set is not wired (control writes work — issue #2 solved 2026-07-07) |
| cooler | control | TimerHour | omit | — | — | — | — | control command field — definition captured in dictionary/overrides; not surfaced because roof-A/C is not installed on this van / set is not wired (control writes work — issue #2 solved 2026-07-07) |
| cooler | control | TimerMin | omit | — | — | — | — | control command field — definition captured in dictionary/overrides; not surfaced because roof-A/C is not installed on this van / set is not wired (control writes work — issue #2 solved 2026-07-07) |
| cooler | control | TimerStart | omit | — | — | — | — | control command field — definition captured in dictionary/overrides; not surfaced because roof-A/C is not installed on this van / set is not wired (control writes work — issue #2 solved 2026-07-07) |
| cooler | state | Error | surface | error | high | gui | — | — |
| cooler | state | Installed | omit | — | — | — | — | per-function feature-availability flag — surfaced as the 'installed' key |
| cooler | state | Level | surface | level | high | — | — | — |
| cooler | state | Mode | surface | mode | high | — | — | — |
| cooler | state | NightTimerHourOff | omit | — | — | — | — | granular timer/mode detail — surfaced via the aggregate state, not standalone |
| cooler | state | NightTimerHourOn | omit | — | — | — | — | granular timer/mode detail — surfaced via the aggregate state, not standalone |
| cooler | state | NightTimerSet | omit | — | — | — | — | granular timer/mode detail — surfaced via the aggregate state, not standalone |
| cooler | state | State | surface | on | high | — | — | — |
| cooler | state | TimerCounterHour | omit | — | — | — | — | granular timer/mode detail — surfaced via the aggregate state, not standalone |
| cooler | state | TimerCounterMin | omit | — | — | — | — | granular timer/mode detail — surfaced via the aggregate state, not standalone |
| cooler | state | TimerElapsed | omit | — | — | — | — | granular timer/mode detail — surfaced via the aggregate state, not standalone |
| cooler | state | TimerHourSet | omit | — | — | — | — | granular timer/mode detail — surfaced via the aggregate state, not standalone |
| cooler | state | TimerMinSet | omit | — | — | — | — | granular timer/mode detail — surfaced via the aggregate state, not standalone |
| cooler | state | TimerState | surface | timer_active | high | — | — | — |
| energy | control | DisplayRefresh | omit | — | — | — | — | control command field — definition captured in dictionary/overrides; not surfaced because roof-A/C is not installed on this van / set is not wired (control writes work — issue #2 solved 2026-07-07) |
| energy | control | EnergyModeSet | omit | — | — | — | — | control command field (not telemetry) — now wired as `set energy mode` (control._energy, char 1601: 0=normal 1=max_charge 2=eco, 3=leave-unchanged). Decompile-derived (xf/d.java:389 + enum bf/c.java), not yet wire/live-verified. |
| energy | state | AgeOneBattValuesMinutes | surface | age_min | high | — | — | — |
| energy | state | CurrentDeratingTemperature | surface | derating_temp_active | high | — | — | — |
| energy | state | DcdcDefect | omit | — | — | — | — | fault/warning/source-state bit — aggregated into the faults[] / source status, not standalone |
| energy | state | DcdcInstalled | surface | dcdc_installed | high | — | — | — |
| energy | state | EmpInstalled | omit | — | — | — | — | EMP feature-availability flag — not a measurement |
| energy | state | EnergyMode | surface | energy_mode | high | gui | — | — |
| energy | state | EnergyModeNotSelectable | surface | energy_mode_locked | high | — | — | — |
| energy | state | IDcdcAfs | surface | dcdc_current | medium | — | — | — |
| energy | state | ILandAfs | surface | shore_current | medium | — | — | — |
| energy | state | IOneBattBemAfs | surface | batt1_current | high | — | — | — |
| energy | state | IPvAfs | surface | solar_current | medium | — | — | — |
| energy | state | ITwoBattBemAfs | surface | batt2_current | medium | gui, vwdoc, live | — | — |
| energy | state | LadInstalled | surface | shore_installed | high | — | — | — |
| energy | state | LandDefect | omit | — | — | — | — | fault/warning/source-state bit — aggregated into the faults[] / source status, not standalone |
| energy | state | LandNotAvailable | omit | — | — | — | — | fault/warning/source-state bit — aggregated into the faults[] / source status, not standalone |
| energy | state | PDcdcAfs | surface | dcdc_power | high | vwdoc | — | — |
| energy | state | PLandAfs | surface | shore_power | high | — | — | — |
| energy | state | PPvAfs | surface | solar_power | high | — | — | — |
| energy | state | PvDefect | omit | — | — | — | — | fault/warning/source-state bit — aggregated into the faults[] / source status, not standalone |
| energy | state | PvInstalled | surface | solar_installed | high | — | — | — |
| energy | state | SleepWarning | surface | sleep_warning | high | — | — | — |
| energy | state | SocOneBattAfs | surface | soc1_level | high | — | — | — |
| energy | state | SocTwoBattAfs | surface | soc2_level | high | — | — | — |
| energy | state | StateDcdcAfs | surface | dcdc_charging | high | — | — | — |
| energy | state | StateLandAfs | omit | — | — | — | — | fault/warning/source-state bit — aggregated into the faults[] / source status, not standalone |
| energy | state | StatePvAfs | omit | — | — | — | — | fault/warning/source-state bit — aggregated into the faults[] / source status, not standalone |
| energy | state | SystemError | omit | — | — | — | — | fault/warning/source-state bit — aggregated into the faults[] / source status, not standalone |
| energy | state | TwoBattNotCharged | omit | — | — | — | — | fault/warning/source-state bit — aggregated into the faults[] / source status, not standalone |
| energy | state | TwoBattSwitchAtCharging | omit | — | — | — | — | fault/warning/source-state bit — aggregated into the faults[] / source status, not standalone |
| energy | state | TwoBattSwitchAtWorkshop | omit | — | — | — | — | fault/warning/source-state bit — aggregated into the faults[] / source status, not standalone |
| energy | state | UOneBattBemAfs | surface | batt1_v | high | vwdoc | — | — |
| energy | state | UTwoBattBemAfs | surface | batt2_v | high | vwdoc | — | — |
| energy | state | WarningLevelActive | omit | — | — | — | — | fault/warning/source-state bit — aggregated into the faults[] / source status, not standalone |
| energy | state | WarningLevelTwo | omit | — | — | — | — | fault/warning/source-state bit — aggregated into the faults[] / source status, not standalone |
| energy | state | tTwoBattRemainingh | surface | batt2_remaining_h | high | — | — | — |
| energy | state | tTwoBattRemainingmin | surface | batt2_remaining_min | high | — | — | — |
| general | state | AmbSwVersion | surface | amb_sw_version | high | — | — | — |
| general | state | CmSwVersion | surface | cm_sw_version | high | — | — | — |
| general | state | CommunicationVersion | surface | comm_version | high | — | — | — |
| generalpurposesignals | state | BitZeroEight | omit | — | — | — | — | opaque general-purpose signal — no GUI widget or getter; meaning undetermined |
| generalpurposesignals | state | BitZeroFive | omit | — | — | — | — | opaque general-purpose signal — no GUI widget or getter; meaning undetermined |
| generalpurposesignals | state | BitZeroFour | omit | — | — | — | — | opaque general-purpose signal — no GUI widget or getter; meaning undetermined |
| generalpurposesignals | state | BitZeroOne | omit | — | — | — | — | opaque general-purpose signal — no GUI widget or getter; meaning undetermined |
| generalpurposesignals | state | BitZeroSeven | omit | — | — | — | — | opaque general-purpose signal — no GUI widget or getter; meaning undetermined |
| generalpurposesignals | state | BitZeroSix | omit | — | — | — | — | opaque general-purpose signal — no GUI widget or getter; meaning undetermined |
| generalpurposesignals | state | BitZeroThree | omit | — | — | — | — | opaque general-purpose signal — no GUI widget or getter; meaning undetermined |
| generalpurposesignals | state | BitZeroTwo | omit | — | — | — | — | opaque general-purpose signal — no GUI widget or getter; meaning undetermined |
| generalpurposesignals | state | ByteZeroEight | omit | — | — | — | — | opaque general-purpose signal — no GUI widget or getter; meaning undetermined |
| generalpurposesignals | state | ByteZeroFive | omit | — | — | — | — | opaque general-purpose signal — no GUI widget or getter; meaning undetermined |
| generalpurposesignals | state | ByteZeroFour | omit | — | — | — | — | opaque general-purpose signal — no GUI widget or getter; meaning undetermined |
| generalpurposesignals | state | ByteZeroOne | omit | — | — | — | — | opaque general-purpose signal — no GUI widget or getter; meaning undetermined |
| generalpurposesignals | state | ByteZeroSeven | omit | — | — | — | — | opaque general-purpose signal — no GUI widget or getter; meaning undetermined |
| generalpurposesignals | state | ByteZeroSix | omit | — | — | — | — | opaque general-purpose signal — no GUI widget or getter; meaning undetermined |
| generalpurposesignals | state | ByteZeroThree | omit | — | — | — | — | opaque general-purpose signal — no GUI widget or getter; meaning undetermined |
| generalpurposesignals | state | ByteZeroTwo | omit | — | — | — | — | opaque general-purpose signal — no GUI widget or getter; meaning undetermined |
| generalpurposesignals | state | DwordZeroOne | omit | — | — | — | — | opaque general-purpose signal — no GUI widget or getter; meaning undetermined |
| generalpurposesignals | state | WordZeroFour | omit | — | — | — | — | opaque general-purpose signal — no GUI widget or getter; meaning undetermined |
| generalpurposesignals | state | WordZeroOne | omit | — | — | — | — | opaque general-purpose signal — no GUI widget or getter; meaning undetermined |
| generalpurposesignals | state | WordZeroThree | omit | — | — | — | — | opaque general-purpose signal — no GUI widget or getter; meaning undetermined |
| generalpurposesignals | state | WordZeroTwo | omit | — | — | — | — | opaque general-purpose signal — no GUI widget or getter; meaning undetermined |
| lighting | control | BrightnessLEight | omit | — | — | — | — | control command field — definition captured in dictionary/overrides; not surfaced because roof-A/C is not installed on this van / set is not wired (control writes work — issue #2 solved 2026-07-07) |
| lighting | control | BrightnessLFive | omit | — | — | — | — | control command field — definition captured in dictionary/overrides; not surfaced because roof-A/C is not installed on this van / set is not wired (control writes work — issue #2 solved 2026-07-07) |
| lighting | control | BrightnessLFour | omit | — | — | — | — | control command field — definition captured in dictionary/overrides; not surfaced because roof-A/C is not installed on this van / set is not wired (control writes work — issue #2 solved 2026-07-07) |
| lighting | control | BrightnessLNine | omit | — | — | — | — | control command field — definition captured in dictionary/overrides; not surfaced because roof-A/C is not installed on this van / set is not wired (control writes work — issue #2 solved 2026-07-07) |
| lighting | control | BrightnessLOne | omit | — | — | — | — | control command field — definition captured in dictionary/overrides; not surfaced because roof-A/C is not installed on this van / set is not wired (control writes work — issue #2 solved 2026-07-07) |
| lighting | control | BrightnessLOneFive | omit | — | — | — | — | control command field — definition captured in dictionary/overrides; not surfaced because roof-A/C is not installed on this van / set is not wired (control writes work — issue #2 solved 2026-07-07) |
| lighting | control | BrightnessLOneFour | omit | — | — | — | — | control command field — definition captured in dictionary/overrides; not surfaced because roof-A/C is not installed on this van / set is not wired (control writes work — issue #2 solved 2026-07-07) |
| lighting | control | BrightnessLOneOne | omit | — | — | — | — | control command field — definition captured in dictionary/overrides; not surfaced because roof-A/C is not installed on this van / set is not wired (control writes work — issue #2 solved 2026-07-07) |
| lighting | control | BrightnessLOneSix | omit | — | — | — | — | control command field — definition captured in dictionary/overrides; not surfaced because roof-A/C is not installed on this van / set is not wired (control writes work — issue #2 solved 2026-07-07) |
| lighting | control | BrightnessLOneThree | omit | — | — | — | — | control command field — definition captured in dictionary/overrides; not surfaced because roof-A/C is not installed on this van / set is not wired (control writes work — issue #2 solved 2026-07-07) |
| lighting | control | BrightnessLOneTwo | omit | — | — | — | — | control command field — definition captured in dictionary/overrides; not surfaced because roof-A/C is not installed on this van / set is not wired (control writes work — issue #2 solved 2026-07-07) |
| lighting | control | BrightnessLOneZero | omit | — | — | — | — | control command field — definition captured in dictionary/overrides; not surfaced because roof-A/C is not installed on this van / set is not wired (control writes work — issue #2 solved 2026-07-07) |
| lighting | control | BrightnessLSeven | omit | — | — | — | — | control command field — definition captured in dictionary/overrides; not surfaced because roof-A/C is not installed on this van / set is not wired (control writes work — issue #2 solved 2026-07-07) |
| lighting | control | BrightnessLSix | omit | — | — | — | — | control command field — definition captured in dictionary/overrides; not surfaced because roof-A/C is not installed on this van / set is not wired (control writes work — issue #2 solved 2026-07-07) |
| lighting | control | BrightnessLThree | omit | — | — | — | — | control command field — definition captured in dictionary/overrides; not surfaced because roof-A/C is not installed on this van / set is not wired (control writes work — issue #2 solved 2026-07-07) |
| lighting | control | BrightnessLTwo | omit | — | — | — | — | control command field — definition captured in dictionary/overrides; not surfaced because roof-A/C is not installed on this van / set is not wired (control writes work — issue #2 solved 2026-07-07) |
| lighting | control | LightValue | omit | — | — | — | — | control command field — definition captured in dictionary/overrides; not surfaced because roof-A/C is not installed on this van / set is not wired (control writes work — issue #2 solved 2026-07-07) |
| lighting | control | Mode | omit | — | — | — | — | control command field — definition captured in dictionary/overrides; not surfaced because roof-A/C is not installed on this van / set is not wired (control writes work — issue #2 solved 2026-07-07) |
| lighting | control | ProfileNumber | omit | — | — | — | — | control command field — definition captured in dictionary/overrides; not surfaced because roof-A/C is not installed on this van / set is not wired (control writes work — issue #2 solved 2026-07-07) |
| lighting | control | Timestamp | omit | — | — | — | — | control command field — definition captured in dictionary/overrides; not surfaced because roof-A/C is not installed on this van / set is not wired (control writes work — issue #2 solved 2026-07-07) |
| lighting | state | BrightnessLEight | surface | brightness_zone_8 | high | — | — | — |
| lighting | state | BrightnessLFive | surface | brightness_zone_5 | high | — | — | — |
| lighting | state | BrightnessLFour | surface | brightness_zone_4 | high | — | — | — |
| lighting | state | BrightnessLNine | surface | brightness_zone_9 | high | — | — | — |
| lighting | state | BrightnessLOne | surface | brightness_zone_1 | high | — | — | — |
| lighting | state | BrightnessLOneFive | surface | brightness_zone_15 | high | — | — | — |
| lighting | state | BrightnessLOneFour | surface | brightness_zone_14 | high | — | — | — |
| lighting | state | BrightnessLOneOne | surface | brightness_zone_11 | high | — | — | — |
| lighting | state | BrightnessLOneSix | surface | brightness_zone_16 | high | — | — | — |
| lighting | state | BrightnessLOneThree | surface | brightness_zone_13 | high | — | — | — |
| lighting | state | BrightnessLOneTwo | surface | brightness_zone_12 | high | — | — | — |
| lighting | state | BrightnessLOneZero | surface | brightness_zone_10 | high | — | — | — |
| lighting | state | BrightnessLSeven | surface | brightness_zone_7 | high | — | — | — |
| lighting | state | BrightnessLSix | surface | brightness_zone_6 | high | — | — | — |
| lighting | state | BrightnessLThree | surface | brightness_zone_3 | high | — | — | — |
| lighting | state | BrightnessLTwo | surface | brightness_zone_2 | high | — | — | — |
| lighting | state | LightValue | surface | any_on | high | — | — | — |
| lighting | state | Mode | surface | mode | high | — | — | — |
| lighting | state | ProfileNumber | surface | profile | high | — | — | — |
| lighting | state | Timestamp | omit | — | — | — | — | frame timestamp, not a user-facing signal |
| livingroomheater | control | Mode | omit | — | — | — | — | control command field — definition captured in dictionary/overrides; not surfaced because roof-A/C is not installed on this van / set is not wired (control writes work — issue #2 solved 2026-07-07) |
| livingroomheater | control | StateAir | omit | — | — | — | — | control command field — definition captured in dictionary/overrides; not surfaced because roof-A/C is not installed on this van / set is not wired (control writes work — issue #2 solved 2026-07-07) |
| livingroomheater | control | StateWater | omit | — | — | — | — | control command field — definition captured in dictionary/overrides; not surfaced because roof-A/C is not installed on this van / set is not wired (control writes work — issue #2 solved 2026-07-07) |
| livingroomheater | control | TemperatureAir | omit | — | — | — | — | control command field — definition captured in dictionary/overrides; not surfaced because roof-A/C is not installed on this van / set is not wired (control writes work — issue #2 solved 2026-07-07) |
| livingroomheater | control | TemperatureWater | omit | — | — | — | — | control command field — definition captured in dictionary/overrides; not surfaced because roof-A/C is not installed on this van / set is not wired (control writes work — issue #2 solved 2026-07-07) |
| livingroomheater | state | Error | surface | error | high | gui | — | — |
| livingroomheater | state | Installed | omit | — | — | — | — | per-function feature-availability flag — surfaced as the 'installed' key |
| livingroomheater | state | Mode | surface | mode | high | — | — | — |
| livingroomheater | state | StateAir | surface | air_on | high | — | — | — |
| livingroomheater | state | StateWater | surface | water_on | high | — | — | — |
| livingroomheater | state | TemperatureAir | surface | air_temp | medium | — | — | — |
| livingroomheater | state | TemperatureWater | surface | water_temp_flag | medium | — | — | — |
| livingroomheater | state | Variant | omit | — | — | — | — | control/telemetry field with no GUI or getter evidence; not surfaced (revisit if needed) |
| roof | control | Down | omit | — | — | — | — | control command field — definition captured in dictionary/overrides; not surfaced because roof-A/C is not installed on this van / set is not wired (control writes work — issue #2 solved 2026-07-07) |
| roof | control | SafetyCounter | omit | — | — | — | — | control command field — definition captured in dictionary/overrides; not surfaced because roof-A/C is not installed on this van / set is not wired (control writes work — issue #2 solved 2026-07-07) |
| roof | control | Up | omit | — | — | — | — | control command field — definition captured in dictionary/overrides; not surfaced because roof-A/C is not installed on this van / set is not wired (control writes work — issue #2 solved 2026-07-07) |
| roof | state | InfoPopUp | omit | — | — | — | — | UI transient popup flag, not telemetry |
| roof | state | Installed | omit | — | — | — | — | per-function feature-availability flag — surfaced as the 'installed' key |
| roof | state | Position | surface | position | high | vwdoc | — | — |
| roof | state | SafetyCounterValid | surface | safety_valid | high | — | — | — |
| roofaircondition | control | FanSpeed | omit | — | — | — | — | control command field — definition captured in dictionary/overrides; not surfaced because roof-A/C is not installed on this van / set is not wired (control writes work — issue #2 solved 2026-07-07) |
| roofaircondition | control | Mode | omit | — | — | — | — | control command field — definition captured in dictionary/overrides; not surfaced because roof-A/C is not installed on this van / set is not wired (control writes work — issue #2 solved 2026-07-07) |
| roofaircondition | control | State | omit | — | — | — | — | control command field — definition captured in dictionary/overrides; not surfaced because roof-A/C is not installed on this van / set is not wired (control writes work — issue #2 solved 2026-07-07) |
| roofaircondition | control | Temperature | omit | — | — | — | — | control command field — definition captured in dictionary/overrides; not surfaced because roof-A/C is not installed on this van / set is not wired (control writes work — issue #2 solved 2026-07-07) |
| roofaircondition | state | Error | surface | error | high | gui | — | — |
| roofaircondition | state | Fanspeed | surface | fan_speed | high | — | — | — |
| roofaircondition | state | Installed | omit | — | — | — | — | per-function feature-availability flag — surfaced as the 'installed' key |
| roofaircondition | state | Mode | surface | mode | high | — | — | — |
| roofaircondition | state | State | surface | on | high | — | — | — |
| roofaircondition | state | Temperature | surface | target_temp | medium | — | — | — |
| satelliteantenna | control | Dish | omit | — | — | — | — | control command field — definition captured in dictionary/overrides; not surfaced because roof-A/C is not installed on this van / set is not wired (control writes work — issue #2 solved 2026-07-07) |
| satelliteantenna | control | DishStop | omit | — | — | — | — | control command field — definition captured in dictionary/overrides; not surfaced because roof-A/C is not installed on this van / set is not wired (control writes work — issue #2 solved 2026-07-07) |
| satelliteantenna | control | SatelliteSelection | omit | — | — | — | — | control command field — definition captured in dictionary/overrides; not surfaced because roof-A/C is not installed on this van / set is not wired (control writes work — issue #2 solved 2026-07-07) |
| satelliteantenna | control | System | omit | — | — | — | — | control command field — definition captured in dictionary/overrides; not surfaced because roof-A/C is not installed on this van / set is not wired (control writes work — issue #2 solved 2026-07-07) |
| satelliteantenna | control | Wlan | omit | — | — | — | — | control command field — definition captured in dictionary/overrides; not surfaced because roof-A/C is not installed on this van / set is not wired (control writes work — issue #2 solved 2026-07-07) |
| satelliteantenna | state | Dish | surface | dish | high | — | — | — |
| satelliteantenna | state | Error | surface | error | high | gui | — | — |
| satelliteantenna | state | Installed | omit | — | — | — | — | per-function feature-availability flag — surfaced as the 'installed' key |
| satelliteantenna | state | SatelliteSelection | surface | satellite | high | — | — | — |
| satelliteantenna | state | SignalLevel | surface | signal_level | high | — | — | — |
| satelliteantenna | state | System | surface | system_on | high | — | — | — |
| stairs | control | Movement | omit | — | — | — | — | control command field — definition captured in dictionary/overrides; not surfaced because roof-A/C is not installed on this van / set is not wired (control writes work — issue #2 solved 2026-07-07) |
| stairs | control | OperationMode | omit | — | — | — | — | control command field — definition captured in dictionary/overrides; not surfaced because roof-A/C is not installed on this van / set is not wired (control writes work — issue #2 solved 2026-07-07) |
| stairs | state | InfoPopUp | omit | — | — | — | — | UI transient popup flag, not telemetry |
| stairs | state | Installed | omit | — | — | — | — | per-function feature-availability flag — surfaced as the 'installed' key |
| stairs | state | OperationMode | surface | mode | high | — | — | — |
| stairs | state | Sensor | surface | obstacle_sensor | high | — | — | — |
| stairs | state | State | surface | extended | high | — | — | — |
| vehicle | state | CarLevelPitch | surface | level_pitch | medium | app | — | — |
| vehicle | state | CarLevelPopUp | surface | level_popup | medium | app | — | — |
| vehicle | state | CarLevelRoll | surface | level_roll | medium | app | — | — |
| vehicle | state | CarTimeDay | omit | — | — | app | — | car-clock component — combined into the car_clock output by semantics.vehicle |
| vehicle | state | CarTimeHour | omit | — | — | app | — | car-clock component — combined into the car_clock output by semantics.vehicle |
| vehicle | state | CarTimeMinute | omit | — | — | app | — | car-clock component — combined into the car_clock output by semantics.vehicle |
| vehicle | state | CarTimeMonth | omit | — | — | app | — | car-clock component (month+1) — combined into the car_clock output by semantics.vehicle |
| vehicle | state | CarTimeSecond | omit | — | — | app | — | car-clock component — combined into the car_clock output by semantics.vehicle |
| vehicle | state | CarTimeYear | omit | — | — | app | — | car-clock component (year+1900) — combined into the car_clock output by semantics.vehicle |
| vehicle | state | CarVariant | surface | car_variant | medium | app | — | — |
| vehicle | state | TerminalOneFive | surface | ignition_on | high | app | — | — |
| water | state | FreshWaterInfoPopUp | omit | — | — | — | — | UI transient popup flag, not telemetry |
| water | state | FreshWaterLevel | surface | fresh_percent | high | vwdoc | — | — |
| water | state | FreshWaterUnit | omit | — | — | — | — | control/telemetry field with no GUI or getter evidence; not surfaced (revisit if needed) |
| water | state | FreshWaterVolume | surface | fresh_capacity_l | high | — | — | — |
| water | state | Installed | omit | — | — | — | — | per-function feature-availability flag — surfaced as the 'installed' key |
| water | state | WasteWaterInfoPopUp | omit | — | — | — | — | UI transient popup flag, not telemetry |
| water | state | WasteWaterLevel | surface | waste_percent | high | vwdoc | — | — |
| water | state | WasteWaterUnit | omit | — | — | — | — | control/telemetry field with no GUI or getter evidence; not surfaced (revisit if needed) |
| water | state | WasteWaterVolume | surface | waste_capacity_l | high | — | — | — |
