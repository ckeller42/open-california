// @ts-check
// remote.js — the remote app's data source (R_UI_REMOTE_APP): reads the van's uplinks from The Things Stack
// (ttn.js readUplinks, read key only), decodes them on the phone (lora_decode.js) and answers a body shaped like
// the satellite's raw /api/state, so semantics.js adaptSatellite() interprets it as it does the ESP's. A classic
// script (no module); app.js calls remoteState() only when window.OC_REMOTE is true. NOT served by the ESP.
// TTS keeps uplinks for 24 h only: the newest frame per function (with its time) and the energy counters
// stay in localStorage. The keys stay in the configuration: never in a body, an error or a log.

const REMOTE_CFG_KEY = "oc.remote.cfg";       // {cluster, appId, devId, readKey, cmdKey} (Task 10 writes it)
const REMOTE_LAST_KEY = "oc.remote.last";     // the newest frame per function, the read cursor
const REMOTE_ENERGY_KEY = "oc.remote.energy"; // [{at, boot, solar_wh, load_wh, batt_net_wh}], oldest first
// ponytail: one counter point per hour and boot, 90 days (~2200 points); finer or longer when a view needs it
const REMOTE_ENERGY_STEP_MS = 3600000;
const REMOTE_ENERGY_KEEP_MS = 90 * 86400000;

/** @param {string} key @returns {any} the parsed value, null when absent, unreadable or blocked */
function remoteLoad(key) {
  try { return JSON.parse(localStorage.getItem(key) || "null"); } catch (e) { return null; }
}

/** @param {string} key @param {any} v */
function remoteSave(key, v) {
  try { localStorage.setItem(key, JSON.stringify(v)); } catch (e) { /* storage blocked/full: the next read starts over */ }
}

/** @returns {{cluster: string, appId: string, devId: string, readKey: string, cmdKey: string} | null} */
function remoteCfg() {
  const c = remoteLoad(REMOTE_CFG_KEY);
  return c && c.cluster && c.appId && c.devId && c.readKey ? c : null;
}

/** The cluster's API base: a host gets https://; a full URL (the tests' fake TTS) stays as it is. @param {string} cluster */
function remoteBase(cluster) {
  return /^https?:\/\//.test(cluster) ? cluster.replace(/\/+$/, "") : "https://" + cluster;
}

/**
 * Reads the uplinks since the last call, keeps the newest frame per function, and answers a satellite-shaped
 * body: `fn` (raw fields), `ext` (the Victron readings of port 2), `device` with `source: "lora"`, `ages_s` per
 * function, `link.last_snap_age_ms` (the newest frame's age), `energy` (the counters), `fw` and `lora_version`
 * (an uplink version this app does not know, else null). Port 12 (key-transfer confirmations) is not state.
 * Throws TtnError when TTS refuses or does not answer.
 * @param {{cluster: string, appId: string, devId: string, readKey: string}} cfg
 * @param {number} now ms since epoch
 * @returns {Promise<Record<string, any>>}
 */
async function remoteState(cfg, now) {
  const id = [cfg.cluster, cfg.appId, cfg.devId].join("/");
  let last = remoteLoad(REMOTE_LAST_KEY);
  if (!last || last.id !== id) last = { id, cursor: null, fn: {}, ext: null, energy: null, fw: null, newest: null, unknown: null };
  const ups = await readUplinks(remoteBase(cfg.cluster), cfg.readKey, cfg.appId, cfg.devId,
    last.cursor ? { after: last.cursor } : {});
  /** @type {Array<{at: number, boot: number, solar_wh: number, load_wh: number, batt_net_wh: number}>} */
  const points = remoteLoad(REMOTE_ENERGY_KEY) || [];
  for (const u of ups.slice().reverse()) {   // oldest first: a newer frame always wins
    last.cursor = u.receivedAt;
    const d = loraDecodeUplink(u.port, u.bytes);
    if (d.error || d.port === 12) continue;
    if (d.version !== undefined && d.version !== loraDecodeUplink.VERSION) { last.unknown = d.version; continue; }
    if (d.version !== undefined) last.unknown = null;
    for (const [name, fields] of Object.entries(d.fn)) last.fn[name] = { at: u.at, d: fields };
    if (d.port === 2) {
      last.ext = { at: u.at, ...d.ext };
      const p = { at: u.at, boot: d.boot, ...d.counters };
      last.energy = p;
      const prev = points[points.length - 1];
      if (!prev || prev.boot !== p.boot || p.at - prev.at >= REMOTE_ENERGY_STEP_MS) points.push(p);
    }
    if (d.port === 4) last.fw = d.fw;
    last.newest = Math.max(last.newest || 0, u.at);
  }
  remoteSave(REMOTE_LAST_KEY, last);
  remoteSave(REMOTE_ENERGY_KEY, points.filter((p) => now - p.at <= REMOTE_ENERGY_KEEP_MS));
  const age = (/** @type {number} */ at) => Math.max(0, (now - at) / 1000);
  /** @type {Record<string, any>} */
  const fn = {};
  /** @type {Record<string, number>} */
  const ages = {};
  /** @type {Record<string, any>} */
  const ext = {};
  for (const [name, v] of Object.entries(last.fn)) { fn[name] = v.d; ages[name] = age(v.at); }
  if (last.ext) {
    for (const type of ["victron_mppt", "victron_battery"]) {
      const e = last.ext[type] || {};
      if (Object.values(e).some((v) => v !== null)) ext[type] = { type, ...e, age_s: age(last.ext.at) };
    }
  }
  const en = last.energy;
  return {
    fn,
    ext,
    device: {
      source: "lora",
      ages_s: ages,
      lora_version: last.unknown,
      link: last.newest == null ? {} : { last_snap_age_ms: now - last.newest },
      energy: en ? { boot: en.boot, solar_wh: en.solar_wh, load_wh: en.load_wh, batt_net_wh: en.batt_net_wh, load_a: null } : null,
      fw: last.fw,
    },
  };
}
