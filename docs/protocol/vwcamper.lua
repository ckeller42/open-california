-- GENERATED from protocol/dictionary.yaml — do not hand-edit; regenerate with
-- `python3 -m tools.gen_wireshark_dissector`. See docs/protocol/README.md.
--
-- Best-effort Wireshark Lua dissector for the VW California T7 camper unit's vendor
-- BLE GATT protocol (see CLAUDE.md / docs/business-logic/). NOT runtime-tested
-- against a live Wireshark capture — a captured BTATT value read/notified on a
-- matching characteristic UUID should self-annotate per field, but verify before
-- relying on it.
--
-- Bit convention (matches calictl/protocol.py to_bits/get_field): MSB-first. A field
-- at bit-offset `o`, width `w` starts in byte `o // 8`, at bit `7 - (o %% 8)` (7=MSB
-- .. 0=LSB) within that byte, descending for `w` bits. Fields with an unresolved
-- (`MERGED_AMBIGUOUS`) offset can't be masked positionally and are skipped, listed
-- in a comment per function instead.

local vwcamper_proto = Proto("vwcamper", "VW California Camper Unit (vendor BLE)")

-- ProtoFields, grouped per function -----------------------------------------------

-- airheater (state char 00001702-6c77-4b7d-bbf6-a5e587701f3d)
local f_airheater_AirDistribution = ProtoField.uint8("vwcamper.airheater.AirDistribution", "AirDistribution", base.DEC, nil, 0xc0)
local f_airheater_Installed = ProtoField.bool("vwcamper.airheater.Installed", "Installed", 8, nil, 0x10)
local f_airheater_FaultTriggerBit = ProtoField.bool("vwcamper.airheater.FaultTriggerBit", "FaultTriggerBit", 8, nil, 0x08)
local f_airheater_PermanentOperation = ProtoField.bool("vwcamper.airheater.PermanentOperation", "PermanentOperation", 8, nil, 0x04)
local f_airheater_NormalOperation = ProtoField.bool("vwcamper.airheater.NormalOperation", "NormalOperation", 8, nil, 0x02)
local f_airheater_PermanentOperationRequestConfirmation = ProtoField.bool("vwcamper.airheater.PermanentOperationRequestConfirmation", "PermanentOperationRequestConfirmation", 8, nil, 0x01)
local f_airheater_HeatingLevel = ProtoField.uint8("vwcamper.airheater.HeatingLevel", "HeatingLevel", base.DEC, nil, 0xf0)
local f_airheater_ErrorCode = ProtoField.uint8("vwcamper.airheater.ErrorCode", "ErrorCode", base.DEC, nil, 0x0f)
local f_airheater_OperationModeCombined = ProtoField.uint8("vwcamper.airheater.OperationModeCombined", "OperationModeCombined", base.DEC, nil, 0xf0)
local f_airheater_OperationModeAirHeater = ProtoField.uint8("vwcamper.airheater.OperationModeAirHeater", "OperationModeAirHeater", base.DEC, nil, 0x0f)
local f_airheater_RunningTime = ProtoField.uint8("vwcamper.airheater.RunningTime", "RunningTime", base.DEC, nil, 0xff)
local f_airheater_TimerHour = ProtoField.uint8("vwcamper.airheater.TimerHour", "TimerHour", base.DEC, nil, 0xff)
local f_airheater_TimerMin = ProtoField.uint8("vwcamper.airheater.TimerMin", "TimerMin", base.DEC, nil, 0xff)
local f_airheater_RunningTimeinAction = ProtoField.uint8("vwcamper.airheater.RunningTimeinAction", "RunningTimeinAction", base.DEC, nil, 0xff)

-- campingmode (state char 00001202-6c77-4b7d-bbf6-a5e587701f3d)
local f_campingmode_Installed = ProtoField.bool("vwcamper.campingmode.Installed", "Installed", 8, nil, 0x20)
local f_campingmode_Enable = ProtoField.bool("vwcamper.campingmode.Enable", "Enable", 8, nil, 0x10)
local f_campingmode_InteriorLight = ProtoField.bool("vwcamper.campingmode.InteriorLight", "InteriorLight", 8, nil, 0x08)
local f_campingmode_OutsideLight = ProtoField.bool("vwcamper.campingmode.OutsideLight", "OutsideLight", 8, nil, 0x04)
local f_campingmode_UsbCharger = ProtoField.bool("vwcamper.campingmode.UsbCharger", "UsbCharger", 8, nil, 0x02)
local f_campingmode_State = ProtoField.bool("vwcamper.campingmode.State", "State", 8, nil, 0x01)

-- cooler (state char 00001102-6c77-4b7d-bbf6-a5e587701f3d)
local f_cooler_Error = ProtoField.uint8("vwcamper.cooler.Error", "Error", base.DEC, nil, 0xc0)
local f_cooler_NightTimerSet = ProtoField.bool("vwcamper.cooler.NightTimerSet", "NightTimerSet", 8, nil, 0x10)
local f_cooler_Installed = ProtoField.bool("vwcamper.cooler.Installed", "Installed", 8, nil, 0x08)
local f_cooler_TimerElapsed = ProtoField.bool("vwcamper.cooler.TimerElapsed", "TimerElapsed", 8, nil, 0x04)
local f_cooler_TimerState = ProtoField.bool("vwcamper.cooler.TimerState", "TimerState", 8, nil, 0x02)
local f_cooler_State = ProtoField.bool("vwcamper.cooler.State", "State", 8, nil, 0x01)
local f_cooler_Mode = ProtoField.uint8("vwcamper.cooler.Mode", "Mode", base.DEC, nil, 0xf0)
local f_cooler_Level = ProtoField.uint8("vwcamper.cooler.Level", "Level", base.DEC, nil, 0x0f)
local f_cooler_TimerHourSet = ProtoField.uint8("vwcamper.cooler.TimerHourSet", "TimerHourSet", base.DEC, nil, 0xff)
local f_cooler_TimerMinSet = ProtoField.uint8("vwcamper.cooler.TimerMinSet", "TimerMinSet", base.DEC, nil, 0xff)
local f_cooler_TimerCounterHour = ProtoField.uint8("vwcamper.cooler.TimerCounterHour", "TimerCounterHour", base.DEC, nil, 0xff)
local f_cooler_TimerCounterMin = ProtoField.uint8("vwcamper.cooler.TimerCounterMin", "TimerCounterMin", base.DEC, nil, 0xff)
local f_cooler_NightTimerHourOn = ProtoField.uint8("vwcamper.cooler.NightTimerHourOn", "NightTimerHourOn", base.DEC, nil, 0xff)
local f_cooler_NightTimerHourOff = ProtoField.uint8("vwcamper.cooler.NightTimerHourOff", "NightTimerHourOff", base.DEC, nil, 0xff)

-- energy (state char 00001602-6c77-4b7d-bbf6-a5e587701f3d)
local f_energy_LandNotAvailable = ProtoField.bool("vwcamper.energy.LandNotAvailable", "LandNotAvailable", 8, nil, 0x80)
local f_energy_LandDefect = ProtoField.bool("vwcamper.energy.LandDefect", "LandDefect", 8, nil, 0x40)
local f_energy_EnergyModeNotSelectable = ProtoField.bool("vwcamper.energy.EnergyModeNotSelectable", "EnergyModeNotSelectable", 8, nil, 0x20)
local f_energy_DcdcDefect = ProtoField.bool("vwcamper.energy.DcdcDefect", "DcdcDefect", 8, nil, 0x10)
local f_energy_WarningLevelActive = ProtoField.bool("vwcamper.energy.WarningLevelActive", "WarningLevelActive", 8, nil, 0x08)
local f_energy_TwoBattSwitchAtWorkshop = ProtoField.bool("vwcamper.energy.TwoBattSwitchAtWorkshop", "TwoBattSwitchAtWorkshop", 8, nil, 0x04)
local f_energy_TwoBattSwitchAtCharging = ProtoField.bool("vwcamper.energy.TwoBattSwitchAtCharging", "TwoBattSwitchAtCharging", 8, nil, 0x02)
local f_energy_TwoBattNotCharged = ProtoField.bool("vwcamper.energy.TwoBattNotCharged", "TwoBattNotCharged", 8, nil, 0x01)
local f_energy_DcdcInstalled = ProtoField.bool("vwcamper.energy.DcdcInstalled", "DcdcInstalled", 8, nil, 0x80)
local f_energy_LadInstalled = ProtoField.bool("vwcamper.energy.LadInstalled", "LadInstalled", 8, nil, 0x40)
local f_energy_PvInstalled = ProtoField.bool("vwcamper.energy.PvInstalled", "PvInstalled", 8, nil, 0x20)
local f_energy_EmpInstalled = ProtoField.bool("vwcamper.energy.EmpInstalled", "EmpInstalled", 8, nil, 0x10)
local f_energy_SystemError = ProtoField.bool("vwcamper.energy.SystemError", "SystemError", 8, nil, 0x08)
local f_energy_CurrentDeratingTemperature = ProtoField.bool("vwcamper.energy.CurrentDeratingTemperature", "CurrentDeratingTemperature", 8, nil, 0x04)
local f_energy_SleepWarning = ProtoField.bool("vwcamper.energy.SleepWarning", "SleepWarning", 8, nil, 0x02)
local f_energy_PvDefect = ProtoField.bool("vwcamper.energy.PvDefect", "PvDefect", 8, nil, 0x01)
local f_energy_SocOneBattAfs = ProtoField.uint8("vwcamper.energy.SocOneBattAfs", "SocOneBattAfs", base.DEC, nil, 0xf0)
local f_energy_EnergyMode = ProtoField.uint8("vwcamper.energy.EnergyMode", "EnergyMode", base.DEC, nil, 0x0c)
local f_energy_WarningLevelTwo = ProtoField.uint8("vwcamper.energy.WarningLevelTwo", "WarningLevelTwo", base.DEC, nil, 0x03)
local f_energy_StateDcdcAfs = ProtoField.uint8("vwcamper.energy.StateDcdcAfs", "StateDcdcAfs", base.DEC, nil, 0xf0)
local f_energy_SocTwoBattAfs = ProtoField.uint8("vwcamper.energy.SocTwoBattAfs", "SocTwoBattAfs", base.DEC, nil, 0x0f)
local f_energy_StatePvAfs = ProtoField.uint8("vwcamper.energy.StatePvAfs", "StatePvAfs", base.DEC, nil, 0xf0)
local f_energy_StateLandAfs = ProtoField.uint8("vwcamper.energy.StateLandAfs", "StateLandAfs", base.DEC, nil, 0x0f)
local f_energy_AgeOneBattValuesMinutes = ProtoField.uint8("vwcamper.energy.AgeOneBattValuesMinutes", "AgeOneBattValuesMinutes", base.DEC, nil, 0xff)
local f_energy_IOneBattBemAfs = ProtoField.int8("vwcamper.energy.IOneBattBemAfs", "IOneBattBemAfs", base.DEC, nil, 0xff)
local f_energy_PDcdcAfs = ProtoField.int8("vwcamper.energy.PDcdcAfs", "PDcdcAfs", base.DEC, nil, 0xff)
local f_energy_PLandAfs = ProtoField.uint8("vwcamper.energy.PLandAfs", "PLandAfs", base.DEC, nil, 0xff)
local f_energy_PPvAfs = ProtoField.uint8("vwcamper.energy.PPvAfs", "PPvAfs", base.DEC, nil, 0xff)
local f_energy_tTwoBattRemainingh = ProtoField.uint8("vwcamper.energy.tTwoBattRemainingh", "tTwoBattRemainingh", base.DEC, nil, 0xff)
local f_energy_tTwoBattRemainingmin = ProtoField.uint8("vwcamper.energy.tTwoBattRemainingmin", "tTwoBattRemainingmin", base.DEC, nil, 0xff)
local f_energy_UOneBattBemAfs = ProtoField.uint8("vwcamper.energy.UOneBattBemAfs", "UOneBattBemAfs", base.DEC, nil, 0xff)
local f_energy_UTwoBattBemAfs = ProtoField.uint8("vwcamper.energy.UTwoBattBemAfs", "UTwoBattBemAfs", base.DEC, nil, 0xff)
local f_energy_ITwoBattBemAfs = ProtoField.int16("vwcamper.energy.ITwoBattBemAfs", "ITwoBattBemAfs", base.DEC, nil, 0xffff)
local f_energy_IDcdcAfs = ProtoField.int16("vwcamper.energy.IDcdcAfs", "IDcdcAfs", base.DEC, nil, 0xffff)
local f_energy_ILandAfs = ProtoField.uint16("vwcamper.energy.ILandAfs", "ILandAfs", base.DEC, nil, 0xffff)
local f_energy_IPvAfs = ProtoField.uint16("vwcamper.energy.IPvAfs", "IPvAfs", base.DEC, nil, 0xffff)

-- general (state char 00001001-6c77-4b7d-bbf6-a5e587701f3d)
local f_general_AmbSwVersion = ProtoField.uint32("vwcamper.general.AmbSwVersion", "AmbSwVersion", base.DEC, nil, 0xffffffff)
local f_general_CmSwVersion = ProtoField.uint32("vwcamper.general.CmSwVersion", "CmSwVersion", base.DEC, nil, 0xffffffff)
local f_general_CommunicationVersion = ProtoField.uint8("vwcamper.general.CommunicationVersion", "CommunicationVersion", base.DEC, nil, 0xff)

-- generalpurposesignals (state char 0000f001-6c77-4b7d-bbf6-a5e587701f3d)
local f_generalpurposesignals_BitZeroEight = ProtoField.bool("vwcamper.generalpurposesignals.BitZeroEight", "BitZeroEight", 8, nil, 0x80)
local f_generalpurposesignals_BitZeroSeven = ProtoField.bool("vwcamper.generalpurposesignals.BitZeroSeven", "BitZeroSeven", 8, nil, 0x40)
local f_generalpurposesignals_BitZeroSix = ProtoField.bool("vwcamper.generalpurposesignals.BitZeroSix", "BitZeroSix", 8, nil, 0x20)
local f_generalpurposesignals_BitZeroFive = ProtoField.bool("vwcamper.generalpurposesignals.BitZeroFive", "BitZeroFive", 8, nil, 0x10)
local f_generalpurposesignals_BitZeroFour = ProtoField.bool("vwcamper.generalpurposesignals.BitZeroFour", "BitZeroFour", 8, nil, 0x08)
local f_generalpurposesignals_BitZeroThree = ProtoField.bool("vwcamper.generalpurposesignals.BitZeroThree", "BitZeroThree", 8, nil, 0x04)
local f_generalpurposesignals_BitZeroTwo = ProtoField.bool("vwcamper.generalpurposesignals.BitZeroTwo", "BitZeroTwo", 8, nil, 0x02)
local f_generalpurposesignals_BitZeroOne = ProtoField.bool("vwcamper.generalpurposesignals.BitZeroOne", "BitZeroOne", 8, nil, 0x01)
local f_generalpurposesignals_ByteZeroOne = ProtoField.uint8("vwcamper.generalpurposesignals.ByteZeroOne", "ByteZeroOne", base.DEC, nil, 0xff)
local f_generalpurposesignals_ByteZeroTwo = ProtoField.uint8("vwcamper.generalpurposesignals.ByteZeroTwo", "ByteZeroTwo", base.DEC, nil, 0xff)
local f_generalpurposesignals_ByteZeroThree = ProtoField.uint8("vwcamper.generalpurposesignals.ByteZeroThree", "ByteZeroThree", base.DEC, nil, 0xff)
local f_generalpurposesignals_ByteZeroFour = ProtoField.uint8("vwcamper.generalpurposesignals.ByteZeroFour", "ByteZeroFour", base.DEC, nil, 0xff)
local f_generalpurposesignals_ByteZeroFive = ProtoField.uint8("vwcamper.generalpurposesignals.ByteZeroFive", "ByteZeroFive", base.DEC, nil, 0xff)
local f_generalpurposesignals_ByteZeroSix = ProtoField.uint8("vwcamper.generalpurposesignals.ByteZeroSix", "ByteZeroSix", base.DEC, nil, 0xff)
local f_generalpurposesignals_ByteZeroSeven = ProtoField.uint8("vwcamper.generalpurposesignals.ByteZeroSeven", "ByteZeroSeven", base.DEC, nil, 0xff)
local f_generalpurposesignals_ByteZeroEight = ProtoField.uint8("vwcamper.generalpurposesignals.ByteZeroEight", "ByteZeroEight", base.DEC, nil, 0xff)
local f_generalpurposesignals_WordZeroOne = ProtoField.uint16("vwcamper.generalpurposesignals.WordZeroOne", "WordZeroOne", base.DEC, nil, 0xffff)
local f_generalpurposesignals_WordZeroTwo = ProtoField.uint16("vwcamper.generalpurposesignals.WordZeroTwo", "WordZeroTwo", base.DEC, nil, 0xffff)
local f_generalpurposesignals_WordZeroThree = ProtoField.uint16("vwcamper.generalpurposesignals.WordZeroThree", "WordZeroThree", base.DEC, nil, 0xffff)
local f_generalpurposesignals_WordZeroFour = ProtoField.uint16("vwcamper.generalpurposesignals.WordZeroFour", "WordZeroFour", base.DEC, nil, 0xffff)
local f_generalpurposesignals_DwordZeroOne = ProtoField.uint32("vwcamper.generalpurposesignals.DwordZeroOne", "DwordZeroOne", base.DEC, nil, 0xffffffff)

-- lighting (state char 00001502-6c77-4b7d-bbf6-a5e587701f3d)
local f_lighting_ProfileNumber = ProtoField.uint8("vwcamper.lighting.ProfileNumber", "ProfileNumber", base.DEC, nil, 0x0f)
local f_lighting_Mode = ProtoField.uint8("vwcamper.lighting.Mode", "Mode", base.DEC, nil, 0xff)
local f_lighting_Timestamp = ProtoField.uint32("vwcamper.lighting.Timestamp", "Timestamp", base.DEC, nil, 0xffffffff)
local f_lighting_LightValue = ProtoField.uint16("vwcamper.lighting.LightValue", "LightValue", base.DEC, nil, 0xffff)
local f_lighting_BrightnessLTwo = ProtoField.uint8("vwcamper.lighting.BrightnessLTwo", "BrightnessLTwo", base.DEC, nil, 0xf0)
local f_lighting_BrightnessLOne = ProtoField.uint8("vwcamper.lighting.BrightnessLOne", "BrightnessLOne", base.DEC, nil, 0x0f)
local f_lighting_BrightnessLFour = ProtoField.uint8("vwcamper.lighting.BrightnessLFour", "BrightnessLFour", base.DEC, nil, 0xf0)
local f_lighting_BrightnessLThree = ProtoField.uint8("vwcamper.lighting.BrightnessLThree", "BrightnessLThree", base.DEC, nil, 0x0f)
local f_lighting_BrightnessLSix = ProtoField.uint8("vwcamper.lighting.BrightnessLSix", "BrightnessLSix", base.DEC, nil, 0xf0)
local f_lighting_BrightnessLFive = ProtoField.uint8("vwcamper.lighting.BrightnessLFive", "BrightnessLFive", base.DEC, nil, 0x0f)
local f_lighting_BrightnessLEight = ProtoField.uint8("vwcamper.lighting.BrightnessLEight", "BrightnessLEight", base.DEC, nil, 0xf0)
local f_lighting_BrightnessLSeven = ProtoField.uint8("vwcamper.lighting.BrightnessLSeven", "BrightnessLSeven", base.DEC, nil, 0x0f)
local f_lighting_BrightnessLOneZero = ProtoField.uint8("vwcamper.lighting.BrightnessLOneZero", "BrightnessLOneZero", base.DEC, nil, 0xf0)
local f_lighting_BrightnessLNine = ProtoField.uint8("vwcamper.lighting.BrightnessLNine", "BrightnessLNine", base.DEC, nil, 0x0f)
local f_lighting_BrightnessLOneTwo = ProtoField.uint8("vwcamper.lighting.BrightnessLOneTwo", "BrightnessLOneTwo", base.DEC, nil, 0xf0)
local f_lighting_BrightnessLOneOne = ProtoField.uint8("vwcamper.lighting.BrightnessLOneOne", "BrightnessLOneOne", base.DEC, nil, 0x0f)
local f_lighting_BrightnessLOneFour = ProtoField.uint8("vwcamper.lighting.BrightnessLOneFour", "BrightnessLOneFour", base.DEC, nil, 0xf0)
local f_lighting_BrightnessLOneThree = ProtoField.uint8("vwcamper.lighting.BrightnessLOneThree", "BrightnessLOneThree", base.DEC, nil, 0x0f)
local f_lighting_BrightnessLOneSix = ProtoField.uint8("vwcamper.lighting.BrightnessLOneSix", "BrightnessLOneSix", base.DEC, nil, 0xf0)
local f_lighting_BrightnessLOneFive = ProtoField.uint8("vwcamper.lighting.BrightnessLOneFive", "BrightnessLOneFive", base.DEC, nil, 0x0f)

-- livingroomheater (state char 00002102-6c77-4b7d-bbf6-a5e587701f3d)
local f_livingroomheater_Variant = ProtoField.uint8("vwcamper.livingroomheater.Variant", "Variant", base.DEC, nil, 0xc0)
local f_livingroomheater_Installed = ProtoField.bool("vwcamper.livingroomheater.Installed", "Installed", 8, nil, 0x08)
local f_livingroomheater_TemperatureWater = ProtoField.bool("vwcamper.livingroomheater.TemperatureWater", "TemperatureWater", 8, nil, 0x04)
local f_livingroomheater_StateWater = ProtoField.bool("vwcamper.livingroomheater.StateWater", "StateWater", 8, nil, 0x02)
local f_livingroomheater_StateAir = ProtoField.bool("vwcamper.livingroomheater.StateAir", "StateAir", 8, nil, 0x01)
local f_livingroomheater_Error = ProtoField.uint8("vwcamper.livingroomheater.Error", "Error", base.DEC, nil, 0xf0)
local f_livingroomheater_Mode = ProtoField.uint8("vwcamper.livingroomheater.Mode", "Mode", base.DEC, nil, 0x0f)
local f_livingroomheater_TemperatureAir = ProtoField.uint8("vwcamper.livingroomheater.TemperatureAir", "TemperatureAir", base.DEC, nil, 0xff)

-- roof (state char 00001402-6c77-4b7d-bbf6-a5e587701f3d)
local f_roof_Position = ProtoField.uint8("vwcamper.roof.Position", "Position", base.DEC, nil, 0xf0)
local f_roof_Installed = ProtoField.bool("vwcamper.roof.Installed", "Installed", 8, nil, 0x02)
local f_roof_SafetyCounterValid = ProtoField.bool("vwcamper.roof.SafetyCounterValid", "SafetyCounterValid", 8, nil, 0x01)
local f_roof_InfoPopUp = ProtoField.uint8("vwcamper.roof.InfoPopUp", "InfoPopUp", base.DEC, nil, 0x0f)

-- roofaircondition (state char 00002002-6c77-4b7d-bbf6-a5e587701f3d)
local f_roofaircondition_Error = ProtoField.uint8("vwcamper.roofaircondition.Error", "Error", base.DEC, nil, 0xc0)
local f_roofaircondition_Mode = ProtoField.uint8("vwcamper.roofaircondition.Mode", "Mode", base.DEC, nil, 0x30)
local f_roofaircondition_Installed = ProtoField.bool("vwcamper.roofaircondition.Installed", "Installed", 8, nil, 0x02)
local f_roofaircondition_State = ProtoField.bool("vwcamper.roofaircondition.State", "State", 8, nil, 0x01)
local f_roofaircondition_Fanspeed = ProtoField.uint8("vwcamper.roofaircondition.Fanspeed", "Fanspeed", base.DEC, nil, 0x0f)
local f_roofaircondition_Temperature = ProtoField.uint8("vwcamper.roofaircondition.Temperature", "Temperature", base.DEC, nil, 0xff)

-- satelliteantenna (state char 00001902-6c77-4b7d-bbf6-a5e587701f3d)
local f_satelliteantenna_Error = ProtoField.uint8("vwcamper.satelliteantenna.Error", "Error", base.DEC, nil, 0xf0)
local f_satelliteantenna_System = ProtoField.uint8("vwcamper.satelliteantenna.System", "System", base.DEC, nil, 0x0c)
local f_satelliteantenna_Installed = ProtoField.bool("vwcamper.satelliteantenna.Installed", "Installed", 8, nil, 0x01)
local f_satelliteantenna_SatelliteSelection = ProtoField.uint8("vwcamper.satelliteantenna.SatelliteSelection", "SatelliteSelection", base.DEC, nil, 0xf0)
local f_satelliteantenna_Dish = ProtoField.uint8("vwcamper.satelliteantenna.Dish", "Dish", base.DEC, nil, 0x0f)
local f_satelliteantenna_SignalLevel = ProtoField.uint8("vwcamper.satelliteantenna.SignalLevel", "SignalLevel", base.DEC, nil, 0xff)

-- stairs (state char 00001802-6c77-4b7d-bbf6-a5e587701f3d)
local f_stairs_InfoPopUp = ProtoField.uint8("vwcamper.stairs.InfoPopUp", "InfoPopUp", base.DEC, nil, 0x30)
local f_stairs_Sensor = ProtoField.bool("vwcamper.stairs.Sensor", "Sensor", 8, nil, 0x08)
local f_stairs_State = ProtoField.bool("vwcamper.stairs.State", "State", 8, nil, 0x04)
local f_stairs_OperationMode = ProtoField.bool("vwcamper.stairs.OperationMode", "OperationMode", 8, nil, 0x02)
local f_stairs_Installed = ProtoField.bool("vwcamper.stairs.Installed", "Installed", 8, nil, 0x01)

-- vehicle (state char 00001004-6c77-4b7d-bbf6-a5e587701f3d)
local f_vehicle_CarVariant = ProtoField.uint8("vwcamper.vehicle.CarVariant", "CarVariant", base.DEC, nil, 0xf0)
local f_vehicle_CarLevelPopUp = ProtoField.uint8("vwcamper.vehicle.CarLevelPopUp", "CarLevelPopUp", base.DEC, nil, 0x0c)
local f_vehicle_TerminalOneFive = ProtoField.bool("vwcamper.vehicle.TerminalOneFive", "TerminalOneFive", 8, nil, 0x01)
local f_vehicle_CarTimeYear = ProtoField.uint8("vwcamper.vehicle.CarTimeYear", "CarTimeYear", base.DEC, nil, 0xff)
local f_vehicle_CarTimeMonth = ProtoField.uint8("vwcamper.vehicle.CarTimeMonth", "CarTimeMonth", base.DEC, nil, 0xff)
local f_vehicle_CarTimeDay = ProtoField.uint8("vwcamper.vehicle.CarTimeDay", "CarTimeDay", base.DEC, nil, 0xff)
local f_vehicle_CarTimeHour = ProtoField.uint8("vwcamper.vehicle.CarTimeHour", "CarTimeHour", base.DEC, nil, 0xff)
local f_vehicle_CarTimeMinute = ProtoField.uint8("vwcamper.vehicle.CarTimeMinute", "CarTimeMinute", base.DEC, nil, 0xff)
local f_vehicle_CarTimeSecond = ProtoField.uint8("vwcamper.vehicle.CarTimeSecond", "CarTimeSecond", base.DEC, nil, 0xff)
local f_vehicle_CarLevelRoll = ProtoField.int16("vwcamper.vehicle.CarLevelRoll", "CarLevelRoll", base.DEC, nil, 0xffff)
local f_vehicle_CarLevelPitch = ProtoField.int16("vwcamper.vehicle.CarLevelPitch", "CarLevelPitch", base.DEC, nil, 0xffff)

-- water (state char 00001302-6c77-4b7d-bbf6-a5e587701f3d)
local f_water_FreshWaterInfoPopUp = ProtoField.uint8("vwcamper.water.FreshWaterInfoPopUp", "FreshWaterInfoPopUp", base.DEC, nil, 0xf0)
local f_water_Installed = ProtoField.bool("vwcamper.water.Installed", "Installed", 8, nil, 0x02)
local f_water_FreshWaterUnit = ProtoField.bool("vwcamper.water.FreshWaterUnit", "FreshWaterUnit", 8, nil, 0x01)
local f_water_FreshWaterLevel = ProtoField.uint8("vwcamper.water.FreshWaterLevel", "FreshWaterLevel", base.DEC, nil, 0xff)
local f_water_FreshWaterVolume = ProtoField.uint8("vwcamper.water.FreshWaterVolume", "FreshWaterVolume", base.DEC, nil, 0xff)
local f_water_WasteWaterInfoPopUp = ProtoField.uint8("vwcamper.water.WasteWaterInfoPopUp", "WasteWaterInfoPopUp", base.DEC, nil, 0x30)
local f_water_WasteWaterUnit = ProtoField.bool("vwcamper.water.WasteWaterUnit", "WasteWaterUnit", 8, nil, 0x01)
local f_water_WasteWaterLevel = ProtoField.uint8("vwcamper.water.WasteWaterLevel", "WasteWaterLevel", base.DEC, nil, 0xff)
local f_water_WasteWaterVolume = ProtoField.uint8("vwcamper.water.WasteWaterVolume", "WasteWaterVolume", base.DEC, nil, 0xff)

vwcamper_proto.fields = {
    f_airheater_AirDistribution,
    f_airheater_Installed,
    f_airheater_FaultTriggerBit,
    f_airheater_PermanentOperation,
    f_airheater_NormalOperation,
    f_airheater_PermanentOperationRequestConfirmation,
    f_airheater_HeatingLevel,
    f_airheater_ErrorCode,
    f_airheater_OperationModeCombined,
    f_airheater_OperationModeAirHeater,
    f_airheater_RunningTime,
    f_airheater_TimerHour,
    f_airheater_TimerMin,
    f_airheater_RunningTimeinAction,
    f_campingmode_Installed,
    f_campingmode_Enable,
    f_campingmode_InteriorLight,
    f_campingmode_OutsideLight,
    f_campingmode_UsbCharger,
    f_campingmode_State,
    f_cooler_Error,
    f_cooler_NightTimerSet,
    f_cooler_Installed,
    f_cooler_TimerElapsed,
    f_cooler_TimerState,
    f_cooler_State,
    f_cooler_Mode,
    f_cooler_Level,
    f_cooler_TimerHourSet,
    f_cooler_TimerMinSet,
    f_cooler_TimerCounterHour,
    f_cooler_TimerCounterMin,
    f_cooler_NightTimerHourOn,
    f_cooler_NightTimerHourOff,
    f_energy_LandNotAvailable,
    f_energy_LandDefect,
    f_energy_EnergyModeNotSelectable,
    f_energy_DcdcDefect,
    f_energy_WarningLevelActive,
    f_energy_TwoBattSwitchAtWorkshop,
    f_energy_TwoBattSwitchAtCharging,
    f_energy_TwoBattNotCharged,
    f_energy_DcdcInstalled,
    f_energy_LadInstalled,
    f_energy_PvInstalled,
    f_energy_EmpInstalled,
    f_energy_SystemError,
    f_energy_CurrentDeratingTemperature,
    f_energy_SleepWarning,
    f_energy_PvDefect,
    f_energy_SocOneBattAfs,
    f_energy_EnergyMode,
    f_energy_WarningLevelTwo,
    f_energy_StateDcdcAfs,
    f_energy_SocTwoBattAfs,
    f_energy_StatePvAfs,
    f_energy_StateLandAfs,
    f_energy_AgeOneBattValuesMinutes,
    f_energy_IOneBattBemAfs,
    f_energy_PDcdcAfs,
    f_energy_PLandAfs,
    f_energy_PPvAfs,
    f_energy_tTwoBattRemainingh,
    f_energy_tTwoBattRemainingmin,
    f_energy_UOneBattBemAfs,
    f_energy_UTwoBattBemAfs,
    f_energy_ITwoBattBemAfs,
    f_energy_IDcdcAfs,
    f_energy_ILandAfs,
    f_energy_IPvAfs,
    f_general_AmbSwVersion,
    f_general_CmSwVersion,
    f_general_CommunicationVersion,
    f_generalpurposesignals_BitZeroEight,
    f_generalpurposesignals_BitZeroSeven,
    f_generalpurposesignals_BitZeroSix,
    f_generalpurposesignals_BitZeroFive,
    f_generalpurposesignals_BitZeroFour,
    f_generalpurposesignals_BitZeroThree,
    f_generalpurposesignals_BitZeroTwo,
    f_generalpurposesignals_BitZeroOne,
    f_generalpurposesignals_ByteZeroOne,
    f_generalpurposesignals_ByteZeroTwo,
    f_generalpurposesignals_ByteZeroThree,
    f_generalpurposesignals_ByteZeroFour,
    f_generalpurposesignals_ByteZeroFive,
    f_generalpurposesignals_ByteZeroSix,
    f_generalpurposesignals_ByteZeroSeven,
    f_generalpurposesignals_ByteZeroEight,
    f_generalpurposesignals_WordZeroOne,
    f_generalpurposesignals_WordZeroTwo,
    f_generalpurposesignals_WordZeroThree,
    f_generalpurposesignals_WordZeroFour,
    f_generalpurposesignals_DwordZeroOne,
    f_lighting_ProfileNumber,
    f_lighting_Mode,
    f_lighting_Timestamp,
    f_lighting_LightValue,
    f_lighting_BrightnessLTwo,
    f_lighting_BrightnessLOne,
    f_lighting_BrightnessLFour,
    f_lighting_BrightnessLThree,
    f_lighting_BrightnessLSix,
    f_lighting_BrightnessLFive,
    f_lighting_BrightnessLEight,
    f_lighting_BrightnessLSeven,
    f_lighting_BrightnessLOneZero,
    f_lighting_BrightnessLNine,
    f_lighting_BrightnessLOneTwo,
    f_lighting_BrightnessLOneOne,
    f_lighting_BrightnessLOneFour,
    f_lighting_BrightnessLOneThree,
    f_lighting_BrightnessLOneSix,
    f_lighting_BrightnessLOneFive,
    f_livingroomheater_Variant,
    f_livingroomheater_Installed,
    f_livingroomheater_TemperatureWater,
    f_livingroomheater_StateWater,
    f_livingroomheater_StateAir,
    f_livingroomheater_Error,
    f_livingroomheater_Mode,
    f_livingroomheater_TemperatureAir,
    f_roof_Position,
    f_roof_Installed,
    f_roof_SafetyCounterValid,
    f_roof_InfoPopUp,
    f_roofaircondition_Error,
    f_roofaircondition_Mode,
    f_roofaircondition_Installed,
    f_roofaircondition_State,
    f_roofaircondition_Fanspeed,
    f_roofaircondition_Temperature,
    f_satelliteantenna_Error,
    f_satelliteantenna_System,
    f_satelliteantenna_Installed,
    f_satelliteantenna_SatelliteSelection,
    f_satelliteantenna_Dish,
    f_satelliteantenna_SignalLevel,
    f_stairs_InfoPopUp,
    f_stairs_Sensor,
    f_stairs_State,
    f_stairs_OperationMode,
    f_stairs_Installed,
    f_vehicle_CarVariant,
    f_vehicle_CarLevelPopUp,
    f_vehicle_TerminalOneFive,
    f_vehicle_CarTimeYear,
    f_vehicle_CarTimeMonth,
    f_vehicle_CarTimeDay,
    f_vehicle_CarTimeHour,
    f_vehicle_CarTimeMinute,
    f_vehicle_CarTimeSecond,
    f_vehicle_CarLevelRoll,
    f_vehicle_CarLevelPitch,
    f_water_FreshWaterInfoPopUp,
    f_water_Installed,
    f_water_FreshWaterUnit,
    f_water_FreshWaterLevel,
    f_water_FreshWaterVolume,
    f_water_WasteWaterInfoPopUp,
    f_water_WasteWaterUnit,
    f_water_WasteWaterLevel,
    f_water_WasteWaterVolume,
}

-- characteristic UUID -> {name, fields} used by the dissector below -------------
local char_table = {
    ["00001702-6c77-4b7d-bbf6-a5e587701f3d"] = {
        name = "airheater",
        fields = {
            { field = f_airheater_AirDistribution, byte = 0, len = 1 },
            { field = f_airheater_Installed, byte = 0, len = 1 },
            { field = f_airheater_FaultTriggerBit, byte = 0, len = 1 },
            { field = f_airheater_PermanentOperation, byte = 0, len = 1 },
            { field = f_airheater_NormalOperation, byte = 0, len = 1 },
            { field = f_airheater_PermanentOperationRequestConfirmation, byte = 0, len = 1 },
            { field = f_airheater_HeatingLevel, byte = 1, len = 1 },
            { field = f_airheater_ErrorCode, byte = 1, len = 1 },
            { field = f_airheater_OperationModeCombined, byte = 2, len = 1 },
            { field = f_airheater_OperationModeAirHeater, byte = 2, len = 1 },
            { field = f_airheater_RunningTime, byte = 3, len = 1 },
            { field = f_airheater_TimerHour, byte = 4, len = 1 },
            { field = f_airheater_TimerMin, byte = 5, len = 1 },
            { field = f_airheater_RunningTimeinAction, byte = 6, len = 1 },
        },
    },
    ["00001202-6c77-4b7d-bbf6-a5e587701f3d"] = {
        name = "campingmode",
        fields = {
            { field = f_campingmode_Installed, byte = 0, len = 1 },
            { field = f_campingmode_Enable, byte = 0, len = 1 },
            { field = f_campingmode_InteriorLight, byte = 0, len = 1 },
            { field = f_campingmode_OutsideLight, byte = 0, len = 1 },
            { field = f_campingmode_UsbCharger, byte = 0, len = 1 },
            { field = f_campingmode_State, byte = 0, len = 1 },
        },
    },
    ["00001102-6c77-4b7d-bbf6-a5e587701f3d"] = {
        name = "cooler",
        fields = {
            { field = f_cooler_Error, byte = 0, len = 1 },
            { field = f_cooler_NightTimerSet, byte = 0, len = 1 },
            { field = f_cooler_Installed, byte = 0, len = 1 },
            { field = f_cooler_TimerElapsed, byte = 0, len = 1 },
            { field = f_cooler_TimerState, byte = 0, len = 1 },
            { field = f_cooler_State, byte = 0, len = 1 },
            { field = f_cooler_Mode, byte = 1, len = 1 },
            { field = f_cooler_Level, byte = 1, len = 1 },
            { field = f_cooler_TimerHourSet, byte = 2, len = 1 },
            { field = f_cooler_TimerMinSet, byte = 3, len = 1 },
            { field = f_cooler_TimerCounterHour, byte = 4, len = 1 },
            { field = f_cooler_TimerCounterMin, byte = 5, len = 1 },
            { field = f_cooler_NightTimerHourOn, byte = 6, len = 1 },
            { field = f_cooler_NightTimerHourOff, byte = 7, len = 1 },
        },
    },
    ["00001602-6c77-4b7d-bbf6-a5e587701f3d"] = {
        name = "energy",
        fields = {
            { field = f_energy_LandNotAvailable, byte = 0, len = 1 },
            { field = f_energy_LandDefect, byte = 0, len = 1 },
            { field = f_energy_EnergyModeNotSelectable, byte = 0, len = 1 },
            { field = f_energy_DcdcDefect, byte = 0, len = 1 },
            { field = f_energy_WarningLevelActive, byte = 0, len = 1 },
            { field = f_energy_TwoBattSwitchAtWorkshop, byte = 0, len = 1 },
            { field = f_energy_TwoBattSwitchAtCharging, byte = 0, len = 1 },
            { field = f_energy_TwoBattNotCharged, byte = 0, len = 1 },
            { field = f_energy_DcdcInstalled, byte = 1, len = 1 },
            { field = f_energy_LadInstalled, byte = 1, len = 1 },
            { field = f_energy_PvInstalled, byte = 1, len = 1 },
            { field = f_energy_EmpInstalled, byte = 1, len = 1 },
            { field = f_energy_SystemError, byte = 1, len = 1 },
            { field = f_energy_CurrentDeratingTemperature, byte = 1, len = 1 },
            { field = f_energy_SleepWarning, byte = 1, len = 1 },
            { field = f_energy_PvDefect, byte = 1, len = 1 },
            { field = f_energy_SocOneBattAfs, byte = 2, len = 1 },
            { field = f_energy_EnergyMode, byte = 2, len = 1 },
            { field = f_energy_WarningLevelTwo, byte = 2, len = 1 },
            { field = f_energy_StateDcdcAfs, byte = 3, len = 1 },
            { field = f_energy_SocTwoBattAfs, byte = 3, len = 1 },
            { field = f_energy_StatePvAfs, byte = 4, len = 1 },
            { field = f_energy_StateLandAfs, byte = 4, len = 1 },
            { field = f_energy_AgeOneBattValuesMinutes, byte = 5, len = 1 },
            { field = f_energy_IOneBattBemAfs, byte = 6, len = 1 },
            { field = f_energy_PDcdcAfs, byte = 7, len = 1 },
            { field = f_energy_PLandAfs, byte = 8, len = 1 },
            { field = f_energy_PPvAfs, byte = 9, len = 1 },
            { field = f_energy_tTwoBattRemainingh, byte = 10, len = 1 },
            { field = f_energy_tTwoBattRemainingmin, byte = 11, len = 1 },
            { field = f_energy_UOneBattBemAfs, byte = 12, len = 1 },
            { field = f_energy_UTwoBattBemAfs, byte = 13, len = 1 },
            { field = f_energy_ITwoBattBemAfs, byte = 14, len = 2 },
            { field = f_energy_IDcdcAfs, byte = 16, len = 2 },
            { field = f_energy_ILandAfs, byte = 18, len = 2 },
            { field = f_energy_IPvAfs, byte = 20, len = 2 },
        },
    },
    ["00001001-6c77-4b7d-bbf6-a5e587701f3d"] = {
        name = "general",
        fields = {
            { field = f_general_AmbSwVersion, byte = 0, len = 4 },
            { field = f_general_CmSwVersion, byte = 4, len = 4 },
            { field = f_general_CommunicationVersion, byte = 8, len = 1 },
        },
    },
    ["0000f001-6c77-4b7d-bbf6-a5e587701f3d"] = {
        name = "generalpurposesignals",
        fields = {
            { field = f_generalpurposesignals_BitZeroEight, byte = 0, len = 1 },
            { field = f_generalpurposesignals_BitZeroSeven, byte = 0, len = 1 },
            { field = f_generalpurposesignals_BitZeroSix, byte = 0, len = 1 },
            { field = f_generalpurposesignals_BitZeroFive, byte = 0, len = 1 },
            { field = f_generalpurposesignals_BitZeroFour, byte = 0, len = 1 },
            { field = f_generalpurposesignals_BitZeroThree, byte = 0, len = 1 },
            { field = f_generalpurposesignals_BitZeroTwo, byte = 0, len = 1 },
            { field = f_generalpurposesignals_BitZeroOne, byte = 0, len = 1 },
            { field = f_generalpurposesignals_ByteZeroOne, byte = 1, len = 1 },
            { field = f_generalpurposesignals_ByteZeroTwo, byte = 2, len = 1 },
            { field = f_generalpurposesignals_ByteZeroThree, byte = 3, len = 1 },
            { field = f_generalpurposesignals_ByteZeroFour, byte = 4, len = 1 },
            { field = f_generalpurposesignals_ByteZeroFive, byte = 5, len = 1 },
            { field = f_generalpurposesignals_ByteZeroSix, byte = 6, len = 1 },
            { field = f_generalpurposesignals_ByteZeroSeven, byte = 7, len = 1 },
            { field = f_generalpurposesignals_ByteZeroEight, byte = 8, len = 1 },
            { field = f_generalpurposesignals_WordZeroOne, byte = 9, len = 2 },
            { field = f_generalpurposesignals_WordZeroTwo, byte = 11, len = 2 },
            { field = f_generalpurposesignals_WordZeroThree, byte = 13, len = 2 },
            { field = f_generalpurposesignals_WordZeroFour, byte = 15, len = 2 },
            { field = f_generalpurposesignals_DwordZeroOne, byte = 17, len = 4 },
        },
    },
    ["00001502-6c77-4b7d-bbf6-a5e587701f3d"] = {
        name = "lighting",
        fields = {
            { field = f_lighting_ProfileNumber, byte = 0, len = 1 },
            { field = f_lighting_Mode, byte = 1, len = 1 },
            { field = f_lighting_Timestamp, byte = 2, len = 4 },
            { field = f_lighting_LightValue, byte = 6, len = 2 },
            { field = f_lighting_BrightnessLTwo, byte = 8, len = 1 },
            { field = f_lighting_BrightnessLOne, byte = 8, len = 1 },
            { field = f_lighting_BrightnessLFour, byte = 9, len = 1 },
            { field = f_lighting_BrightnessLThree, byte = 9, len = 1 },
            { field = f_lighting_BrightnessLSix, byte = 10, len = 1 },
            { field = f_lighting_BrightnessLFive, byte = 10, len = 1 },
            { field = f_lighting_BrightnessLEight, byte = 11, len = 1 },
            { field = f_lighting_BrightnessLSeven, byte = 11, len = 1 },
            { field = f_lighting_BrightnessLOneZero, byte = 12, len = 1 },
            { field = f_lighting_BrightnessLNine, byte = 12, len = 1 },
            { field = f_lighting_BrightnessLOneTwo, byte = 13, len = 1 },
            { field = f_lighting_BrightnessLOneOne, byte = 13, len = 1 },
            { field = f_lighting_BrightnessLOneFour, byte = 14, len = 1 },
            { field = f_lighting_BrightnessLOneThree, byte = 14, len = 1 },
            { field = f_lighting_BrightnessLOneSix, byte = 15, len = 1 },
            { field = f_lighting_BrightnessLOneFive, byte = 15, len = 1 },
        },
    },
    ["00002102-6c77-4b7d-bbf6-a5e587701f3d"] = {
        name = "livingroomheater",
        fields = {
            { field = f_livingroomheater_Variant, byte = 0, len = 1 },
            { field = f_livingroomheater_Installed, byte = 0, len = 1 },
            { field = f_livingroomheater_TemperatureWater, byte = 0, len = 1 },
            { field = f_livingroomheater_StateWater, byte = 0, len = 1 },
            { field = f_livingroomheater_StateAir, byte = 0, len = 1 },
            { field = f_livingroomheater_Error, byte = 1, len = 1 },
            { field = f_livingroomheater_Mode, byte = 1, len = 1 },
            { field = f_livingroomheater_TemperatureAir, byte = 2, len = 1 },
        },
    },
    ["00001402-6c77-4b7d-bbf6-a5e587701f3d"] = {
        name = "roof",
        fields = {
            { field = f_roof_Position, byte = 0, len = 1 },
            { field = f_roof_Installed, byte = 0, len = 1 },
            { field = f_roof_SafetyCounterValid, byte = 0, len = 1 },
            { field = f_roof_InfoPopUp, byte = 1, len = 1 },
        },
    },
    ["00002002-6c77-4b7d-bbf6-a5e587701f3d"] = {
        name = "roofaircondition",
        fields = {
            { field = f_roofaircondition_Error, byte = 0, len = 1 },
            { field = f_roofaircondition_Mode, byte = 0, len = 1 },
            { field = f_roofaircondition_Installed, byte = 0, len = 1 },
            { field = f_roofaircondition_State, byte = 0, len = 1 },
            { field = f_roofaircondition_Fanspeed, byte = 1, len = 1 },
            { field = f_roofaircondition_Temperature, byte = 2, len = 1 },
        },
    },
    ["00001902-6c77-4b7d-bbf6-a5e587701f3d"] = {
        name = "satelliteantenna",
        fields = {
            { field = f_satelliteantenna_Error, byte = 0, len = 1 },
            { field = f_satelliteantenna_System, byte = 0, len = 1 },
            { field = f_satelliteantenna_Installed, byte = 0, len = 1 },
            { field = f_satelliteantenna_SatelliteSelection, byte = 1, len = 1 },
            { field = f_satelliteantenna_Dish, byte = 1, len = 1 },
            { field = f_satelliteantenna_SignalLevel, byte = 2, len = 1 },
        },
    },
    ["00001802-6c77-4b7d-bbf6-a5e587701f3d"] = {
        name = "stairs",
        fields = {
            { field = f_stairs_InfoPopUp, byte = 0, len = 1 },
            { field = f_stairs_Sensor, byte = 0, len = 1 },
            { field = f_stairs_State, byte = 0, len = 1 },
            { field = f_stairs_OperationMode, byte = 0, len = 1 },
            { field = f_stairs_Installed, byte = 0, len = 1 },
        },
    },
    ["00001004-6c77-4b7d-bbf6-a5e587701f3d"] = {
        name = "vehicle",
        fields = {
            { field = f_vehicle_CarVariant, byte = 0, len = 1 },
            { field = f_vehicle_CarLevelPopUp, byte = 0, len = 1 },
            { field = f_vehicle_TerminalOneFive, byte = 0, len = 1 },
            { field = f_vehicle_CarTimeYear, byte = 1, len = 1 },
            { field = f_vehicle_CarTimeMonth, byte = 2, len = 1 },
            { field = f_vehicle_CarTimeDay, byte = 3, len = 1 },
            { field = f_vehicle_CarTimeHour, byte = 4, len = 1 },
            { field = f_vehicle_CarTimeMinute, byte = 5, len = 1 },
            { field = f_vehicle_CarTimeSecond, byte = 6, len = 1 },
            { field = f_vehicle_CarLevelRoll, byte = 7, len = 2 },
            { field = f_vehicle_CarLevelPitch, byte = 9, len = 2 },
        },
    },
    ["00001302-6c77-4b7d-bbf6-a5e587701f3d"] = {
        name = "water",
        fields = {
            { field = f_water_FreshWaterInfoPopUp, byte = 0, len = 1 },
            { field = f_water_Installed, byte = 0, len = 1 },
            { field = f_water_FreshWaterUnit, byte = 0, len = 1 },
            { field = f_water_FreshWaterLevel, byte = 1, len = 1 },
            { field = f_water_FreshWaterVolume, byte = 2, len = 1 },
            { field = f_water_WasteWaterInfoPopUp, byte = 3, len = 1 },
            { field = f_water_WasteWaterUnit, byte = 3, len = 1 },
            { field = f_water_WasteWaterLevel, byte = 4, len = 1 },
            { field = f_water_WasteWaterVolume, byte = 5, len = 1 },
        },
    },
}

-- Dissection: best-effort. There's no single documented Wireshark dissector table
-- keyed on a BTATT 128-bit characteristic UUID across all Wireshark versions, so we
-- try the most likely one and fall back to no-op registration (pcall-guarded).
local function char_uuid_from_pinfo()
    local ok, f = pcall(Field.new, "bluetooth.uuid128")
    if not ok or not f then
        return nil
    end
    local finfo = f()
    if not finfo then
        return nil
    end
    return tostring(finfo.value):lower()
end

function vwcamper_proto.dissector(buffer, pinfo, tree)
    local uuid = char_uuid_from_pinfo()
    local spec = uuid and char_table[uuid]
    if not spec then
        return
    end
    pinfo.cols.protocol = "VWCAMPER"
    local subtree = tree:add(vwcamper_proto, buffer(), "VW Camper " .. spec.name .. " state")
    for _, entry in ipairs(spec.fields) do
        if buffer:len() >= entry.byte + entry.len then
            subtree:add(entry.field, buffer(entry.byte, entry.len))
        end
    end
end

-- Best-effort registration against a UUID128 dissector table, if one exists in this
-- Wireshark build; harmless no-op otherwise.
local uuid_table = DissectorTable.get("bluetooth.uuid128") or DissectorTable.get("btatt.uuid128")
if uuid_table then
    for uuid, _ in pairs(char_table) do
        pcall(function() uuid_table:add(uuid, vwcamper_proto) end)
    end
end
