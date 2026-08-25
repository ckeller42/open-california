# Protocol frame bit-layouts

GENERATED from `protocol/dictionary.yaml` — do not hand-edit; see [README.md](README.md) for how to regenerate.

Bit numbering is **MSB-first** (`calictl/protocol.py:to_bits`): frame-bit index 0 is bit 7 (the MSB) of byte 0. A field at `offset`/`width` occupies frame-bit indices `[offset .. offset + width - 1]`; the grids below place those bits per byte (bit 7 = MSB .. bit 0 = LSB). `·` marks an unused/gap bit. Fields with a `MERGED_AMBIGUOUS` offset can't be positioned and are listed separately.

## airheater

### State frame (char(s): 00001700-6c77-4b7d-bbf6-a5e587701f3d, 00001702-6c77-4b7d-bbf6-a5e587701f3d)

| byte | bit 7 | bit 6 | bit 5 | bit 4 | bit 3 | bit 2 | bit 1 | bit 0 |
|---|---|---|---|---|---|---|---|---|
| 0 | AirDistribution | AirDistribution | · | Installed | FaultTriggerBit | PermanentOperation | NormalOperation | PermanentOperationRequestConfirmation |
| 1 | HeatingLevel | HeatingLevel | HeatingLevel | HeatingLevel | ErrorCode | ErrorCode | ErrorCode | ErrorCode |
| 2 | OperationModeCombined | OperationModeCombined | OperationModeCombined | OperationModeCombined | OperationModeAirHeater | OperationModeAirHeater | OperationModeAirHeater | OperationModeAirHeater |
| 3 | RunningTime | RunningTime | RunningTime | RunningTime | RunningTime | RunningTime | RunningTime | RunningTime |
| 4 | TimerHour | TimerHour | TimerHour | TimerHour | TimerHour | TimerHour | TimerHour | TimerHour |
| 5 | TimerMin | TimerMin | TimerMin | TimerMin | TimerMin | TimerMin | TimerMin | TimerMin |
| 6 | RunningTimeinAction | RunningTimeinAction | RunningTimeinAction | RunningTimeinAction | RunningTimeinAction | RunningTimeinAction | RunningTimeinAction | RunningTimeinAction |

### Control frame (control_source: sf/a.java)

| byte | bit 7 | bit 6 | bit 5 | bit 4 | bit 3 | bit 2 | bit 1 | bit 0 |
|---|---|---|---|---|---|---|---|---|
| 0 | AirDistribution | AirDistribution | PermanentOperationRequest | PermanentOperationRequest | PermanentOperationConfirmation | PermanentOperationConfirmation | NormalOperationRequest | NormalOperationRequest |
| 1 | OperationModeAirHeater | OperationModeAirHeater | OperationModeAirHeater | OperationModeAirHeater | HeatingLevel | HeatingLevel | HeatingLevel | HeatingLevel |

**Ambiguous offset (not shown in map):** OperationModeCombined, RunningTime, TimerHour, TimerMin

## campingmode

### State frame (char(s): 00001200-6c77-4b7d-bbf6-a5e587701f3d, 00001202-6c77-4b7d-bbf6-a5e587701f3d)

| byte | bit 7 | bit 6 | bit 5 | bit 4 | bit 3 | bit 2 | bit 1 | bit 0 |
|---|---|---|---|---|---|---|---|---|
| 0 | · | · | Installed | Enable | InteriorLight | OutsideLight | UsbCharger | State |

### Control frame (control_source: lg/a.java)

| byte | bit 7 | bit 6 | bit 5 | bit 4 | bit 3 | bit 2 | bit 1 | bit 0 |
|---|---|---|---|---|---|---|---|---|
| 0 | · | · | · | · | · | · | State | State |

**Ambiguous offset (not shown in map):** UsbCharger, OutsideLight, InteriorLight

## cooler

### State frame (char(s): 00001100-6c77-4b7d-bbf6-a5e587701f3d, 00001102-6c77-4b7d-bbf6-a5e587701f3d)

| byte | bit 7 | bit 6 | bit 5 | bit 4 | bit 3 | bit 2 | bit 1 | bit 0 |
|---|---|---|---|---|---|---|---|---|
| 0 | Error | Error | · | NightTimerSet | Installed | TimerElapsed | TimerState | State |
| 1 | Mode | Mode | Mode | Mode | Level | Level | Level | Level |
| 2 | TimerHourSet | TimerHourSet | TimerHourSet | TimerHourSet | TimerHourSet | TimerHourSet | TimerHourSet | TimerHourSet |
| 3 | TimerMinSet | TimerMinSet | TimerMinSet | TimerMinSet | TimerMinSet | TimerMinSet | TimerMinSet | TimerMinSet |
| 4 | TimerCounterHour | TimerCounterHour | TimerCounterHour | TimerCounterHour | TimerCounterHour | TimerCounterHour | TimerCounterHour | TimerCounterHour |
| 5 | TimerCounterMin | TimerCounterMin | TimerCounterMin | TimerCounterMin | TimerCounterMin | TimerCounterMin | TimerCounterMin | TimerCounterMin |
| 6 | NightTimerHourOn | NightTimerHourOn | NightTimerHourOn | NightTimerHourOn | NightTimerHourOn | NightTimerHourOn | NightTimerHourOn | NightTimerHourOn |
| 7 | NightTimerHourOff | NightTimerHourOff | NightTimerHourOff | NightTimerHourOff | NightTimerHourOff | NightTimerHourOff | NightTimerHourOff | NightTimerHourOff |

### Control frame (control_source: sf/a.java)

| byte | bit 7 | bit 6 | bit 5 | bit 4 | bit 3 | bit 2 | bit 1 | bit 0 |
|---|---|---|---|---|---|---|---|---|
| 0 | NightTimerSet | NightTimerSet | TimerCancel | TimerCancel | TimerStart | TimerStart | State | State |
| 1 | Mode | Mode | Mode | Mode | Level | Level | Level | Level |

**Ambiguous offset (not shown in map):** TimerHour, TimerMin, NightTimerHourOn, NightTimerHourOff

## energy

### State frame (char(s): 00001600-6c77-4b7d-bbf6-a5e587701f3d, 00001602-6c77-4b7d-bbf6-a5e587701f3d)

| byte | bit 7 | bit 6 | bit 5 | bit 4 | bit 3 | bit 2 | bit 1 | bit 0 |
|---|---|---|---|---|---|---|---|---|
| 0 | LandNotAvailable | LandDefect | EnergyModeNotSelectable | DcdcDefect | WarningLevelActive | TwoBattSwitchAtWorkshop | TwoBattSwitchAtCharging | TwoBattNotCharged |
| 1 | DcdcInstalled | LadInstalled | PvInstalled | EmpInstalled | SystemError | CurrentDeratingTemperature | SleepWarning | PvDefect |
| 2 | SocOneBattAfs | SocOneBattAfs | SocOneBattAfs | SocOneBattAfs | EnergyMode | EnergyMode | WarningLevelTwo | WarningLevelTwo |
| 3 | StateDcdcAfs | StateDcdcAfs | StateDcdcAfs | StateDcdcAfs | SocTwoBattAfs | SocTwoBattAfs | SocTwoBattAfs | SocTwoBattAfs |
| 4 | StatePvAfs | StatePvAfs | StatePvAfs | StatePvAfs | StateLandAfs | StateLandAfs | StateLandAfs | StateLandAfs |
| 5 | AgeOneBattValuesMinutes | AgeOneBattValuesMinutes | AgeOneBattValuesMinutes | AgeOneBattValuesMinutes | AgeOneBattValuesMinutes | AgeOneBattValuesMinutes | AgeOneBattValuesMinutes | AgeOneBattValuesMinutes |
| 6 | IOneBattBemAfs | IOneBattBemAfs | IOneBattBemAfs | IOneBattBemAfs | IOneBattBemAfs | IOneBattBemAfs | IOneBattBemAfs | IOneBattBemAfs |
| 7 | PDcdcAfs | PDcdcAfs | PDcdcAfs | PDcdcAfs | PDcdcAfs | PDcdcAfs | PDcdcAfs | PDcdcAfs |
| 8 | PLandAfs | PLandAfs | PLandAfs | PLandAfs | PLandAfs | PLandAfs | PLandAfs | PLandAfs |
| 9 | PPvAfs | PPvAfs | PPvAfs | PPvAfs | PPvAfs | PPvAfs | PPvAfs | PPvAfs |
| 10 | tTwoBattRemainingh | tTwoBattRemainingh | tTwoBattRemainingh | tTwoBattRemainingh | tTwoBattRemainingh | tTwoBattRemainingh | tTwoBattRemainingh | tTwoBattRemainingh |
| 11 | tTwoBattRemainingmin | tTwoBattRemainingmin | tTwoBattRemainingmin | tTwoBattRemainingmin | tTwoBattRemainingmin | tTwoBattRemainingmin | tTwoBattRemainingmin | tTwoBattRemainingmin |
| 12 | UOneBattBemAfs | UOneBattBemAfs | UOneBattBemAfs | UOneBattBemAfs | UOneBattBemAfs | UOneBattBemAfs | UOneBattBemAfs | UOneBattBemAfs |
| 13 | UTwoBattBemAfs | UTwoBattBemAfs | UTwoBattBemAfs | UTwoBattBemAfs | UTwoBattBemAfs | UTwoBattBemAfs | UTwoBattBemAfs | UTwoBattBemAfs |
| 14 | ITwoBattBemAfs | ITwoBattBemAfs | ITwoBattBemAfs | ITwoBattBemAfs | ITwoBattBemAfs | ITwoBattBemAfs | ITwoBattBemAfs | ITwoBattBemAfs |
| 15 | ITwoBattBemAfs | ITwoBattBemAfs | ITwoBattBemAfs | ITwoBattBemAfs | ITwoBattBemAfs | ITwoBattBemAfs | ITwoBattBemAfs | ITwoBattBemAfs |
| 16 | IDcdcAfs | IDcdcAfs | IDcdcAfs | IDcdcAfs | IDcdcAfs | IDcdcAfs | IDcdcAfs | IDcdcAfs |
| 17 | IDcdcAfs | IDcdcAfs | IDcdcAfs | IDcdcAfs | IDcdcAfs | IDcdcAfs | IDcdcAfs | IDcdcAfs |
| 18 | ILandAfs | ILandAfs | ILandAfs | ILandAfs | ILandAfs | ILandAfs | ILandAfs | ILandAfs |
| 19 | ILandAfs | ILandAfs | ILandAfs | ILandAfs | ILandAfs | ILandAfs | ILandAfs | ILandAfs |
| 20 | IPvAfs | IPvAfs | IPvAfs | IPvAfs | IPvAfs | IPvAfs | IPvAfs | IPvAfs |
| 21 | IPvAfs | IPvAfs | IPvAfs | IPvAfs | IPvAfs | IPvAfs | IPvAfs | IPvAfs |

### Control frame (control_source: pg/a.java)

| byte | bit 7 | bit 6 | bit 5 | bit 4 | bit 3 | bit 2 | bit 1 | bit 0 |
|---|---|---|---|---|---|---|---|---|
| 0 | · | · | EnergyModeSet | EnergyModeSet | · | · | · | DisplayRefresh |

## general

### State frame (char(s): 00001000-6c77-4b7d-bbf6-a5e587701f3d, 00001001-6c77-4b7d-bbf6-a5e587701f3d)

| byte | bit 7 | bit 6 | bit 5 | bit 4 | bit 3 | bit 2 | bit 1 | bit 0 |
|---|---|---|---|---|---|---|---|---|
| 0 | AmbSwVersion | AmbSwVersion | AmbSwVersion | AmbSwVersion | AmbSwVersion | AmbSwVersion | AmbSwVersion | AmbSwVersion |
| 1 | AmbSwVersion | AmbSwVersion | AmbSwVersion | AmbSwVersion | AmbSwVersion | AmbSwVersion | AmbSwVersion | AmbSwVersion |
| 2 | AmbSwVersion | AmbSwVersion | AmbSwVersion | AmbSwVersion | AmbSwVersion | AmbSwVersion | AmbSwVersion | AmbSwVersion |
| 3 | AmbSwVersion | AmbSwVersion | AmbSwVersion | AmbSwVersion | AmbSwVersion | AmbSwVersion | AmbSwVersion | AmbSwVersion |
| 4 | CmSwVersion | CmSwVersion | CmSwVersion | CmSwVersion | CmSwVersion | CmSwVersion | CmSwVersion | CmSwVersion |
| 5 | CmSwVersion | CmSwVersion | CmSwVersion | CmSwVersion | CmSwVersion | CmSwVersion | CmSwVersion | CmSwVersion |
| 6 | CmSwVersion | CmSwVersion | CmSwVersion | CmSwVersion | CmSwVersion | CmSwVersion | CmSwVersion | CmSwVersion |
| 7 | CmSwVersion | CmSwVersion | CmSwVersion | CmSwVersion | CmSwVersion | CmSwVersion | CmSwVersion | CmSwVersion |
| 8 | CommunicationVersion | CommunicationVersion | CommunicationVersion | CommunicationVersion | CommunicationVersion | CommunicationVersion | CommunicationVersion | CommunicationVersion |

### Control frame (control_source: —)

_(no positionally-resolved fields)_

## generalpurposesignals

### State frame (char(s): 0000f000-6c77-4b7d-bbf6-a5e587701f3d, 0000f001-6c77-4b7d-bbf6-a5e587701f3d)

| byte | bit 7 | bit 6 | bit 5 | bit 4 | bit 3 | bit 2 | bit 1 | bit 0 |
|---|---|---|---|---|---|---|---|---|
| 0 | BitZeroEight | BitZeroSeven | BitZeroSix | BitZeroFive | BitZeroFour | BitZeroThree | BitZeroTwo | BitZeroOne |
| 1 | ByteZeroOne | ByteZeroOne | ByteZeroOne | ByteZeroOne | ByteZeroOne | ByteZeroOne | ByteZeroOne | ByteZeroOne |
| 2 | ByteZeroTwo | ByteZeroTwo | ByteZeroTwo | ByteZeroTwo | ByteZeroTwo | ByteZeroTwo | ByteZeroTwo | ByteZeroTwo |
| 3 | ByteZeroThree | ByteZeroThree | ByteZeroThree | ByteZeroThree | ByteZeroThree | ByteZeroThree | ByteZeroThree | ByteZeroThree |
| 4 | ByteZeroFour | ByteZeroFour | ByteZeroFour | ByteZeroFour | ByteZeroFour | ByteZeroFour | ByteZeroFour | ByteZeroFour |
| 5 | ByteZeroFive | ByteZeroFive | ByteZeroFive | ByteZeroFive | ByteZeroFive | ByteZeroFive | ByteZeroFive | ByteZeroFive |
| 6 | ByteZeroSix | ByteZeroSix | ByteZeroSix | ByteZeroSix | ByteZeroSix | ByteZeroSix | ByteZeroSix | ByteZeroSix |
| 7 | ByteZeroSeven | ByteZeroSeven | ByteZeroSeven | ByteZeroSeven | ByteZeroSeven | ByteZeroSeven | ByteZeroSeven | ByteZeroSeven |
| 8 | ByteZeroEight | ByteZeroEight | ByteZeroEight | ByteZeroEight | ByteZeroEight | ByteZeroEight | ByteZeroEight | ByteZeroEight |
| 9 | WordZeroOne | WordZeroOne | WordZeroOne | WordZeroOne | WordZeroOne | WordZeroOne | WordZeroOne | WordZeroOne |
| 10 | WordZeroOne | WordZeroOne | WordZeroOne | WordZeroOne | WordZeroOne | WordZeroOne | WordZeroOne | WordZeroOne |
| 11 | WordZeroTwo | WordZeroTwo | WordZeroTwo | WordZeroTwo | WordZeroTwo | WordZeroTwo | WordZeroTwo | WordZeroTwo |
| 12 | WordZeroTwo | WordZeroTwo | WordZeroTwo | WordZeroTwo | WordZeroTwo | WordZeroTwo | WordZeroTwo | WordZeroTwo |
| 13 | WordZeroThree | WordZeroThree | WordZeroThree | WordZeroThree | WordZeroThree | WordZeroThree | WordZeroThree | WordZeroThree |
| 14 | WordZeroThree | WordZeroThree | WordZeroThree | WordZeroThree | WordZeroThree | WordZeroThree | WordZeroThree | WordZeroThree |
| 15 | WordZeroFour | WordZeroFour | WordZeroFour | WordZeroFour | WordZeroFour | WordZeroFour | WordZeroFour | WordZeroFour |
| 16 | WordZeroFour | WordZeroFour | WordZeroFour | WordZeroFour | WordZeroFour | WordZeroFour | WordZeroFour | WordZeroFour |
| 17 | DwordZeroOne | DwordZeroOne | DwordZeroOne | DwordZeroOne | DwordZeroOne | DwordZeroOne | DwordZeroOne | DwordZeroOne |
| 18 | DwordZeroOne | DwordZeroOne | DwordZeroOne | DwordZeroOne | DwordZeroOne | DwordZeroOne | DwordZeroOne | DwordZeroOne |
| 19 | DwordZeroOne | DwordZeroOne | DwordZeroOne | DwordZeroOne | DwordZeroOne | DwordZeroOne | DwordZeroOne | DwordZeroOne |
| 20 | DwordZeroOne | DwordZeroOne | DwordZeroOne | DwordZeroOne | DwordZeroOne | DwordZeroOne | DwordZeroOne | DwordZeroOne |

### Control frame (control_source: —)

_(no positionally-resolved fields)_

## lighting

### State frame (char(s): 00001500-6c77-4b7d-bbf6-a5e587701f3d, 00001502-6c77-4b7d-bbf6-a5e587701f3d)

| byte | bit 7 | bit 6 | bit 5 | bit 4 | bit 3 | bit 2 | bit 1 | bit 0 |
|---|---|---|---|---|---|---|---|---|
| 0 | · | · | · | · | ProfileNumber | ProfileNumber | ProfileNumber | ProfileNumber |
| 1 | Mode | Mode | Mode | Mode | Mode | Mode | Mode | Mode |
| 2 | Timestamp | Timestamp | Timestamp | Timestamp | Timestamp | Timestamp | Timestamp | Timestamp |
| 3 | Timestamp | Timestamp | Timestamp | Timestamp | Timestamp | Timestamp | Timestamp | Timestamp |
| 4 | Timestamp | Timestamp | Timestamp | Timestamp | Timestamp | Timestamp | Timestamp | Timestamp |
| 5 | Timestamp | Timestamp | Timestamp | Timestamp | Timestamp | Timestamp | Timestamp | Timestamp |
| 6 | LightValue | LightValue | LightValue | LightValue | LightValue | LightValue | LightValue | LightValue |
| 7 | LightValue | LightValue | LightValue | LightValue | LightValue | LightValue | LightValue | LightValue |
| 8 | BrightnessLTwo | BrightnessLTwo | BrightnessLTwo | BrightnessLTwo | BrightnessLOne | BrightnessLOne | BrightnessLOne | BrightnessLOne |
| 9 | BrightnessLFour | BrightnessLFour | BrightnessLFour | BrightnessLFour | BrightnessLThree | BrightnessLThree | BrightnessLThree | BrightnessLThree |
| 10 | BrightnessLSix | BrightnessLSix | BrightnessLSix | BrightnessLSix | BrightnessLFive | BrightnessLFive | BrightnessLFive | BrightnessLFive |
| 11 | BrightnessLEight | BrightnessLEight | BrightnessLEight | BrightnessLEight | BrightnessLSeven | BrightnessLSeven | BrightnessLSeven | BrightnessLSeven |
| 12 | BrightnessLOneZero | BrightnessLOneZero | BrightnessLOneZero | BrightnessLOneZero | BrightnessLNine | BrightnessLNine | BrightnessLNine | BrightnessLNine |
| 13 | BrightnessLOneTwo | BrightnessLOneTwo | BrightnessLOneTwo | BrightnessLOneTwo | BrightnessLOneOne | BrightnessLOneOne | BrightnessLOneOne | BrightnessLOneOne |
| 14 | BrightnessLOneFour | BrightnessLOneFour | BrightnessLOneFour | BrightnessLOneFour | BrightnessLOneThree | BrightnessLOneThree | BrightnessLOneThree | BrightnessLOneThree |
| 15 | BrightnessLOneSix | BrightnessLOneSix | BrightnessLOneSix | BrightnessLOneSix | BrightnessLOneFive | BrightnessLOneFive | BrightnessLOneFive | BrightnessLOneFive |

### Control frame (control_source: eg/a.java)

| byte | bit 7 | bit 6 | bit 5 | bit 4 | bit 3 | bit 2 | bit 1 | bit 0 |
|---|---|---|---|---|---|---|---|---|
| 0 | · | · | · | · | ProfileNumber | ProfileNumber | ProfileNumber | ProfileNumber |
| 1 | Mode | Mode | Mode | Mode | Mode | Mode | Mode | Mode |
| 2 | Timestamp | Timestamp | Timestamp | Timestamp | Timestamp | Timestamp | Timestamp | Timestamp |
| 3 | Timestamp | Timestamp | Timestamp | Timestamp | Timestamp | Timestamp | Timestamp | Timestamp |
| 4 | Timestamp | Timestamp | Timestamp | Timestamp | Timestamp | Timestamp | Timestamp | Timestamp |
| 5 | Timestamp | Timestamp | Timestamp | Timestamp | Timestamp | Timestamp | Timestamp | Timestamp |
| 6 | LightValue | LightValue | LightValue | LightValue | LightValue | LightValue | LightValue | LightValue |
| 7 | LightValue | LightValue | LightValue | LightValue | LightValue | LightValue | LightValue | LightValue |
| 8 | BrightnessLTwo | BrightnessLTwo | BrightnessLTwo | BrightnessLTwo | BrightnessLOne | BrightnessLOne | BrightnessLOne | BrightnessLOne |
| 9 | BrightnessLFour | BrightnessLFour | BrightnessLFour | BrightnessLFour | BrightnessLThree | BrightnessLThree | BrightnessLThree | BrightnessLThree |
| 10 | BrightnessLSix | BrightnessLSix | BrightnessLSix | BrightnessLSix | BrightnessLFive | BrightnessLFive | BrightnessLFive | BrightnessLFive |
| 11 | BrightnessLEight | BrightnessLEight | BrightnessLEight | BrightnessLEight | BrightnessLSeven | BrightnessLSeven | BrightnessLSeven | BrightnessLSeven |
| 12 | BrightnessLOneZero | BrightnessLOneZero | BrightnessLOneZero | BrightnessLOneZero | BrightnessLNine | BrightnessLNine | BrightnessLNine | BrightnessLNine |
| 13 | BrightnessLOneTwo | BrightnessLOneTwo | BrightnessLOneTwo | BrightnessLOneTwo | BrightnessLOneOne | BrightnessLOneOne | BrightnessLOneOne | BrightnessLOneOne |
| 14 | BrightnessLOneFour | BrightnessLOneFour | BrightnessLOneFour | BrightnessLOneFour | BrightnessLOneThree | BrightnessLOneThree | BrightnessLOneThree | BrightnessLOneThree |
| 15 | BrightnessLOneSix | BrightnessLOneSix | BrightnessLOneSix | BrightnessLOneSix | BrightnessLOneFive | BrightnessLOneFive | BrightnessLOneFive | BrightnessLOneFive |

## livingroomheater

### State frame (char(s): 00002100-6c77-4b7d-bbf6-a5e587701f3d, 00002102-6c77-4b7d-bbf6-a5e587701f3d)

| byte | bit 7 | bit 6 | bit 5 | bit 4 | bit 3 | bit 2 | bit 1 | bit 0 |
|---|---|---|---|---|---|---|---|---|
| 0 | Variant | Variant | · | · | Installed | TemperatureWater | StateWater | StateAir |
| 1 | Error | Error | Error | Error | Mode | Mode | Mode | Mode |
| 2 | TemperatureAir | TemperatureAir | TemperatureAir | TemperatureAir | TemperatureAir | TemperatureAir | TemperatureAir | TemperatureAir |

### Control frame (control_source: gg/a.java)

| byte | bit 7 | bit 6 | bit 5 | bit 4 | bit 3 | bit 2 | bit 1 | bit 0 |
|---|---|---|---|---|---|---|---|---|
| 0 | · | · | · | · | · | · | · | · |
| 1 | · | · | · | · | Mode | Mode | Mode | Mode |

**Ambiguous offset (not shown in map):** StateAir, StateWater, TemperatureWater, TemperatureAir

## roof

### State frame (char(s): 00001400-6c77-4b7d-bbf6-a5e587701f3d, 00001402-6c77-4b7d-bbf6-a5e587701f3d)

| byte | bit 7 | bit 6 | bit 5 | bit 4 | bit 3 | bit 2 | bit 1 | bit 0 |
|---|---|---|---|---|---|---|---|---|
| 0 | Position | Position | Position | Position | · | · | Installed | SafetyCounterValid |
| 1 | · | · | · | · | InfoPopUp | InfoPopUp | InfoPopUp | InfoPopUp |

### Control frame (control_source: jg/a.java)

| byte | bit 7 | bit 6 | bit 5 | bit 4 | bit 3 | bit 2 | bit 1 | bit 0 |
|---|---|---|---|---|---|---|---|---|
| 0 | · | · | · | · | Down | Down | Up | Up |
| 1 | SafetyCounter | SafetyCounter | SafetyCounter | SafetyCounter | SafetyCounter | SafetyCounter | SafetyCounter | SafetyCounter |
| 2 | SafetyCounter | SafetyCounter | SafetyCounter | SafetyCounter | SafetyCounter | SafetyCounter | SafetyCounter | SafetyCounter |
| 3 | SafetyCounter | SafetyCounter | SafetyCounter | SafetyCounter | SafetyCounter | SafetyCounter | SafetyCounter | SafetyCounter |
| 4 | SafetyCounter | SafetyCounter | SafetyCounter | SafetyCounter | SafetyCounter | SafetyCounter | SafetyCounter | SafetyCounter |

## roofaircondition

### State frame (char(s): 00002000-6c77-4b7d-bbf6-a5e587701f3d, 00002002-6c77-4b7d-bbf6-a5e587701f3d)

| byte | bit 7 | bit 6 | bit 5 | bit 4 | bit 3 | bit 2 | bit 1 | bit 0 |
|---|---|---|---|---|---|---|---|---|
| 0 | Error | Error | Mode | Mode | · | · | Installed | State |
| 1 | · | · | · | · | Fanspeed | Fanspeed | Fanspeed | Fanspeed |
| 2 | Temperature | Temperature | Temperature | Temperature | Temperature | Temperature | Temperature | Temperature |

### Control frame (control_source: lg/a.java)

| byte | bit 7 | bit 6 | bit 5 | bit 4 | bit 3 | bit 2 | bit 1 | bit 0 |
|---|---|---|---|---|---|---|---|---|
| 0 | · | · | · | · | · | · | State | State |

**Ambiguous offset (not shown in map):** FanSpeed, Mode, Temperature

## satelliteantenna

### State frame (char(s): 00001900-6c77-4b7d-bbf6-a5e587701f3d, 00001902-6c77-4b7d-bbf6-a5e587701f3d, 00001903-6c77-4b7d-bbf6-a5e587701f3d, 00001904-6c77-4b7d-bbf6-a5e587701f3d, 00001905-6c77-4b7d-bbf6-a5e587701f3d)

| byte | bit 7 | bit 6 | bit 5 | bit 4 | bit 3 | bit 2 | bit 1 | bit 0 |
|---|---|---|---|---|---|---|---|---|
| 0 | Error | Error | Error | Error | System | System | · | Installed |
| 1 | SatelliteSelection | SatelliteSelection | SatelliteSelection | SatelliteSelection | Dish | Dish | Dish | Dish |
| 2 | SignalLevel | SignalLevel | SignalLevel | SignalLevel | SignalLevel | SignalLevel | SignalLevel | SignalLevel |

### Control frame (control_source: gg/a.java)

| byte | bit 7 | bit 6 | bit 5 | bit 4 | bit 3 | bit 2 | bit 1 | bit 0 |
|---|---|---|---|---|---|---|---|---|
| 0 | · | · | · | · | · | · | · | · |
| 1 | · | · | · | · | SatelliteSelection | SatelliteSelection | SatelliteSelection | SatelliteSelection |

**Ambiguous offset (not shown in map):** DishStop, Wlan, System, Dish

## stairs

### State frame (char(s): 00001800-6c77-4b7d-bbf6-a5e587701f3d, 00001802-6c77-4b7d-bbf6-a5e587701f3d)

| byte | bit 7 | bit 6 | bit 5 | bit 4 | bit 3 | bit 2 | bit 1 | bit 0 |
|---|---|---|---|---|---|---|---|---|
| 0 | · | · | InfoPopUp | InfoPopUp | Sensor | State | OperationMode | Installed |

### Control frame (control_source: pg/a.java)

_(no positionally-resolved fields)_

**Ambiguous offset (not shown in map):** OperationMode, Movement

## vehicle

### State frame (char(s): 00001000-6c77-4b7d-bbf6-a5e587701f3d, 00001004-6c77-4b7d-bbf6-a5e587701f3d)

| byte | bit 7 | bit 6 | bit 5 | bit 4 | bit 3 | bit 2 | bit 1 | bit 0 |
|---|---|---|---|---|---|---|---|---|
| 0 | CarVariant | CarVariant | CarVariant | CarVariant | CarLevelPopUp | CarLevelPopUp | · | TerminalOneFive |
| 1 | CarTimeYear | CarTimeYear | CarTimeYear | CarTimeYear | CarTimeYear | CarTimeYear | CarTimeYear | CarTimeYear |
| 2 | CarTimeMonth | CarTimeMonth | CarTimeMonth | CarTimeMonth | CarTimeMonth | CarTimeMonth | CarTimeMonth | CarTimeMonth |
| 3 | CarTimeDay | CarTimeDay | CarTimeDay | CarTimeDay | CarTimeDay | CarTimeDay | CarTimeDay | CarTimeDay |
| 4 | CarTimeHour | CarTimeHour | CarTimeHour | CarTimeHour | CarTimeHour | CarTimeHour | CarTimeHour | CarTimeHour |
| 5 | CarTimeMinute | CarTimeMinute | CarTimeMinute | CarTimeMinute | CarTimeMinute | CarTimeMinute | CarTimeMinute | CarTimeMinute |
| 6 | CarTimeSecond | CarTimeSecond | CarTimeSecond | CarTimeSecond | CarTimeSecond | CarTimeSecond | CarTimeSecond | CarTimeSecond |
| 7 | CarLevelRoll | CarLevelRoll | CarLevelRoll | CarLevelRoll | CarLevelRoll | CarLevelRoll | CarLevelRoll | CarLevelRoll |
| 8 | CarLevelRoll | CarLevelRoll | CarLevelRoll | CarLevelRoll | CarLevelRoll | CarLevelRoll | CarLevelRoll | CarLevelRoll |
| 9 | CarLevelPitch | CarLevelPitch | CarLevelPitch | CarLevelPitch | CarLevelPitch | CarLevelPitch | CarLevelPitch | CarLevelPitch |
| 10 | CarLevelPitch | CarLevelPitch | CarLevelPitch | CarLevelPitch | CarLevelPitch | CarLevelPitch | CarLevelPitch | CarLevelPitch |

### Control frame (control_source: —)

_(no positionally-resolved fields)_

## water

### State frame (char(s): 00001300-6c77-4b7d-bbf6-a5e587701f3d, 00001302-6c77-4b7d-bbf6-a5e587701f3d)

| byte | bit 7 | bit 6 | bit 5 | bit 4 | bit 3 | bit 2 | bit 1 | bit 0 |
|---|---|---|---|---|---|---|---|---|
| 0 | FreshWaterInfoPopUp | FreshWaterInfoPopUp | FreshWaterInfoPopUp | FreshWaterInfoPopUp | · | · | Installed | FreshWaterUnit |
| 1 | FreshWaterLevel | FreshWaterLevel | FreshWaterLevel | FreshWaterLevel | FreshWaterLevel | FreshWaterLevel | FreshWaterLevel | FreshWaterLevel |
| 2 | FreshWaterVolume | FreshWaterVolume | FreshWaterVolume | FreshWaterVolume | FreshWaterVolume | FreshWaterVolume | FreshWaterVolume | FreshWaterVolume |
| 3 | · | · | WasteWaterInfoPopUp | WasteWaterInfoPopUp | · | · | · | WasteWaterUnit |
| 4 | WasteWaterLevel | WasteWaterLevel | WasteWaterLevel | WasteWaterLevel | WasteWaterLevel | WasteWaterLevel | WasteWaterLevel | WasteWaterLevel |
| 5 | WasteWaterVolume | WasteWaterVolume | WasteWaterVolume | WasteWaterVolume | WasteWaterVolume | WasteWaterVolume | WasteWaterVolume | WasteWaterVolume |

### Control frame (control_source: —)

_(no positionally-resolved fields)_
