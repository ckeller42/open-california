# Protocol reference

GENERATED from `protocol/dictionary.yaml` — do not hand-edit; see [README.md](README.md) for how to regenerate.

## airheater

- **State char UUID(s):** 00001700-6c77-4b7d-bbf6-a5e587701f3d, 00001702-6c77-4b7d-bbf6-a5e587701f3d
- **Control source:** sf/a.java

### State fields

| field | offset | width | default | raw_range | value_semantics |
|---|---|---|---|---|---|
| AirDistribution | 0 | 2 | — | — | — |
| Installed | 3 | 1 | — | — | — |
| FaultTriggerBit | 4 | 1 | — | — | — |
| PermanentOperation | 5 | 1 | — | — | — |
| NormalOperation | 6 | 1 | — | — | — |
| PermanentOperationRequestConfirmation | 7 | 1 | — | — | — |
| HeatingLevel | 8 | 4 | — | — | — |
| ErrorCode | 12 | 4 | — | — | — |
| OperationModeCombined | 16 | 4 | — | — | — |
| OperationModeAirHeater | 20 | 4 | — | — | — |
| RunningTime | 24 | 8 | — | — | — |
| TimerHour | 32 | 8 | — | — | — |
| TimerMin | 40 | 8 | — | — | — |
| RunningTimeinAction | 48 | 8 | — | — | — |

### Control fields

| field | offset | width | default | raw_range | value_semantics |
|---|---|---|---|---|---|
| NormalOperationRequest | 6 | 2 | 3 | 0..3 | UNVERIFIED |
| PermanentOperationConfirmation | 4 | 2 | 3 | 0..3 | UNVERIFIED |
| PermanentOperationRequest | 2 | 2 | 3 | 0..3 | UNVERIFIED |
| AirDistribution | 0 | 2 | 0 | 0..3 | UNVERIFIED |
| HeatingLevel | 12 | 4 | 11 | 0..15 | UNVERIFIED |
| OperationModeAirHeater | 8 | 4 | 7 | 0..15 | UNVERIFIED |
| OperationModeCombined | **MERGED_AMBIGUOUS** | 4 | 0 | 0..15 | UNVERIFIED |
| RunningTime | **MERGED_AMBIGUOUS** | 8 | 127 | 0..255 | UNVERIFIED |
| TimerHour | **MERGED_AMBIGUOUS** | 8 | 31 | 0..255 | UNVERIFIED |
| TimerMin | **MERGED_AMBIGUOUS** | 8 | 63 | 0..255 | UNVERIFIED |

## campingmode

- **State char UUID(s):** 00001200-6c77-4b7d-bbf6-a5e587701f3d, 00001202-6c77-4b7d-bbf6-a5e587701f3d
- **Control source:** lg/a.java

### State fields

| field | offset | width | default | raw_range | value_semantics |
|---|---|---|---|---|---|
| Installed | 2 | 1 | — | — | — |
| Enable | 3 | 1 | — | — | — |
| InteriorLight | 4 | 1 | — | — | — |
| OutsideLight | 5 | 1 | — | — | — |
| UsbCharger | 6 | 1 | — | — | — |
| State | 7 | 1 | — | — | — |

### Control fields

| field | offset | width | default | raw_range | value_semantics |
|---|---|---|---|---|---|
| State | 6 | 2 | 3 | 0..3 | UNVERIFIED |
| UsbCharger | **MERGED_AMBIGUOUS** | 2 | 3 | 0..3 | UNVERIFIED |
| OutsideLight | **MERGED_AMBIGUOUS** | 2 | 3 | 0..3 | UNVERIFIED |
| InteriorLight | **MERGED_AMBIGUOUS** | 2 | 3 | 0..3 | UNVERIFIED |

## cooler

- **State char UUID(s):** 00001100-6c77-4b7d-bbf6-a5e587701f3d, 00001102-6c77-4b7d-bbf6-a5e587701f3d
- **Control source:** sf/a.java

### State fields

| field | offset | width | default | raw_range | value_semantics |
|---|---|---|---|---|---|
| Error | 0 | 2 | — | — | — |
| NightTimerSet | 3 | 1 | — | — | — |
| Installed | 4 | 1 | — | — | — |
| TimerElapsed | 5 | 1 | — | — | — |
| TimerState | 6 | 1 | — | — | — |
| State | 7 | 1 | — | — | — |
| Mode | 8 | 4 | — | — | — |
| Level | 12 | 4 | — | — | — |
| TimerHourSet | 16 | 8 | — | — | — |
| TimerMinSet | 24 | 8 | — | — | — |
| TimerCounterHour | 32 | 8 | — | — | — |
| TimerCounterMin | 40 | 8 | — | — | — |
| NightTimerHourOn | 48 | 8 | — | — | — |
| NightTimerHourOff | 56 | 8 | — | — | — |

### Control fields

| field | offset | width | default | raw_range | value_semantics |
|---|---|---|---|---|---|
| State | 6 | 2 | 3 | 0..3 | UNVERIFIED |
| TimerStart | 4 | 2 | 3 | 0..3 | UNVERIFIED |
| TimerCancel | 2 | 2 | 3 | 0..3 | UNVERIFIED |
| NightTimerSet | 0 | 2 | 3 | 0..3 | UNVERIFIED |
| Level | 12 | 4 | 7 | 0..15 | UNVERIFIED |
| Mode | 8 | 4 | 7 | 0..15 | UNVERIFIED |
| TimerHour | **MERGED_AMBIGUOUS** | 8 | 30 | 0..255 | UNVERIFIED |
| TimerMin | **MERGED_AMBIGUOUS** | 8 | 62 | 0..255 | UNVERIFIED |
| NightTimerHourOn | **MERGED_AMBIGUOUS** | 8 | 31 | 0..255 | UNVERIFIED |
| NightTimerHourOff | **MERGED_AMBIGUOUS** | 8 | 31 | 0..255 | UNVERIFIED |

## energy

- **State char UUID(s):** 00001600-6c77-4b7d-bbf6-a5e587701f3d, 00001602-6c77-4b7d-bbf6-a5e587701f3d
- **Control source:** pg/a.java

### State fields

| field | offset | width | default | raw_range | value_semantics |
|---|---|---|---|---|---|
| LandNotAvailable | 0 | 1 | — | — | — |
| LandDefect | 1 | 1 | — | — | — |
| EnergyModeNotSelectable | 2 | 1 | — | — | — |
| DcdcDefect | 3 | 1 | — | — | — |
| WarningLevelActive | 4 | 1 | — | — | — |
| TwoBattSwitchAtWorkshop | 5 | 1 | — | — | — |
| TwoBattSwitchAtCharging | 6 | 1 | — | — | — |
| TwoBattNotCharged | 7 | 1 | — | — | — |
| DcdcInstalled | 8 | 1 | — | — | — |
| LadInstalled | 9 | 1 | — | — | — |
| PvInstalled | 10 | 1 | — | — | — |
| EmpInstalled | 11 | 1 | — | — | — |
| SystemError | 12 | 1 | — | — | — |
| CurrentDeratingTemperature | 13 | 1 | — | — | — |
| SleepWarning | 14 | 1 | — | — | — |
| PvDefect | 15 | 1 | — | — | — |
| SocOneBattAfs | 16 | 4 | — | — | — |
| EnergyMode | 20 | 2 | — | — | — |
| WarningLevelTwo | 22 | 2 | — | — | — |
| StateDcdcAfs | 24 | 4 | — | — | — |
| SocTwoBattAfs | 28 | 4 | — | — | — |
| StatePvAfs | 32 | 4 | — | — | — |
| StateLandAfs | 36 | 4 | — | — | — |
| AgeOneBattValuesMinutes | 40 | 8 | — | — | — |
| IOneBattBemAfs | 48 | 8 | — | — | — |
| PDcdcAfs | 56 | 8 | — | — | — |
| PLandAfs | 64 | 8 | — | — | — |
| PPvAfs | 72 | 8 | — | — | — |
| tTwoBattRemainingh | 80 | 8 | — | — | — |
| tTwoBattRemainingmin | 88 | 8 | — | — | — |
| UOneBattBemAfs | 96 | 8 | — | — | — |
| UTwoBattBemAfs | 104 | 8 | — | — | — |
| ITwoBattBemAfs | 112 | 16 | — | — | — |
| IDcdcAfs | 128 | 16 | — | — | — |
| ILandAfs | 144 | 16 | — | — | — |
| IPvAfs | 160 | 16 | — | — | — |

### Control fields

| field | offset | width | default | raw_range | value_semantics |
|---|---|---|---|---|---|
| EnergyModeSet | 2 | 2 | 3 | 0..3 | 0=normal 1=max_charge 2=eco (enum bf/c.java, same as read EnergyMode), 3=leave-unchanged |
| DisplayRefresh | 7 | 1 | 0 | — | app mode setter (xf/d.java:389) leaves this 0; purpose unverified |

## general

- **State char UUID(s):** 00001000-6c77-4b7d-bbf6-a5e587701f3d, 00001001-6c77-4b7d-bbf6-a5e587701f3d
- **Control source:** —

### State fields

| field | offset | width | default | raw_range | value_semantics |
|---|---|---|---|---|---|
| AmbSwVersion | 0 | 32 | — | — | — |
| CmSwVersion | 32 | 32 | — | — | — |
| CommunicationVersion | 64 | 8 | — | — | — |

### Control fields

_(none)_

## generalpurposesignals

- **State char UUID(s):** 0000f000-6c77-4b7d-bbf6-a5e587701f3d, 0000f001-6c77-4b7d-bbf6-a5e587701f3d
- **Control source:** —

### State fields

| field | offset | width | default | raw_range | value_semantics |
|---|---|---|---|---|---|
| BitZeroEight | 0 | 1 | — | — | — |
| BitZeroSeven | 1 | 1 | — | — | — |
| BitZeroSix | 2 | 1 | — | — | — |
| BitZeroFive | 3 | 1 | — | — | — |
| BitZeroFour | 4 | 1 | — | — | — |
| BitZeroThree | 5 | 1 | — | — | — |
| BitZeroTwo | 6 | 1 | — | — | — |
| BitZeroOne | 7 | 1 | — | — | — |
| ByteZeroOne | 8 | 8 | — | — | — |
| ByteZeroTwo | 16 | 8 | — | — | — |
| ByteZeroThree | 24 | 8 | — | — | — |
| ByteZeroFour | 32 | 8 | — | — | — |
| ByteZeroFive | 40 | 8 | — | — | — |
| ByteZeroSix | 48 | 8 | — | — | — |
| ByteZeroSeven | 56 | 8 | — | — | — |
| ByteZeroEight | 64 | 8 | — | — | — |
| WordZeroOne | 72 | 16 | — | — | — |
| WordZeroTwo | 88 | 16 | — | — | — |
| WordZeroThree | 104 | 16 | — | — | — |
| WordZeroFour | 120 | 16 | — | — | — |
| DwordZeroOne | 136 | 32 | — | — | — |

### Control fields

_(none)_

## lighting

- **State char UUID(s):** 00001500-6c77-4b7d-bbf6-a5e587701f3d, 00001502-6c77-4b7d-bbf6-a5e587701f3d
- **Control source:** eg/a.java

### State fields

| field | offset | width | default | raw_range | value_semantics |
|---|---|---|---|---|---|
| ProfileNumber | 4 | 4 | — | — | — |
| Mode | 8 | 8 | — | — | — |
| Timestamp | 16 | 32 | — | — | — |
| LightValue | 48 | 16 | — | — | — |
| BrightnessLTwo | 64 | 4 | — | — | — |
| BrightnessLOne | 68 | 4 | — | — | — |
| BrightnessLFour | 72 | 4 | — | — | — |
| BrightnessLThree | 76 | 4 | — | — | — |
| BrightnessLSix | 80 | 4 | — | — | — |
| BrightnessLFive | 84 | 4 | — | — | — |
| BrightnessLEight | 88 | 4 | — | — | — |
| BrightnessLSeven | 92 | 4 | — | — | — |
| BrightnessLOneZero | 96 | 4 | — | — | — |
| BrightnessLNine | 100 | 4 | — | — | — |
| BrightnessLOneTwo | 104 | 4 | — | — | — |
| BrightnessLOneOne | 108 | 4 | — | — | — |
| BrightnessLOneFour | 112 | 4 | — | — | — |
| BrightnessLOneThree | 116 | 4 | — | — | — |
| BrightnessLOneSix | 120 | 4 | — | — | — |
| BrightnessLOneFive | 124 | 4 | — | — | — |

### Control fields

| field | offset | width | default | raw_range | value_semantics |
|---|---|---|---|---|---|
| ProfileNumber | 4 | 4 | 14 | 0..15 | UNVERIFIED |
| Mode | 8 | 8 | 0 | 0..255 | UNVERIFIED |
| Timestamp | 16 | 32 | 0 | — | epoch/monotonic stamp, offset read from dg/h.java builder (decompile-derived) |
| LightValue | 48 | 16 | 0 | 0..65535 | UNVERIFIED |
| BrightnessLOne | 68 | 4 | 14 | 0..15 | UNVERIFIED |
| BrightnessLTwo | 64 | 4 | 14 | 0..15 | UNVERIFIED |
| BrightnessLThree | 76 | 4 | 14 | 0..15 | UNVERIFIED |
| BrightnessLFour | 72 | 4 | 14 | 0..15 | UNVERIFIED |
| BrightnessLFive | 84 | 4 | 14 | 0..15 | UNVERIFIED |
| BrightnessLSix | 80 | 4 | 14 | 0..15 | UNVERIFIED |
| BrightnessLSeven | 92 | 4 | 14 | 0..15 | UNVERIFIED |
| BrightnessLEight | 88 | 4 | 14 | 0..15 | UNVERIFIED |
| BrightnessLNine | 100 | 4 | 14 | 0..15 | UNVERIFIED |
| BrightnessLOneZero | 96 | 4 | 14 | 0..15 | UNVERIFIED |
| BrightnessLOneOne | 108 | 4 | 14 | 0..15 | UNVERIFIED |
| BrightnessLOneTwo | 104 | 4 | 14 | 0..15 | UNVERIFIED |
| BrightnessLOneThree | 116 | 4 | 14 | 0..15 | UNVERIFIED |
| BrightnessLOneFour | 112 | 4 | 14 | 0..15 | UNVERIFIED |
| BrightnessLOneFive | 124 | 4 | 14 | 0..15 | UNVERIFIED |
| BrightnessLOneSix | 120 | 4 | 14 | 0..15 | UNVERIFIED |

## livingroomheater

- **State char UUID(s):** 00002100-6c77-4b7d-bbf6-a5e587701f3d, 00002102-6c77-4b7d-bbf6-a5e587701f3d
- **Control source:** gg/a.java

### State fields

| field | offset | width | default | raw_range | value_semantics |
|---|---|---|---|---|---|
| Variant | 0 | 2 | — | — | — |
| Installed | 4 | 1 | — | — | — |
| TemperatureWater | 5 | 1 | — | — | — |
| StateWater | 6 | 1 | — | — | — |
| StateAir | 7 | 1 | — | — | — |
| Error | 8 | 4 | — | — | — |
| Mode | 12 | 4 | — | — | — |
| TemperatureAir | 16 | 8 | — | — | — |

### Control fields

| field | offset | width | default | raw_range | value_semantics |
|---|---|---|---|---|---|
| StateAir | **MERGED_AMBIGUOUS** | 2 | 3 | 0..3 | UNVERIFIED |
| StateWater | **MERGED_AMBIGUOUS** | 2 | 3 | 0..3 | UNVERIFIED |
| TemperatureWater | **MERGED_AMBIGUOUS** | 2 | 3 | 0..3 | UNVERIFIED |
| Mode | 12 | 4 | 5 | 0..15 | UNVERIFIED |
| TemperatureAir | **MERGED_AMBIGUOUS** | 8 | 0 | 0..255 | UNVERIFIED |

## roof

- **State char UUID(s):** 00001400-6c77-4b7d-bbf6-a5e587701f3d, 00001402-6c77-4b7d-bbf6-a5e587701f3d
- **Control source:** jg/a.java

### State fields

| field | offset | width | default | raw_range | value_semantics |
|---|---|---|---|---|---|
| Position | 0 | 4 | — | — | — |
| Installed | 6 | 1 | — | — | — |
| SafetyCounterValid | 7 | 1 | — | — | — |
| InfoPopUp | 12 | 4 | — | — | — |

### Control fields

| field | offset | width | default | raw_range | value_semantics |
|---|---|---|---|---|---|
| Up | 6 | 2 | 3 | 0..3 | Up=1 => OPEN command (byte 0x01); app-verified polarity ig/c.java n2()/e1(), PHYSICAL actuation NOT-LIVE-VERIFIED |
| Down | 4 | 2 | 3 | 0..3 | Down=1 => CLOSE command (byte 0x04); stop=Up0/Down0 (0x00). App-verified, PHYSICAL NOT-LIVE-VERIFIED |
| SafetyCounter | 8 | 32 | 0 | — | app-generated monotonic BE-uint32, ~+1/500ms (NOT unit-echoed); see overrides.py + device.actuate_roof |

## roofaircondition

- **State char UUID(s):** 00002000-6c77-4b7d-bbf6-a5e587701f3d, 00002002-6c77-4b7d-bbf6-a5e587701f3d
- **Control source:** lg/a.java

### State fields

| field | offset | width | default | raw_range | value_semantics |
|---|---|---|---|---|---|
| Error | 0 | 2 | — | — | — |
| Mode | 2 | 2 | — | — | — |
| Installed | 6 | 1 | — | — | — |
| State | 7 | 1 | — | — | — |
| Fanspeed | 12 | 4 | — | — | — |
| Temperature | 16 | 8 | — | — | — |

### Control fields

| field | offset | width | default | raw_range | value_semantics |
|---|---|---|---|---|---|
| State | 6 | 2 | 3 | 0..3 | UNVERIFIED |
| FanSpeed | **MERGED_AMBIGUOUS** | 4 | 7 | 0..15 | UNVERIFIED |
| Mode | **MERGED_AMBIGUOUS** | 4 | 7 | 0..15 | UNVERIFIED |
| Temperature | **MERGED_AMBIGUOUS** | 8 | 0 | 0..255 | UNVERIFIED |

## satelliteantenna

- **State char UUID(s):** 00001900-6c77-4b7d-bbf6-a5e587701f3d, 00001902-6c77-4b7d-bbf6-a5e587701f3d, 00001903-6c77-4b7d-bbf6-a5e587701f3d, 00001904-6c77-4b7d-bbf6-a5e587701f3d, 00001905-6c77-4b7d-bbf6-a5e587701f3d
- **Control source:** gg/a.java

### State fields

| field | offset | width | default | raw_range | value_semantics |
|---|---|---|---|---|---|
| Error | 0 | 4 | — | — | — |
| System | 4 | 2 | — | — | — |
| Installed | 7 | 1 | — | — | — |
| SatelliteSelection | 8 | 4 | — | — | — |
| Dish | 12 | 4 | — | — | — |
| SignalLevel | 16 | 8 | — | — | — |

### Control fields

| field | offset | width | default | raw_range | value_semantics |
|---|---|---|---|---|---|
| DishStop | **MERGED_AMBIGUOUS** | UNKNOWN | UNKNOWN | — | UNVERIFIED |
| Wlan | **MERGED_AMBIGUOUS** | UNKNOWN | UNKNOWN | — | UNVERIFIED |
| System | **MERGED_AMBIGUOUS** | 2 | 3 | 0..3 | UNVERIFIED |
| Dish | **MERGED_AMBIGUOUS** | 2 | 0 | 0..3 | UNVERIFIED |
| SatelliteSelection | 12 | 4 | 0 | 0..15 | UNVERIFIED |

## stairs

- **State char UUID(s):** 00001800-6c77-4b7d-bbf6-a5e587701f3d, 00001802-6c77-4b7d-bbf6-a5e587701f3d
- **Control source:** pg/a.java

### State fields

| field | offset | width | default | raw_range | value_semantics |
|---|---|---|---|---|---|
| InfoPopUp | 2 | 2 | — | — | — |
| Sensor | 4 | 1 | — | — | — |
| State | 5 | 1 | — | — | — |
| OperationMode | 6 | 1 | — | — | — |
| Installed | 7 | 1 | — | — | — |

### Control fields

| field | offset | width | default | raw_range | value_semantics |
|---|---|---|---|---|---|
| OperationMode | **MERGED_AMBIGUOUS** | 2 | 3 | 0..3 | UNVERIFIED |
| Movement | **MERGED_AMBIGUOUS** | 2 | 0 | 0..3 | UNVERIFIED |

## vehicle

- **State char UUID(s):** 00001000-6c77-4b7d-bbf6-a5e587701f3d, 00001004-6c77-4b7d-bbf6-a5e587701f3d
- **Control source:** —

### State fields

| field | offset | width | default | raw_range | value_semantics |
|---|---|---|---|---|---|
| CarVariant | 0 | 4 | — | — | — |
| CarLevelPopUp | 4 | 2 | — | — | — |
| TerminalOneFive | 7 | 1 | — | — | — |
| CarTimeYear | 8 | 8 | — | — | — |
| CarTimeMonth | 16 | 8 | — | — | — |
| CarTimeDay | 24 | 8 | — | — | — |
| CarTimeHour | 32 | 8 | — | — | — |
| CarTimeMinute | 40 | 8 | — | — | — |
| CarTimeSecond | 48 | 8 | — | — | — |
| CarLevelRoll | 56 | 16 | — | — | — |
| CarLevelPitch | 72 | 16 | — | — | — |

### Control fields

_(none)_

## water

- **State char UUID(s):** 00001300-6c77-4b7d-bbf6-a5e587701f3d, 00001302-6c77-4b7d-bbf6-a5e587701f3d
- **Control source:** —

### State fields

| field | offset | width | default | raw_range | value_semantics |
|---|---|---|---|---|---|
| FreshWaterInfoPopUp | 0 | 4 | — | — | — |
| Installed | 6 | 1 | — | — | — |
| FreshWaterUnit | 7 | 1 | — | — | — |
| FreshWaterLevel | 8 | 8 | — | — | — |
| FreshWaterVolume | 16 | 8 | — | — | — |
| WasteWaterInfoPopUp | 26 | 2 | — | — | — |
| WasteWaterUnit | 31 | 1 | — | — | — |
| WasteWaterLevel | 32 | 8 | — | — | — |
| WasteWaterVolume | 40 | 8 | — | — | — |

### Control fields

_(none)_
