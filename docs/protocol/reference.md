# Protocol reference

GENERATED from `protocol/dictionary.yaml`, `calictl/overrides.py` and `protocol/signals.yaml` —
do not hand-edit; see [README.md](README.md) for how to regenerate.

How to read the tables:

- **offset** — the field's first bit (MSB-first). "(override)": the extractor could not place it
  (two functions share one control class in the app) and `calictl/overrides.py` places it from the
  app's frame builder. A bare `MERGED_AMBIGUOUS` would still be unplaced; none is used by calictl.
- **calictl** — what the [signal catalog](signal-matrix.md) decided: `name` (unit) = surfaced
  under that name, with its verified unit if it has one (a value without a unit is a raw level or
  enum — verified as such, see `docs/business-logic/signal-scales.md`); "omitted" = not surfaced
  (the reason is in `protocol/signals.yaml`). Control fields are written, never surfaced: their
  column is "—" (the commands that write them: `calictl/control.py`).
- **meaning** — the value meaning the decompile pins down, where the extractor recorded one.

## airheater

- **State char UUID(s):** 00001700-6c77-4b7d-bbf6-a5e587701f3d, 00001702-6c77-4b7d-bbf6-a5e587701f3d
- **Control source:** sf/a.java

### State fields

| field | offset | width | default | raw_range | calictl | meaning |
|---|---|---|---|---|---|---|
| AirDistribution | 0 | 2 | — | — | `air_distribution` | — |
| Installed | 3 | 1 | — | — | omitted | — |
| FaultTriggerBit | 4 | 1 | — | — | omitted | — |
| PermanentOperation | 5 | 1 | — | — | `permanent` | — |
| NormalOperation | 6 | 1 | — | — | `running` | — |
| PermanentOperationRequestConfirmation | 7 | 1 | — | — | omitted | — |
| HeatingLevel | 8 | 4 | — | — | `level` | — |
| ErrorCode | 12 | 4 | — | — | `error_code` | — |
| OperationModeCombined | 16 | 4 | — | — | omitted | — |
| OperationModeAirHeater | 20 | 4 | — | — | `mode` | — |
| RunningTime | 24 | 8 | — | — | `running_time` (min) | — |
| TimerHour | 32 | 8 | — | — | omitted | — |
| TimerMin | 40 | 8 | — | — | omitted | — |
| RunningTimeinAction | 48 | 8 | — | — | omitted | — |

### Control fields

| field | offset | width | default | raw_range | calictl | meaning |
|---|---|---|---|---|---|---|
| NormalOperationRequest | 6 | 2 | 3 | 0..3 | — | — |
| PermanentOperationConfirmation | 4 | 2 | 3 | 0..3 | — | — |
| PermanentOperationRequest | 2 | 2 | 3 | 0..3 | — | — |
| AirDistribution | 0 | 2 | 0 | 0..3 | — | — |
| HeatingLevel | 12 | 4 | 11 | 0..15 | — | — |
| OperationModeAirHeater | 8 | 4 | 7 | 0..15 | — | — |
| OperationModeCombined | 20 (override) | 4 | 0 | 0..15 | — | — |
| RunningTime | 24 (override) | 8 | 127 | 0..255 | — | — |
| TimerHour | 32 (override) | 8 | 31 | 0..255 | — | — |
| TimerMin | 40 (override) | 8 | 63 | 0..255 | — | — |

## campingmode

- **State char UUID(s):** 00001200-6c77-4b7d-bbf6-a5e587701f3d, 00001202-6c77-4b7d-bbf6-a5e587701f3d
- **Control source:** lg/a.java

### State fields

| field | offset | width | default | raw_range | calictl | meaning |
|---|---|---|---|---|---|---|
| Installed | 2 | 1 | — | — | omitted | — |
| Enable | 3 | 1 | — | — | `enable` | — |
| InteriorLight | 4 | 1 | — | — | `lights_on` | — |
| OutsideLight | 5 | 1 | — | — | omitted | — |
| UsbCharger | 6 | 1 | — | — | `usb_charger` | — |
| State | 7 | 1 | — | — | `master_on` | — |

### Control fields

| field | offset | width | default | raw_range | calictl | meaning |
|---|---|---|---|---|---|---|
| State | 6 | 2 | 3 | 0..3 | — | — |
| UsbCharger | 4 (override) | 2 | 3 | 0..3 | — | — |
| OutsideLight | 2 (override) | 2 | 3 | 0..3 | — | — |
| InteriorLight | 0 (override) | 2 | 3 | 0..3 | — | — |

## cooler

- **State char UUID(s):** 00001100-6c77-4b7d-bbf6-a5e587701f3d, 00001102-6c77-4b7d-bbf6-a5e587701f3d
- **Control source:** sf/a.java

### State fields

| field | offset | width | default | raw_range | calictl | meaning |
|---|---|---|---|---|---|---|
| Error | 0 | 2 | — | — | `error` | — |
| NightTimerSet | 3 | 1 | — | — | omitted | — |
| Installed | 4 | 1 | — | — | omitted | — |
| TimerElapsed | 5 | 1 | — | — | omitted | — |
| TimerState | 6 | 1 | — | — | `timer_active` | — |
| State | 7 | 1 | — | — | `on` | — |
| Mode | 8 | 4 | — | — | `mode` | — |
| Level | 12 | 4 | — | — | `level` | — |
| TimerHourSet | 16 | 8 | — | — | omitted | — |
| TimerMinSet | 24 | 8 | — | — | omitted | — |
| TimerCounterHour | 32 | 8 | — | — | omitted | — |
| TimerCounterMin | 40 | 8 | — | — | omitted | — |
| NightTimerHourOn | 48 | 8 | — | — | omitted | — |
| NightTimerHourOff | 56 | 8 | — | — | omitted | — |

### Control fields

| field | offset | width | default | raw_range | calictl | meaning |
|---|---|---|---|---|---|---|
| State | 6 | 2 | 3 | 0..3 | — | — |
| TimerStart | 4 | 2 | 3 | 0..3 | — | — |
| TimerCancel | 2 | 2 | 3 | 0..3 | — | — |
| NightTimerSet | 0 | 2 | 3 | 0..3 | — | — |
| Level | 12 | 4 | 7 | 0..15 | — | — |
| Mode | 8 | 4 | 7 | 0..15 | — | — |
| TimerHour | 16 (override) | 8 | 30 | 0..255 | — | — |
| TimerMin | 24 (override) | 8 | 62 | 0..255 | — | — |
| NightTimerHourOn | 32 (override) | 8 | 31 | 0..255 | — | — |
| NightTimerHourOff | 40 (override) | 8 | 31 | 0..255 | — | — |

## energy

- **State char UUID(s):** 00001600-6c77-4b7d-bbf6-a5e587701f3d, 00001602-6c77-4b7d-bbf6-a5e587701f3d
- **Control source:** pg/a.java

### State fields

| field | offset | width | default | raw_range | calictl | meaning |
|---|---|---|---|---|---|---|
| LandNotAvailable | 0 | 1 | — | — | omitted | — |
| LandDefect | 1 | 1 | — | — | omitted | — |
| EnergyModeNotSelectable | 2 | 1 | — | — | `energy_mode_locked` | — |
| DcdcDefect | 3 | 1 | — | — | omitted | — |
| WarningLevelActive | 4 | 1 | — | — | omitted | — |
| TwoBattSwitchAtWorkshop | 5 | 1 | — | — | omitted | — |
| TwoBattSwitchAtCharging | 6 | 1 | — | — | omitted | — |
| TwoBattNotCharged | 7 | 1 | — | — | omitted | — |
| DcdcInstalled | 8 | 1 | — | — | `dcdc_installed` | — |
| LadInstalled | 9 | 1 | — | — | `shore_installed` | — |
| PvInstalled | 10 | 1 | — | — | `solar_installed` | — |
| EmpInstalled | 11 | 1 | — | — | omitted | — |
| SystemError | 12 | 1 | — | — | omitted | — |
| CurrentDeratingTemperature | 13 | 1 | — | — | `derating_temp_active` | — |
| SleepWarning | 14 | 1 | — | — | `sleep_warning` | — |
| PvDefect | 15 | 1 | — | — | omitted | — |
| SocOneBattAfs | 16 | 4 | — | — | `soc1_level` | — |
| EnergyMode | 20 | 2 | — | — | `energy_mode` | — |
| WarningLevelTwo | 22 | 2 | — | — | omitted | — |
| StateDcdcAfs | 24 | 4 | — | — | `dcdc_charging` | — |
| SocTwoBattAfs | 28 | 4 | — | — | `soc2_level` | — |
| StatePvAfs | 32 | 4 | — | — | omitted | — |
| StateLandAfs | 36 | 4 | — | — | omitted | — |
| AgeOneBattValuesMinutes | 40 | 8 | — | — | `age_min` (min) | — |
| IOneBattBemAfs | 48 | 8 | — | — | `batt1_current` (A) | — |
| PDcdcAfs | 56 | 8 | — | — | `dcdc_power` (W) | — |
| PLandAfs | 64 | 8 | — | — | `shore_power` (W) | — |
| PPvAfs | 72 | 8 | — | — | `solar_power` (W) | — |
| tTwoBattRemainingh | 80 | 8 | — | — | `batt2_remaining_h` (h) | — |
| tTwoBattRemainingmin | 88 | 8 | — | — | `batt2_remaining_min` (min) | — |
| UOneBattBemAfs | 96 | 8 | — | — | `batt1_v` (V) | — |
| UTwoBattBemAfs | 104 | 8 | — | — | `batt2_v` (V) | — |
| ITwoBattBemAfs | 112 | 16 | — | — | `batt2_current` (A) | — |
| IDcdcAfs | 128 | 16 | — | — | `dcdc_current` (A) | — |
| ILandAfs | 144 | 16 | — | — | `shore_current` (A) | — |
| IPvAfs | 160 | 16 | — | — | `solar_current` (A) | — |

### Control fields

| field | offset | width | default | raw_range | calictl | meaning |
|---|---|---|---|---|---|---|
| EnergyModeSet | 2 | 2 | 3 | 0..3 | — | 0=normal 1=max_charge 2=eco (enum bf/c.java, same as read EnergyMode), 3=leave-unchanged |
| DisplayRefresh | 7 | 1 | 0 | — | — | app mode setter (xf/d.java:389) leaves this 0; purpose unverified |

## general

- **State char UUID(s):** 00001000-6c77-4b7d-bbf6-a5e587701f3d, 00001001-6c77-4b7d-bbf6-a5e587701f3d
- **Control source:** —

### State fields

| field | offset | width | default | raw_range | calictl | meaning |
|---|---|---|---|---|---|---|
| AmbSwVersion | 0 | 32 | — | — | `amb_sw_version` | — |
| CmSwVersion | 32 | 32 | — | — | `cm_sw_version` | — |
| CommunicationVersion | 64 | 8 | — | — | `comm_version` | — |

### Control fields

_(none)_

## generalpurposesignals

- **State char UUID(s):** 0000f000-6c77-4b7d-bbf6-a5e587701f3d, 0000f001-6c77-4b7d-bbf6-a5e587701f3d
- **Control source:** —

### State fields

| field | offset | width | default | raw_range | calictl | meaning |
|---|---|---|---|---|---|---|
| BitZeroEight | 0 | 1 | — | — | omitted | — |
| BitZeroSeven | 1 | 1 | — | — | omitted | — |
| BitZeroSix | 2 | 1 | — | — | omitted | — |
| BitZeroFive | 3 | 1 | — | — | omitted | — |
| BitZeroFour | 4 | 1 | — | — | omitted | — |
| BitZeroThree | 5 | 1 | — | — | omitted | — |
| BitZeroTwo | 6 | 1 | — | — | omitted | — |
| BitZeroOne | 7 | 1 | — | — | omitted | — |
| ByteZeroOne | 8 | 8 | — | — | omitted | — |
| ByteZeroTwo | 16 | 8 | — | — | omitted | — |
| ByteZeroThree | 24 | 8 | — | — | omitted | — |
| ByteZeroFour | 32 | 8 | — | — | omitted | — |
| ByteZeroFive | 40 | 8 | — | — | omitted | — |
| ByteZeroSix | 48 | 8 | — | — | omitted | — |
| ByteZeroSeven | 56 | 8 | — | — | omitted | — |
| ByteZeroEight | 64 | 8 | — | — | omitted | — |
| WordZeroOne | 72 | 16 | — | — | omitted | — |
| WordZeroTwo | 88 | 16 | — | — | omitted | — |
| WordZeroThree | 104 | 16 | — | — | omitted | — |
| WordZeroFour | 120 | 16 | — | — | omitted | — |
| DwordZeroOne | 136 | 32 | — | — | omitted | — |

### Control fields

_(none)_

## lighting

- **State char UUID(s):** 00001500-6c77-4b7d-bbf6-a5e587701f3d, 00001502-6c77-4b7d-bbf6-a5e587701f3d
- **Control source:** eg/a.java

### State fields

| field | offset | width | default | raw_range | calictl | meaning |
|---|---|---|---|---|---|---|
| ProfileNumber | 4 | 4 | — | — | `profile` | — |
| Mode | 8 | 8 | — | — | `mode` | — |
| Timestamp | 16 | 32 | — | — | `wakeup` | — |
| LightValue | 48 | 16 | — | — | `any_on` | — |
| BrightnessLTwo | 64 | 4 | — | — | `brightness_zone_2` | — |
| BrightnessLOne | 68 | 4 | — | — | `brightness_zone_1` | — |
| BrightnessLFour | 72 | 4 | — | — | `brightness_zone_4` | — |
| BrightnessLThree | 76 | 4 | — | — | `brightness_zone_3` | — |
| BrightnessLSix | 80 | 4 | — | — | `brightness_zone_6` | — |
| BrightnessLFive | 84 | 4 | — | — | `brightness_zone_5` | — |
| BrightnessLEight | 88 | 4 | — | — | `brightness_zone_8` | — |
| BrightnessLSeven | 92 | 4 | — | — | `brightness_zone_7` | — |
| BrightnessLOneZero | 96 | 4 | — | — | `brightness_zone_10` | — |
| BrightnessLNine | 100 | 4 | — | — | `brightness_zone_9` | — |
| BrightnessLOneTwo | 104 | 4 | — | — | `brightness_zone_12` | — |
| BrightnessLOneOne | 108 | 4 | — | — | `brightness_zone_11` | — |
| BrightnessLOneFour | 112 | 4 | — | — | `brightness_zone_14` | — |
| BrightnessLOneThree | 116 | 4 | — | — | `brightness_zone_13` | — |
| BrightnessLOneSix | 120 | 4 | — | — | `brightness_zone_16` | — |
| BrightnessLOneFive | 124 | 4 | — | — | `brightness_zone_15` | — |

### Control fields

| field | offset | width | default | raw_range | calictl | meaning |
|---|---|---|---|---|---|---|
| ProfileNumber | 4 | 4 | 14 | 0..15 | — | — |
| Mode | 8 | 8 | 0 | 0..255 | — | — |
| Timestamp | 16 | 32 | 0 | — | — | epoch/monotonic stamp, offset read from dg/h.java builder (decompile-derived) |
| LightValue | 48 | 16 | 0 | 0..65535 | — | — |
| BrightnessLOne | 68 | 4 | 14 | 0..15 | — | — |
| BrightnessLTwo | 64 | 4 | 14 | 0..15 | — | — |
| BrightnessLThree | 76 | 4 | 14 | 0..15 | — | — |
| BrightnessLFour | 72 | 4 | 14 | 0..15 | — | — |
| BrightnessLFive | 84 | 4 | 14 | 0..15 | — | — |
| BrightnessLSix | 80 | 4 | 14 | 0..15 | — | — |
| BrightnessLSeven | 92 | 4 | 14 | 0..15 | — | — |
| BrightnessLEight | 88 | 4 | 14 | 0..15 | — | — |
| BrightnessLNine | 100 | 4 | 14 | 0..15 | — | — |
| BrightnessLOneZero | 96 | 4 | 14 | 0..15 | — | — |
| BrightnessLOneOne | 108 | 4 | 14 | 0..15 | — | — |
| BrightnessLOneTwo | 104 | 4 | 14 | 0..15 | — | — |
| BrightnessLOneThree | 116 | 4 | 14 | 0..15 | — | — |
| BrightnessLOneFour | 112 | 4 | 14 | 0..15 | — | — |
| BrightnessLOneFive | 124 | 4 | 14 | 0..15 | — | — |
| BrightnessLOneSix | 120 | 4 | 14 | 0..15 | — | — |

## livingroomheater

- **State char UUID(s):** 00002100-6c77-4b7d-bbf6-a5e587701f3d, 00002102-6c77-4b7d-bbf6-a5e587701f3d
- **Control source:** gg/a.java

### State fields

| field | offset | width | default | raw_range | calictl | meaning |
|---|---|---|---|---|---|---|
| Variant | 0 | 2 | — | — | omitted | — |
| Installed | 4 | 1 | — | — | omitted | — |
| TemperatureWater | 5 | 1 | — | — | `water_temp_flag` | — |
| StateWater | 6 | 1 | — | — | `water_on` | — |
| StateAir | 7 | 1 | — | — | `air_on` | — |
| Error | 8 | 4 | — | — | `error` | — |
| Mode | 12 | 4 | — | — | `mode` | — |
| TemperatureAir | 16 | 8 | — | — | `air_temp` | — |

### Control fields

| field | offset | width | default | raw_range | calictl | meaning |
|---|---|---|---|---|---|---|
| StateAir | 6 (override) | 2 | 3 | 0..3 | — | — |
| StateWater | 4 (override) | 2 | 3 | 0..3 | — | — |
| TemperatureWater | 2 (override) | 2 | 3 | 0..3 | — | — |
| Mode | 12 | 4 | 5 | 0..15 | — | — |
| TemperatureAir | 16 (override) | 8 | 0 | 0..255 | — | — |

## roof

- **State char UUID(s):** 00001400-6c77-4b7d-bbf6-a5e587701f3d, 00001402-6c77-4b7d-bbf6-a5e587701f3d
- **Control source:** jg/a.java

### State fields

| field | offset | width | default | raw_range | calictl | meaning |
|---|---|---|---|---|---|---|
| Position | 0 | 4 | — | — | `position` | — |
| Installed | 6 | 1 | — | — | omitted | — |
| SafetyCounterValid | 7 | 1 | — | — | `safety_valid` | — |
| InfoPopUp | 12 | 4 | — | — | omitted | — |

### Control fields

| field | offset | width | default | raw_range | calictl | meaning |
|---|---|---|---|---|---|---|
| Up | 6 | 2 | 3 | 0..3 | — | Up=1 => OPEN command (byte 0x01); app-verified polarity ig/c.java n2()/e1(), PHYSICAL actuation NOT-LIVE-VERIFIED |
| Down | 4 | 2 | 3 | 0..3 | — | Down=1 => CLOSE command (byte 0x04); stop=Up0/Down0 (0x00). App-verified, PHYSICAL NOT-LIVE-VERIFIED |
| SafetyCounter | 8 | 32 | 0 | — | — | app-generated monotonic BE-uint32, ~+1/500ms (NOT unit-echoed); see overrides.py + device.actuate_roof |

## roofaircondition

- **State char UUID(s):** 00002000-6c77-4b7d-bbf6-a5e587701f3d, 00002002-6c77-4b7d-bbf6-a5e587701f3d
- **Control source:** lg/a.java

### State fields

| field | offset | width | default | raw_range | calictl | meaning |
|---|---|---|---|---|---|---|
| Error | 0 | 2 | — | — | `error` | — |
| Mode | 2 | 2 | — | — | `mode` | — |
| Installed | 6 | 1 | — | — | omitted | — |
| State | 7 | 1 | — | — | `on` | — |
| Fanspeed | 12 | 4 | — | — | `fan_speed` | — |
| Temperature | 16 | 8 | — | — | `target_temp` | — |

### Control fields

| field | offset | width | default | raw_range | calictl | meaning |
|---|---|---|---|---|---|---|
| State | 6 | 2 | 3 | 0..3 | — | — |
| FanSpeed | 12 (override) | 4 | 7 | 0..15 | — | — |
| Mode | 8 (override) | 4 | 7 | 0..15 | — | — |
| Temperature | 16 (override) | 8 | 0 | 0..255 | — | — |

## satelliteantenna

- **State char UUID(s):** 00001900-6c77-4b7d-bbf6-a5e587701f3d, 00001902-6c77-4b7d-bbf6-a5e587701f3d, 00001903-6c77-4b7d-bbf6-a5e587701f3d, 00001904-6c77-4b7d-bbf6-a5e587701f3d, 00001905-6c77-4b7d-bbf6-a5e587701f3d
- **Control source:** gg/a.java

### State fields

| field | offset | width | default | raw_range | calictl | meaning |
|---|---|---|---|---|---|---|
| Error | 0 | 4 | — | — | `error` | — |
| System | 4 | 2 | — | — | `system_on` | — |
| Installed | 7 | 1 | — | — | omitted | — |
| SatelliteSelection | 8 | 4 | — | — | `satellite` | — |
| Dish | 12 | 4 | — | — | `dish` | — |
| SignalLevel | 16 | 8 | — | — | `signal_level` | — |

### Control fields

| field | offset | width | default | raw_range | calictl | meaning |
|---|---|---|---|---|---|---|
| DishStop | 7 (override) | 1 | — | — | — | — |
| Wlan | 6 (override) | 1 | — | — | — | — |
| System | 2 (override) | 2 | 3 | 0..3 | — | — |
| Dish | 0 (override) | 2 | 0 | 0..3 | — | — |
| SatelliteSelection | 12 | 4 | 0 | 0..15 | — | — |

## stairs

- **State char UUID(s):** 00001800-6c77-4b7d-bbf6-a5e587701f3d, 00001802-6c77-4b7d-bbf6-a5e587701f3d
- **Control source:** pg/a.java

### State fields

| field | offset | width | default | raw_range | calictl | meaning |
|---|---|---|---|---|---|---|
| InfoPopUp | 2 | 2 | — | — | omitted | — |
| Sensor | 4 | 1 | — | — | `obstacle_sensor` | — |
| State | 5 | 1 | — | — | `extended` | — |
| OperationMode | 6 | 1 | — | — | `mode` | — |
| Installed | 7 | 1 | — | — | omitted | — |

### Control fields

| field | offset | width | default | raw_range | calictl | meaning |
|---|---|---|---|---|---|---|
| OperationMode | 6 (override) | 2 | 3 | 0..3 | — | — |
| Movement | 4 (override) | 2 | 0 | 0..3 | — | — |

## vehicle

- **State char UUID(s):** 00001000-6c77-4b7d-bbf6-a5e587701f3d, 00001004-6c77-4b7d-bbf6-a5e587701f3d
- **Control source:** —

### State fields

| field | offset | width | default | raw_range | calictl | meaning |
|---|---|---|---|---|---|---|
| CarVariant | 0 | 4 | — | — | `car_variant` | 0 California T7, 1 Grand California, 2 California Next (app 5.4.0 qg/e.java:357-363; wire values, not the enum ordinals) |
| CarLevelPopUp | 4 | 2 | — | — | `level_popup` | — |
| TerminalOneFive | 7 | 1 | — | — | `ignition_on` | — |
| CarTimeYear | 8 | 8 | — | — | omitted | — |
| CarTimeMonth | 16 | 8 | — | — | omitted | — |
| CarTimeDay | 24 | 8 | — | — | omitted | — |
| CarTimeHour | 32 | 8 | — | — | omitted | — |
| CarTimeMinute | 40 | 8 | — | — | omitted | — |
| CarTimeSecond | 48 | 8 | — | — | omitted | — |
| CarLevelRoll | 56 | 16 | — | — | `level_roll` (°) | — |
| CarLevelPitch | 72 | 16 | — | — | `level_pitch` (°) | — |

### Control fields

_(none)_

## water

- **State char UUID(s):** 00001300-6c77-4b7d-bbf6-a5e587701f3d, 00001302-6c77-4b7d-bbf6-a5e587701f3d
- **Control source:** —

### State fields

| field | offset | width | default | raw_range | calictl | meaning |
|---|---|---|---|---|---|---|
| FreshWaterInfoPopUp | 0 | 4 | — | — | `fresh_alert` | — |
| Installed | 6 | 1 | — | — | omitted | — |
| FreshWaterUnit | 7 | 1 | — | — | omitted | — |
| FreshWaterLevel | 8 | 8 | — | — | `fresh_percent` (%) | — |
| FreshWaterVolume | 16 | 8 | — | — | `fresh_capacity_l` (L) | — |
| WasteWaterInfoPopUp | 26 | 2 | — | — | `waste_alert` | — |
| WasteWaterUnit | 31 | 1 | — | — | omitted | — |
| WasteWaterLevel | 32 | 8 | — | — | `waste_percent` (%) | — |
| WasteWaterVolume | 40 | 8 | — | — | `waste_capacity_l` (L) | — |

### Control fields

_(none)_
