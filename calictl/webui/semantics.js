// @ts-check
// semantics.js — the browser twin of calictl/semantics.py for the ESP32 satellite (#154).
//
// The satellite's GET /api/state is RAW decoded fields ({t, fn, device}); calictl's is interpreted
// ({<fn>: leaves…, _meta}). adaptSatellite() turns the first into the second, so app.js renders both
// with the same code. PYTHON IS THE AUTHORITY: each function transliterates the Python named in its
// comment, and tests/test_semantics_js_parity.py holds them equal over tests/vectors/semantics.json
// (python3 -m tools.gen_semantics_vectors). Change both together. Only the functions the web UI
// renders are twinned; any other function gets semGeneric()'s raw view.
// A classic script loaded before app.js (shared global scope): every top-level name is public
// (SAT_OFFLINE_S, pyRound, interpret, applySwCorrections, anchorsCheck, firmwareMeta, isSatelliteBody,
// adaptSatellite) or sem*-prefixed, so nothing collides with app.js.

/** @typedef {Record<string, number>} Fields  one function's decoded fields (calictl.protocol.decode) */
/** @typedef {Record<string, any>} Interp  one function's interpreted leaves */

/** Seconds without a snapshot after which the satellite page reads offline ("van asleep"): three
 * of the session's held-link water re-read periods (3 * CALI_SESSION_WATER_REREAD_MS / 1000,
 * cali_session.h — tests pin it), so a link momentarily down with seconds-old data is NOT "van
 * asleep" — the banner keys on DATA AGE, never on link state. (The CoreS3's own screen keeps its 10 s red tint: DISPLAY_STALE_MS in
 * display_model.c is the device's link-freshness cue, a different thing.) */
const SAT_OFFLINE_S = 90;

/**
 * Python `d.get(k, dflt)`: the default only when the key is ABSENT (a short frame drops trailing fields).
 * @param {Fields} d @param {string} k @param {any} [dflt] @returns {any}
 */
function semGet(d, k, dflt = null) {
  return Object.prototype.hasOwnProperty.call(d, k) ? d[k] : dflt;
}

/**
 * Python `table.get(v, dflt)` for the int-keyed enum tables (None/null never matches).
 * @param {Record<number, string>} map @param {number|null} v @param {string|null} [dflt] @returns {string|null}
 */
function semLookup(map, v, dflt = null) {
  return v !== null && Object.prototype.hasOwnProperty.call(map, v) ? map[v] : dflt;
}

/** semantics.py:20 _signed(). @param {number} v @param {number} width @returns {number} */
function semSigned(v, width) {
  return v >= 2 ** (width - 1) ? v - 2 ** width : v;
}

/** semantics.py:24 _pct(): Python `//` floors. @param {number} cur @param {number} cap @returns {number|null} */
function semPct(cur, cap) {
  return cap ? Math.floor((cur * 100) / cap) : null;
}

/**
 * Python's round(x, nd) on a float: the double's EXACT decimal value rounded to nd digits, an exact
 * tie going to the even digit. toFixed() rounds the exact value too (ES spec) but breaks a tie away
 * from zero; Math.round(x * 10 ** nd) is wrong for both (12.35 * 10 is 123.50000000000001).
 * @param {number} x @param {number} nd @returns {number}
 */
function pyRound(x, nd) {
  const a = Math.abs(x);
  if (!Number.isFinite(a) || a >= 1e21) return x;
  const s = a.toFixed(100);                     // exact decimal expansion for every value we round
  const dot = s.indexOf(".");
  let r = Number(a.toFixed(nd));
  if (/^50*$/.test(s.slice(dot + 1 + nd))) {    // an exact tie: keep the even digit, like Python
    const kept = s.slice(0, dot + 1 + nd);       // truncated toward zero, e.g. "2." or "0.12"
    if (Number(kept.replace(".", "").slice(-1)) % 2 === 0) r = Number(kept);
  }
  return x < 0 ? -r : r;
}

/** Python str(float) for the anchor texts: "91.0", not "91". @param {number} v @returns {string} */
function semFloatStr(v) {
  return Number.isInteger(v) ? v.toFixed(1) : String(v);
}

// --- water (semantics.py:30) -----------------------------------------------------------------
const SEM_FRESH_WATER_ALERT = { 1: "pump_protection", 2: "sensor_error", 3: "error", 7: "error", 4: "pump_error", 5: "empty" }; // :55
const SEM_WASTE_WATER_ALERT = { 1: "full", 2: "sensor_error", 3: "error" }; // :63

/** semantics.py:31 water.tank(). @param {number|null} unit @param {number|null} level @param {number|null} volume @returns {Interp} */
function semTank(unit, level, volume) {
  if (level === null || volume === null) return { liters: null, capacity_l: volume, percent: null };
  if (unit) return { liters: level, capacity_l: volume, percent: semPct(level, volume) };
  return { liters: Math.floor((volume * level) / 100), capacity_l: volume, percent: level };
}

/** semantics.py:30 water(). @param {Fields} d @returns {Interp} */
function semWater(d) {
  return {
    installed: !!semGet(d, "Installed"),
    fresh: semTank(semGet(d, "FreshWaterUnit"), semGet(d, "FreshWaterLevel"), semGet(d, "FreshWaterVolume")),
    waste: semTank(semGet(d, "WasteWaterUnit"), semGet(d, "WasteWaterLevel"), semGet(d, "WasteWaterVolume")),
    fresh_alert: semLookup(SEM_FRESH_WATER_ALERT, semGet(d, "FreshWaterInfoPopUp")),
    waste_alert: semLookup(SEM_WASTE_WATER_ALERT, semGet(d, "WasteWaterInfoPopUp")),
  };
}

// --- energy (semantics.py:66) ----------------------------------------------------------------
const SEM_SRC_STATE = { 0: "inactive", 1: "active", 2: "standby", 6: "init" }; // :96, else "error"
const SEM_ENERGY_FAULTS = [ // :140-155, in order
  "SystemError", "DcdcDefect", "PvDefect", "LandDefect", "LandNotAvailable", "TwoBattNotCharged",
  "TwoBattSwitchAtCharging", "TwoBattSwitchAtWorkshop", "WarningLevelTwo", "WarningLevelActive",
  "SleepWarning", "CurrentDeratingTemperature",
];

/** semantics.py:66 energy(). @param {Fields} d @returns {Interp} */
function semEnergy(d) {
  const b1 = semGet(d, "IOneBattBemAfs");
  const b1Valid = b1 !== null && b1 !== 0x81;
  const dcdcI = !!semGet(d, "DcdcInstalled"), shoreI = !!semGet(d, "LadInstalled"), solarI = !!semGet(d, "PvInstalled");
  /** @param {any} l */
  const socPct = (l) => (Number.isInteger(l) && l >= 0 && l <= 10 ? l * 10 : null);
  /** @param {number|null} v */
  const srcState = (v) => (v === null ? null : semLookup(SEM_SRC_STATE, v, "error"));
  const soc1 = semGet(d, "SocOneBattAfs"), soc2 = semGet(d, "SocTwoBattAfs");
  return {
    installed: true,
    stale: semGet(d, "AgeOneBattValuesMinutes", 255) >= 255,
    age_min: semGet(d, "AgeOneBattValuesMinutes"),
    batt1_v: b1Valid ? pyRound(semGet(d, "UOneBattBemAfs", 0) * 0.1, 1) : null,
    batt1_current: b1Valid ? semSigned(semGet(d, "IOneBattBemAfs", 0), 8) : null,
    soc1_level: soc1,
    soc1_pct: socPct(soc1),
    batt2_v: pyRound(semGet(d, "UTwoBattBemAfs", 0) * 0.1, 1),
    batt2_current: pyRound(semSigned(semGet(d, "ITwoBattBemAfs", 0), 16) * 0.1, 1),
    soc2_level: soc2,
    soc2_pct: socPct(soc2),
    batt2_remaining_h: semGet(d, "tTwoBattRemainingh"),
    batt2_remaining_min: semGet(d, "tTwoBattRemainingmin"),
    dcdc_charging: srcState(semGet(d, "StateDcdcAfs")) === "active",
    dcdc_state: srcState(semGet(d, "StateDcdcAfs")),
    shore_state: srcState(semGet(d, "StateLandAfs")),
    solar_state: srcState(semGet(d, "StatePvAfs")),
    dcdc_power: dcdcI ? semSigned(semGet(d, "PDcdcAfs", 0), 8) * 10 : 0,
    shore_power: shoreI ? semGet(d, "PLandAfs", 0) * 10 : 0,
    solar_power: solarI ? semGet(d, "PPvAfs", 0) * 10 : 0,
    dcdc_current: dcdcI ? semSigned(semGet(d, "IDcdcAfs", 0), 16) : null,
    shore_current: shoreI ? pyRound(semGet(d, "ILandAfs", 0) * 0.1, 1) : null,
    solar_current: solarI ? pyRound(semGet(d, "IPvAfs", 0) * 0.1, 1) : null,
    dcdc_installed: dcdcI,
    shore_installed: shoreI,
    solar_installed: solarI,
    energy_mode: semGet(d, "EnergyMode"),
    energy_mode_locked: !!semGet(d, "EnergyModeNotSelectable"),
    warning_level: semGet(d, "WarningLevelTwo"),
    warning_active: !!semGet(d, "WarningLevelActive"),
    derating_temp_active: !!semGet(d, "CurrentDeratingTemperature"),
    sleep_warning: !!semGet(d, "SleepWarning"),
    faults: SEM_ENERGY_FAULTS.filter((k) => !!semGet(d, k)),
  };
}

// --- cooler (semantics.py:172) ---------------------------------------------------------------
const SEM_COOLER_FAULT = { 1: "error", 2: "emergency", 3: "door_open" }; // :166
const SEM_COOLER_QUIET = { 0: "off", 2: "manual", 4: "scheduled" };       // :169

/** semantics.py:172 cooler(). @param {Fields} d @returns {Interp} */
function semCooler(d) {
  const fault = semGet(d, "Installed") ? semLookup(SEM_COOLER_FAULT, semGet(d, "Error")) : null;
  return {
    installed: !!semGet(d, "Installed"),
    on: semGet(d, "State") === 1,
    level: semGet(d, "Level"),
    mode: semGet(d, "Mode"),
    timer_active: !!semGet(d, "TimerState"),
    quiet_mode: semLookup(SEM_COOLER_QUIET, semGet(d, "Mode")),
    quiet_scheduled: semGet(d, "Mode") === 4,
    quiet_from: semGet(d, "NightTimerHourOn"),
    quiet_to: semGet(d, "NightTimerHourOff"),
    timer_hour: semGet(d, "TimerHourSet"),
    timer_min: semGet(d, "TimerMinSet"),
    fault,
    door_open: fault === "door_open",
    error: !!fault,
  };
}

// --- airheater (semantics.py:216) ------------------------------------------------------------
const SEM_AIRHEATER_ERROR = { 1: "low_battery", 2: "low_fuel", 3: "system_error", 4: "heating_time_exceeded", 5: "not_possible", 6: "engine_running", 7: "aux_heater_active" }; // :207

/** semantics.py:216 airheater(). @param {Fields} d @returns {Interp} */
function semAirheater(d) {
  const err = semGet(d, "ErrorCode");
  return {
    installed: !!semGet(d, "Installed"),
    running: !!(semGet(d, "NormalOperation") || semGet(d, "PermanentOperation")),
    permanent: !!semGet(d, "PermanentOperation"),
    level: semGet(d, "HeatingLevel"),
    error_code: err,
    error: !err ? null : semLookup(SEM_AIRHEATER_ERROR, err, "unknown"),
    mode: semGet(d, "OperationModeAirHeater"),
    timer_armed: semGet(d, "OperationModeAirHeater") === 3,
    air_distribution: semGet(d, "AirDistribution"),
    running_time: semGet(d, "RunningTime"),
    timer_hour: semGet(d, "TimerHour"),
    timer_min: semGet(d, "TimerMin"),
    running_time_remaining: semGet(d, "RunningTimeinAction"),
  };
}

/** semantics.py:247 campingmode() — lights INVERTED + combined, USB gated by master. @param {Fields} d @returns {Interp} */
function semCampingmode(d) {
  const master = !!semGet(d, "State");
  return {
    installed: !!semGet(d, "Installed"),
    master_on: master,
    usb_charger: !!semGet(d, "UsbCharger"),
    usb_powered: master && !!semGet(d, "UsbCharger"),
    lights_on: master && semGet(d, "InteriorLight") === 0 && semGet(d, "OutsideLight") === 0,
    outputs_controllable: master,
    enable: !!semGet(d, "Enable"),
  };
}

// --- roof (semantics.py:297) -----------------------------------------------------------------
const SEM_ROOF_POS = { 0: "closed", 1: "open", 2: "middle", 14: "closed", 15: "error" }; // :271, else "other"
const SEM_ROOF_ALERT = { // :276
  1: "child_lock", 4: "error", 5: "driving", 6: "sensor_error", 7: "emergency_locked", 10: "not_possible",
  11: "low_battery", 2: "open_checklist", 9: "not_stationary", // 3/8/12 = motion progress, no alert
};

/** semantics.py:297 roof(). @param {Fields} d @returns {Interp} */
function semRoof(d) {
  const installed = !!semGet(d, "Installed");
  const pos = semGet(d, "Position");
  return {
    installed,
    position: pos,
    position_name: pos === null ? null : semLookup(SEM_ROOF_POS, pos, "other"),
    alert: installed ? semLookup(SEM_ROOF_ALERT, semGet(d, "InfoPopUp")) : null,
    safety_valid: !!semGet(d, "SafetyCounterValid"),
  };
}

// --- lighting (semantics.py:365) -------------------------------------------------------------
const SEM_LZONES = /** @type {[string, number][]} */ ([ // :335 _LZONES, in order
  ["One", 1], ["Two", 2], ["Three", 3], ["Four", 4], ["Five", 5], ["Six", 6], ["Seven", 7], ["Eight", 8],
  ["Nine", 9], ["OneZero", 10], ["OneOne", 11], ["OneTwo", 12], ["OneThree", 13], ["OneFour", 14],
  ["OneFive", 15], ["OneSix", 16],
]);

const SEM_LIGHT_CONFIG_KEYS = ["WakeupTimestamp", "WakeupLightValue", "DoorContact", "FavouritesStored"]; // LIGHT_CONFIG_KEYS

/** semantics.py lighting_config(None, d). @param {Fields} d @returns {Record<string, number>} */
function semLightingConfig(d) {
  /** @type {Record<string, number>} */
  const out = {};
  for (const k of SEM_LIGHT_CONFIG_KEYS) { const v = semGet(d, k); if (v !== null) out[k] = v; }
  const mode = semGet(d, "Mode"), pn = semGet(d, "ProfileNumber"), lv = semGet(d, "LightValue"), ts = semGet(d, "Timestamp");
  if (mode === 20 && lv !== null && ts !== null) { out.WakeupTimestamp = ts; out.WakeupLightValue = lv; }
  else if (mode === 16 && pn === 8 && lv !== null) out.DoorContact = lv;
  else if (mode === 12 && lv !== null && pn !== 13) out.FavouritesStored = lv & 0x7f;
  else if (mode === 4 && pn !== null && pn >= 1 && pn <= 7 && "FavouritesStored" in out) out.FavouritesStored |= 1 << (pn - 1);
  return out;
}

/** semantics.py wakeup_config(). @param {Record<string, number>} cfg @returns {Interp|null} */
function semWakeup(cfg) {
  const ts = cfg.WakeupTimestamp, lv = cfg.WakeupLightValue;
  if (ts === undefined || lv === undefined) return null;
  const hour = Math.floor(ts / 3600) % 24, minute = Math.floor(ts / 60) % 60;
  /** @type {number[]} */
  const areas = [];
  for (let a = 1; a <= 4; a++) if ((lv >> (7 + a)) & 1) areas.push(a);
  return {
    time: String(hour).padStart(2, "0") + ":" + String(minute).padStart(2, "0"),
    hour, minute, enabled: !!(lv & 1), ramp: ((lv & 0xf) >> 1) * 10,
    brightness: (lv >> 4) & 0xf, areas, colour: (lv >> 12) & 0xf,
  };
}

/** semantics.py:365 lighting(): any_on ignores 13 (not equipped) and 14 (leave-unchanged). @param {Fields} d @returns {Interp} */
function semLighting(d) {
  /** @type {Interp} */
  const out = { installed: true, profile: semGet(d, "ProfileNumber"), mode: semGet(d, "Mode") };
  let anyOn = false;
  for (const [suf, num] of SEM_LZONES) {
    const v = semGet(d, "BrightnessL" + suf);
    out["brightness_zone_" + num] = v;
    if (v && v !== 13 && v !== 14) anyOn = true;
  }
  out.any_on = anyOn;
  const cfg = semLightingConfig(d);
  out.wakeup = semWakeup(cfg);
  out.door_contact = cfg.DoorContact === undefined ? null : cfg.DoorContact === 1;
  const fs = cfg.FavouritesStored;
  /** @type {number[]|null} */
  let favs = null;
  if (fs !== undefined) { favs = []; for (let n = 1; n <= 7; n++) if ((fs >> (n - 1)) & 1) favs.push(n); }
  out.favourites_stored = favs;
  return out;
}

// --- general (semantics.py:390) --------------------------------------------------------------
const SEM_DCDC_PLUS2_SW = ["0409", "0410"]; // :556
const SEM_TESTED_AMB_SW = ["0409", "0410"]; // :561
const SEM_TESTED_COMM = 2;                  // :562

/**
 * semantics.py:380 _sw_ascii(): 4 ASCII bytes in a u32 -> "0410"; bytes >= 0x80 decode to U+FFFD
 * (bytes.decode("ascii", "replace"), which str.isprintable() accepts); a control byte -> null.
 * @param {number|null} v @returns {string|null}
 */
function semSwAscii(v) {
  if (!Number.isInteger(v)) return null;
  let s = "";
  for (const shift of [24, 16, 8, 0]) {
    const b = (/** @type {number} */ (v) >>> shift) & 0xff;
    if (b >= 0x80) s += "�";
    else if (b < 0x20 || b === 0x7f) return null;
    else s += String.fromCharCode(b);
  }
  return s;
}

/** semantics.py:565 _firmware_untested(). @param {string|null} amb @param {number|null} comm @returns {boolean} */
function semFirmwareUntested(amb, comm) {
  if (amb === null && comm === null) return false;
  const ambBad = amb !== null && !SEM_TESTED_AMB_SW.includes(amb);
  const commBad = comm !== null && comm !== SEM_TESTED_COMM;
  return ambBad || commBad;
}

/** semantics.py:390 general(). @param {Fields} d @returns {Interp} */
function semGeneral(d) {
  const amb = semSwAscii(semGet(d, "AmbSwVersion"));
  return {
    installed: true,
    comm_version: semGet(d, "CommunicationVersion"),
    cm_sw_version: semSwAscii(semGet(d, "CmSwVersion")),
    amb_sw_version: amb,
    firmware_untested: semFirmwareUntested(amb, semGet(d, "CommunicationVersion")),
  };
}

/** semantics.py:466 vehicle(): signed-16 hundredths of a degree, RTC +1900/+1, day 0 = unset. @param {Fields} d @returns {Interp} */
function semVehicle(d) {
  /** @param {number|null} v */
  const s16 = (v) => (v === null ? null : v >= 32768 ? v - 65536 : v);
  /** @param {number|null} v */
  const deg = (v) => { const s = s16(v); return s === null ? null : pyRound(s / 100, 2); };
  /** @param {number} n @param {number} w */
  const pad = (n, w) => String(n).padStart(w, "0");
  const [y, mo, da, h, mi, se] = ["CarTimeYear", "CarTimeMonth", "CarTimeDay", "CarTimeHour", "CarTimeMinute", "CarTimeSecond"]
    .map((k) => semGet(d, k));
  let clock = null;
  if (![y, mo, da, h, mi, se].includes(null) && da) {
    clock = `${pad(y + 1900, 4)}-${pad(mo + 1, 2)}-${pad(da, 2)} ${pad(h, 2)}:${pad(mi, 2)}:${pad(se, 2)}`;
  }
  const ign = !!semGet(d, "TerminalOneFive");
  return {
    installed: true,
    ignition_on: ign,
    car_variant: semGet(d, "CarVariant"),
    level_popup: semGet(d, "CarLevelPopUp"),
    level_roll: ign ? deg(semGet(d, "CarLevelRoll")) : null,  // ignition off: no level signal (app "-.-°")
    level_pitch: ign ? deg(semGet(d, "CarLevelPitch")) : null,
    car_clock: clock,
  };
}

/** semantics.py:521 _generic(): Installed first, every other field raw. @param {Fields} d @returns {Interp} */
function semGeneric(d) {
  /** @type {Interp} */
  const out = {};
  if (Object.prototype.hasOwnProperty.call(d, "Installed")) out.installed = !!d.Installed;
  for (const [k, v] of Object.entries(d)) if (k !== "Installed") out[k] = v;
  return out;
}

/** @type {Record<string, (d: Fields) => Interp>} semantics.py:530 INTERPRETERS (UI functions only) */
const SEM_INTERPRETERS = {
  water: semWater, energy: semEnergy, cooler: semCooler, airheater: semAirheater, campingmode: semCampingmode,
  roof: semRoof, lighting: semLighting, general: semGeneral, vehicle: semVehicle,
};

/** semantics.py:547 interpret(). @param {string} fn @param {Fields} d @returns {Interp} */
function interpret(fn, d) {
  return (Object.prototype.hasOwnProperty.call(SEM_INTERPRETERS, fn) ? SEM_INTERPRETERS[fn] : semGeneric)(d);
}

/** semantics.py:575 apply_sw_corrections(): DC-DC current +2 on AmbSwVersion 0409/0410 (mutates). @param {Record<string, any>} states */
function applySwCorrections(states) {
  const en = states.energy, gen = states.general;
  if (en && typeof en === "object" && gen && typeof gen === "object" && en.dcdc_current != null
      && SEM_DCDC_PLUS2_SW.includes(gen.amb_sw_version)) {
    en.dcdc_current = en.dcdc_current + 2;
  }
  return states;
}

/** anchors.py:23 check(): plausibility violations, Python's texts byte for byte. @param {Record<string, any>} states @returns {string[]} */
function anchorsCheck(states) {
  /** @type {string[]} */
  const out = [];
  /** @param {any} v */
  const num = (v) => typeof v === "number";
  const en = states.energy || {};
  const v = en.batt2_v;
  if (num(v) && !(v >= 8 && v <= 16)) out.push(`leisure battery ${v.toFixed(1)} V outside 8-16 V`);
  const soc = en.soc2_level;
  if (num(soc) && !(soc >= 0 && soc <= 15)) out.push(`leisure SoC level ${soc} outside 0-15`);
  const c = states.cooler || {};
  if (c.installed) {
    const lvl = c.level;
    if (num(lvl) && !(lvl >= 1 && lvl <= 5)) out.push(`cooler level ${lvl} outside 1-5`);
    for (const k of ["quiet_from", "quiet_to"]) {
      const hr = c[k];
      if (num(hr) && !(hr >= 0 && hr <= 23)) out.push(`cooler ${k} ${hr} outside 0-23 h`);
    }
  }
  const r = states.roof || {};
  if (r.installed) {
    const pos = r.position;
    if (num(pos) && !(pos >= 0 && pos <= 15)) out.push(`roof position ${pos} outside 0-15`);
  }
  const veh = states.vehicle || {};
  for (const k of ["level_roll", "level_pitch"]) {
    const deg = veh[k];
    if (num(deg) && Math.abs(deg) > 90) out.push(`vehicle ${k} ${semFloatStr(deg)}° outside ±90°`);
  }
  return out;
}

/** serve.py:158 ServeBackend._firmware_meta(). @param {Interp|null|undefined} general @returns {Interp} */
function firmwareMeta(general) {
  const g = general || {};
  return {
    amb_sw_version: g.amb_sw_version ?? null,
    cm_sw_version: g.cm_sw_version ?? null,
    comm_version: g.comm_version ?? null,
    untested: !!g.firmware_untested,
    tested: "amb 0409/0410 · comm 2",
  };
}

/**
 * True for the ESP32 satellite's raw GET /api/state ({t, fn, device}, firmware web.c api_state()),
 * false for calictl's interpreted body (which always carries _meta) or anything else.
 * @param {any} b @returns {boolean}
 */
function isSatelliteBody(b) {
  return !!b && typeof b === "object" && "fn" in b && "device" in b && !("_meta" in b);
}

/**
 * The satellite's raw /api/state -> the interpreted STATE calictl's /api/state carries, with a
 * synthesized `_meta` shaped like serve.py:135 ServeBackend.state(): read-only unless the firmware
 * reports `device.control.writes` (station mode), no session, no auto-camper, plus
 * `satellite: true` (app.js gates calictl-only chrome on it).
 * @param {any} body  {t, fn, device}
 * @param {number} nowMs  Date.now(): the ESP has no wall clock, so last_seen = now - age
 * @returns {Record<string, any>}
 */
function adaptSatellite(body, nowMs) {
  const fn = body.fn || {}, dev = body.device || {};
  const link = dev.link || {}, pairing = dev.pairing || {};
  /** @type {Record<string, any>} */
  const out = {};
  for (const name of Object.keys(fn)) out[name] = interpret(name, fn[name]);
  applySwCorrections(out);
  // The firmware holds the last-plausible water frame when the parked unit returns the stale latch
  // (device.water_held) — flag the tanks stale, as serve.state() does on calictl. No wall clock on
  // the satellite, so no stale_since (the UI's "🕒 last measured" renders without the "(ago)").
  if (dev.water_held && out.water) {
    if (out.water.fresh) out.water.fresh = { ...out.water.fresh, stale: true };
    if (out.water.waste) out.water.waste = { ...out.water.waste, stale: true };
  }
  const ms = link.last_snap_age_ms;
  const age = typeof ms === "number" ? ms / 1000 : null;
  out._meta = {
    online: age !== null && age <= SAT_OFFLINE_S,
    age_s: age,
    last_seen: age === null ? null : nowMs / 1000 - age,
    paired: !!pairing.address,
    // The firmware accepts POST /api/command only in station mode (device.control.writes); an older
    // firmware without the field stays display-only.
    read_only: !(dev.control && dev.control.writes === true),
    session: "off",
    session_mode: "off",
    satellite: true,
    firmware: firmwareMeta(out.general),
    anchors: anchorsCheck(out),
    // Raw device identity/transport rows for the in-app Device-status screen (only the
    // satellite has them; calictl's _meta carries its own session fields instead).
    sat: {
      fw: dev.fw || null,
      uptime_ms: typeof dev.uptime_ms === "number" ? dev.uptime_ms : null,
      wifi: dev.wifi || null,
      link_up: !!link.up,
    },
  };
  return out;
}
