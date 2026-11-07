// @ts-check
// remote.js — the remote app's data source (R_UI_REMOTE_APP): reads the van's uplinks from The Things Stack
// (ttn.js readUplinks, read key only), decodes them on the phone (lora_decode.js) and answers a body shaped like
// the satellite's raw /api/state, so semantics.js adaptSatellite() interprets it as it does the ESP's. A classic
// script (no module); app.js calls remoteState() only when window.OC_REMOTE is true. NOT served by the ESP.
// TTS keeps uplinks for 24 h only: the newest frame per function (with its time) and the energy counters
// stay in localStorage. The keys stay in the configuration: never in a body, an error or a log.
// Below remoteState: the setup screens (Set up LoRa, Scan the van's code, Remove this van) that app.js render()
// shows in remote mode; they use the vendored QrScanner (vendor/qr-scanner) and lora_setup.js.

const REMOTE_CFG_KEY = "oc.remote.cfg";       // {cluster, appId, devId, readKey, cmdKey}; the setup or a repair QR writes it
const REMOTE_LAST_KEY = "oc.remote.last";     // the newest frame per function, the read cursor
const REMOTE_ENERGY_KEY = "oc.remote.energy"; // [{at, boot, solar_wh, load_wh, batt_net_wh}], oldest first
// ponytail: one counter point per hour (a new boot: at once, but >= 60 s apart), 90 days (~2200 points); finer or longer when a view needs it
const REMOTE_ENERGY_STEP_MS = 3600000;
const REMOTE_ENERGY_KEEP_MS = 90 * 86400000;

/** @param {string} key @returns {any} the parsed value, null when absent, unreadable or blocked */
function remoteLoad(key) {
  try { return JSON.parse(localStorage.getItem(key) || "null"); } catch (e) { return null; }
}

/** @param {string} key @param {any} v @returns {boolean} false: storage blocked or full (the next read starts over) */
function remoteSave(key, v) {
  try { localStorage.setItem(key, JSON.stringify(v)); return true; } catch (e) { return false; }
}

/** remoteState's read outlived its configuration (the van was removed, a repair QR chose another): nothing of it
 * is stored or shown. */
class RemoteStale extends Error {}

/** A TTS cluster host (`eu1.cloud.thethings.network`, `<tenant>.eu1.cloud.thethings.industries`): never a URL, and
 * only under the two domains the page's connect-src allows (tools/build_remote.py CSP), so a typed router or LAN
 * host never gets a key. @param {any} h */
const remoteHostOk = (h) => typeof h === "string" && h.length <= 64 && LORA_ID_RE.test(h)
  && /^([a-z0-9-]+\.)+thethings\.(network|industries)$/.test(h);

/** @returns {{cluster: string, appId: string, devId: string, readKey: string, cmdKey: string, base?: string} | null}
 * null without a usable configuration (a cluster that is not a plain host is refused) */
function remoteCfg() {
  const c = remoteLoad(REMOTE_CFG_KEY);
  return c && remoteHostOk(c.cluster) && c.appId && c.devId && c.readKey ? c : null;
}

/** The API base the keys go to: always `https://<cluster host>`. The only exception is the tests' seam `base`,
 * honoured only for `http://127.0.0.1:<port>` (the fake TTS), so a configuration can never send a key elsewhere.
 * @param {{cluster: string, base?: string}} o */
function remoteBase(o) {
  if (typeof o.base === "string" && /^http:\/\/127\.0\.0\.1:\d{1,5}$/.test(o.base)) return o.base;
  if (!remoteHostOk(o.cluster)) throw new TtnError(0, "bad cluster host", "setup");
  return "https://" + o.cluster;
}

/**
 * Reads the uplinks since the last call, keeps the newest frame per function, and answers a satellite-shaped
 * body: `fn` (raw fields), `ext` (the Victron readings of port 2), `device` with `source: "lora"`, `ages_s` per
 * function, `link.last_snap_age_ms` (the newest frame's age), `energy` (the counters), `fw` and `lora_version`
 * (an uplink version this app does not know, else null). Port 12 (key-transfer confirmations) is not state.
 * Throws TtnError when TTS refuses or does not answer.
 * @param {{cluster: string, appId: string, devId: string, readKey: string, base?: string}} cfg
 * @param {number} now ms since epoch
 * @returns {Promise<Record<string, any>>}
 */
async function remoteState(cfg, now) {
  const id = remoteId(cfg);
  let last = remoteLoad(REMOTE_LAST_KEY);
  // another van (a repair QR chose it): its energy history starts empty, the old van's points never mix in
  const other = !last || last.id !== id;
  if (other) last = { id, cursor: null, fn: {}, ext: null, energy: null, fw: null, newest: null, unknown: null };
  const ups = await readUplinks(remoteBase(cfg), cfg.readKey, cfg.appId, cfg.devId,
    last.cursor ? { after: last.cursor } : {});
  if (JSON.stringify(remoteCfg()) !== JSON.stringify(cfg)) throw new RemoteStale("the configuration changed");
  /** @type {Array<{at: number, boot: number, solar_wh: number, load_wh: number, batt_net_wh: number}>} */
  const points = other ? [] : remoteLoad(REMOTE_ENERGY_KEY) || [];
  for (const u of ups.slice().reverse()) {   // oldest first: a newer frame always wins
    if (u.receivedAt === last.cursor) continue;   // `after` may be inclusive: the cursor's own uplink again
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
      // a new boot adds a point at once, but at most one a minute (a van in a reboot loop must not fill the storage)
      if (!prev || p.at - prev.at >= (prev.boot !== p.boot ? 60000 : REMOTE_ENERGY_STEP_MS)) points.push(p);
    }
    if (d.port === 4) last.fw = d.fw;
    last.newest = Math.max(last.newest || 0, u.at);
  }
  remoteSave(REMOTE_LAST_KEY, last);
  remoteSave(REMOTE_ENERGY_KEY, points.filter((p) => now - p.at <= REMOTE_ENERGY_KEEP_MS));
  return remoteBody(last, now);
}

/** @param {{cluster: string, appId: string, devId: string}} cfg the van's identity in oc.remote.last */
const remoteId = (cfg) => [cfg.cluster, cfg.appId, cfg.devId].join("/");

/** The stored frames of `cfg`'s van as remoteState answers them, without a TTS read; null when none are stored.
 * app.js shows them when the first read fails and no snapshot was restored (calictl.last_state missing or too big
 * for the storage). @param {{cluster: string, appId: string, devId: string}} cfg @param {number} now */
function remoteCached(cfg, now) {
  const last = remoteLoad(REMOTE_LAST_KEY);
  return last && last.id === remoteId(cfg) && last.newest != null ? remoteBody(last, now) : null;
}

/** The satellite-shaped body of the stored frames (see remoteState). @param {any} last @param {number} now */
function remoteBody(last, now) {
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

// --- the setup (spec 5.1 steps 2-8), the repair scan (5.2) and "Remove this van" (5.5) ------------------------
// The full-rights key lives ONLY in rsKey: never in storage, the DOM (the input is cleared on submit), an error or
// a log. It is dropped right after deleteOwnKey, which runs as soon as the phone's two keys exist and the
// configuration is stored, before the join wait and the transfer. What a reload must survive is in
// oc.remote.setup: {step: "join"|"sent"|"rescan", cluster, appId, devId, devEui, tk, nonce, queued, total,
// startedAt, sentAt, resends, cursor, held}; tk (the van's transfer key, hex) is wiped by deleting the entry
// on status 0; held: fragments that wait for room. oc.remote.pushes: {devEui: the times (ms) of the setup downlinks
// pushed in the last 24 h}, kept across setups and "Remove this van", so a new setup never starts a fresh day.
// oc.remote.keyleft lists the full-rights keys (id and application only, never the secret) from a passed check
// until their delete answered: a closed page, an abandoned flow or a failed delete leaves the owner a warning.

const REMOTE_SETUP_KEY = "oc.remote.setup";
const REMOTE_KEYLEFT_KEY = "oc.remote.keyleft";   // [{keyId, appId}]
const REMOTE_PUSH_KEY = "oc.remote.pushes";       // {devEui: [ms]}
const REMOTE_LOCK = "oc.remote.setup";            // navigator.locks: one tab at a time reads and pushes the setup
const REMOTE_SETUP_POLL_MS = 15000;   // setup only; the cockpit reads every REMOTE_POLL_MS
const REMOTE_JOIN_HINT_MS = 600000;
// no confirmation this long after a push: push the last fragment again as a probe (fair use: never sooner), at most 3
// times; the van answers it with status 2 and the bitmap of what it has, or 00 when it already has the keys
const REMOTE_RESEND_MS = 600000;
const REMOTE_RESENDS = 3;
// at most this many setup downlinks per 24 h (TTN fair use 10 a day; the van's own budget is 8)
const REMOTE_DOWNLINKS = 8;
const REMOTE_DAY_MS = 86400000;
const REMOTE_LIMIT_MSG = "The van's daily downlink limit is reached. The setup continues tomorrow.";
const REMOTE_STORE_MSG = "This phone cannot store the van's keys. Free some storage, leave private browsing and scan again.";
const REMOTE_JOIN_SKEW_MS = 120000;   // an uplink counts as the join from 2 min before the wait (phone vs TTS clock)
const REMOTE_CLUSTERS = ["eu1.cloud.thethings.network"];   // EU868 only (registerDevice); "other": a TTS Cloud host
const REMOTE_OTHER = "other";

let rsKey = "", rsKeyId = "", rsAppId = "", rsCluster = "";
/** @type {"key"|"scan"|"work"|"done"} the screen while oc.remote.setup is empty */
let rsPhase = "key";
let rsMsg = "";        // an English source text (translated at paint), never an error's own message
/** @type {ReturnType<typeof setTimeout>|null} */
let rsTimer = null;
/** @type {{view: string, phase: string, lang: string, el: HTMLElement}|null} the open screen (kept across repaints) */
let rsDom = null;
/** @type {any} the running camera scanner */
let rsCam = null;
let rsGen = 0;   // +1 by "Remove this van": a step still waiting on TTS then stores nothing

/** @param {Uint8Array} b */
const rsHex = (b) => Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
/** The test seam of the stored configuration (see remoteBase), carried into the new one. */
const rsSeam = () => (remoteLoad(REMOTE_CFG_KEY) || {}).base;
/** @param {any} st @returns {boolean} false: not stored */
const rsSave = (st) => remoteSave(REMOTE_SETUP_KEY, st);
/** @returns {Array<{keyId: string, appId: string}>} */
const rsKeysLeft = () => remoteLoad(REMOTE_KEYLEFT_KEY) || [];
/** Mark (on) or clear (off) a full-rights key that may be live at TTN. @param {string} keyId @param {string} appId @param {boolean} on */
function rsMarkKey(keyId, appId, on) {
  const left = rsKeysLeft().filter((k) => k.keyId !== keyId);
  if (on) left.push({ keyId, appId });
  if (left.length) remoteSave(REMOTE_KEYLEFT_KEY, left);
  else try { localStorage.removeItem(REMOTE_KEYLEFT_KEY); } catch (e) { /* storage blocked */ }
}

/** app.js render(), every remote screen: one warning per full-rights key that may still be live (not the one the
 * open setup screen is using right now). @returns {HTMLElement|null} */
function remoteKeyWarn() {
  const left = rsKeysLeft().filter((k) => !(view === "remotesetup" && k.keyId === rsKeyId));
  if (!left.length) return null;
  const box = h("div", { class: "sat" });
  for (const k of left) {
    const done = satBtn(T("I have deleted it"));
    done.onclick = () => { rsMarkKey(k.keyId, k.appId, false); render(); };
    box.append(h("div", { class: "card dcard" }, h("p", { class: "warn" },
      tf("A full-rights key may still be live. Delete the API key {id} in The Things Network console (application {app}).",
        { id: k.keyId, app: k.appId })), h("div", { class: "acts" }, done)));
  }
  return box;
}

/** The phase a screen shows: the stored setup's step wins. @param {string} v */
function rsPhaseNow(v) {
  if (v !== "remotesetup") return v;
  const st = remoteLoad(REMOTE_SETUP_KEY);
  return st && st.step ? (st.step === "rescan" ? "scan" : st.step) : rsPhase;
}

/** The owner's text for a TTS failure (TtnError.kind; a network failure is a TypeError). @param {any} e */
function rsErrText(e) {
  const k = e && e.kind;
  if (k === "user_key") return "This is not an application key: create the key in the application's API keys.";
  if (k === "not_all_rights") return 'The key needs "Grant all current and future rights".';
  if (k === "auth") return "TTN does not accept this key.";
  if (k === "forbidden") return "The key lacks a right for this step.";
  if (k === "setup") return "Enter the cluster's host name, for example eu1.cloud.thethings.network.";
  if (k === "timeout") return "TTN does not answer. Check the cluster and the connection.";
  return e instanceof TtnError ? "TTN refused this step. Try again." : "TTN does not answer. Check the cluster and the connection.";
}

/** Repaint the open setup screen: its status line in place, a new phase as a new screen. */
function rsRefresh() {
  if (!REMOTE_VIEWS.includes(view)) return;
  if (rsDom && rsDom.view === view && rsDom.phase === rsPhaseNow(view) && rsDom.lang === LANG) rsPaint();
  else render();
}

/** Stop the camera (leaving the scan, or a code was read). */
function rsCamStop() {
  if (rsCam) { rsCam.destroy(); rsCam = null; }
}

/** The app starts: a stored setup reopens its screen and goes on waiting. Before the first poll. */
function remoteBoot() {
  if (!(remoteLoad(REMOTE_SETUP_KEY) || {}).step) return;
  view = "remotesetup";
  try { history.replaceState({ v: view }, "", viewUrl(view)); } catch (e) { /* no history API */ }
  rsPoll();
}

/** "Set up LoRa", "Scan the van's code", "Remove this van" (the remote ⋮ menu). @param {string} v */
function remoteOpen(v) {
  closeMenu();
  if (v === "remotesetup" && rsPhase === "done") rsPhase = "key";
  rsMsg = "";
  goto(v);
}

// --- the steps -------------------------------------------------------------------------------------------------

/** Step 2: the pasted key -> checkFullKey (no write before it passes). @param {string} cluster @param {string} key */
async function rsCheck(cluster, key) {
  rsMsg = "";
  if (!remoteHostOk(cluster)) { rsMsg = rsErrText({ kind: "setup" }); rsRefresh(); return; }
  if (!key) return;
  rsPhase = "work"; rsRefresh();
  try {
    const r = await checkFullKey(remoteBase({ cluster, base: rsSeam() }), key);
    rsMarkKey(r.keyId, r.appId, true);   // live at TTN until deleteOwnKey answers
    rsKey = key; rsKeyId = r.keyId; rsAppId = r.appId; rsCluster = cluster;
    rsPhase = "scan";
  } catch (e) {
    rsPhase = "key"; rsMsg = rsErrText(e);
  }
  rsRefresh();
}

/** A scanned setup QR: steps 5 (register, keys, delete the full-rights key) or, after status 1/3, a new transfer
 * key for the same van. While a new scan is asked for, the van's repair QR with the stored keys also ends the
 * setup: the van has the keys and only its confirmation was lost. @param {string} text */
async function rsSetupQr(text) {
  const st = remoteLoad(REMOTE_SETUP_KEY);
  const r = st && st.step === "rescan" ? parseRepairQr(text.trim()) : null;
  if (r) {
    const cfg = remoteCfg();
    const same = cfg && cfg.cluster === r.cluster && cfg.appId === r.appId && cfg.devId === r.devId
      && cfg.readKey === r.readKey && cfg.cmdKey === r.cmdKey;
    if (!same) { rsMsg = "This code belongs to another van."; rsRefresh(); return; }
    rsDone();
    return;
  }
  const q = parseSetupQr(text.trim());
  if (!q) { rsMsg = "This is not the van's setup code."; rsRefresh(); return; }
  if (st && st.step === "rescan") {
    if (q.devEui !== st.devEui) { rsMsg = "This code belongs to another van."; rsRefresh(); return; }
    // a new transfer: new TK and nonce, a fresh resend budget (rsSend stores queued, total and sentAt anew)
    Object.assign(st, { tk: rsHex(q.tk), nonce: rsHex(crypto.getRandomValues(new Uint8Array(12))), resends: 0 });
    rsSave(st);
    rsMsg = "";
    await rsSend(st, null);   // the van is joined: it has just sent its confirmation (a failed push: scan again)
    return;
  }
  if (!rsKey) { rsPhase = "key"; rsRefresh(); return; }
  rsMsg = ""; rsPhase = "work"; rsRefresh();
  const devId = "van-" + q.devEui.slice(-6).toLowerCase();
  const seam = rsSeam(), base = remoteBase({ cluster: rsCluster, base: seam }), gen = rsGen, key = rsKey;
  // "Remove this van" meanwhile: the next step is not taken and neither record comes back (the key warning stays)
  const gone = () => gen !== rsGen;
  try {
    await registerDevice(base, key, { appId: rsAppId, devId, devEui: q.devEui, appKey: q.appKey });
    if (gone()) return;
    await enableStorage(base, key, rsAppId);
    if (gone()) return;
    const keys = await makePhoneKeys(base, key, rsAppId, devId);
    if (gone()) return;
    /** @type {Record<string, string>} */
    const cfg = { cluster: rsCluster, appId: rsAppId, devId, readKey: keys.readKey, cmdKey: keys.cmdKey };
    if (seam) cfg.base = seam;
    const next = { step: "join", cluster: rsCluster, appId: rsAppId, devId, devEui: q.devEui, tk: rsHex(q.tk),
      nonce: rsHex(crypto.getRandomValues(new Uint8Array(12))), queued: [], total: 0, startedAt: Date.now(),
      cursor: null };
    if (!remoteSave(REMOTE_CFG_KEY, cfg) || !rsSave(next)) {
      // storage blocked or full: the full-rights key stays (a new scan makes the phone's keys again)
      try { localStorage.removeItem(REMOTE_CFG_KEY); } catch (e) { /* storage blocked */ }
      rsPhase = "scan"; rsMsg = REMOTE_STORE_MSG; rsRefresh();
      return;
    }
    try {
      await deleteOwnKey(base, rsKey, rsAppId, rsKeyId);
      rsMarkKey(rsKeyId, rsAppId, false);
    } catch (e) { /* the mark stays: every remote screen tells the owner to delete it in the console */ }
    rsKey = rsKeyId = "";   // the phone's keys exist: the full-rights key is done, whatever the delete answered
  } catch (e) {
    if (gone()) return;
    rsPhase = "scan"; rsMsg = rsErrText(e); rsRefresh();   // before the phone's keys: the owner may scan again
    return;
  }
  rsPoll();
}

/** The setup is done (the van's confirmation, or its repair QR with the stored keys): the transfer key is wiped. */
function rsDone() {
  try { localStorage.removeItem(REMOTE_SETUP_KEY); } catch (e) { /* storage blocked */ }
  rsPhase = "done"; rsMsg = "";
  rsRefresh();
}

/** The setup downlinks pushed to `st`'s van in the last 24 h. @param {any} st @param {number} now @returns {number[]} */
const rsPushes = (st, now) => ((remoteLoad(REMOTE_PUSH_KEY) || {})[st.devEui] || []).filter((/** @type {number} */ t) => now - t < REMOTE_DAY_MS);

/** Store the push times of a van (older than a day: dropped, for every van). @param {string} devEui @param {number[]} list */
function rsPushSave(devEui, list) {
  const now = Date.now(), all = { ...(remoteLoad(REMOTE_PUSH_KEY) || {}), [devEui]: list };
  /** @type {Record<string, number[]>} */
  const keep = {};
  for (const [k, v] of Object.entries(all)) {
    const t = (Array.isArray(v) ? v : []).filter((x) => now - x < REMOTE_DAY_MS);
    if (t.length) keep[k] = t;
  }
  remoteSave(REMOTE_PUSH_KEY, keep);
}

/** Step 7: seal the transfer (the stored nonce: a resend is byte-identical) and queue `which` fragments (null =
 * all). `st` (with the cursor past the uplink that asked for it) is stored only when the push went through, so a
 * failed push is tried again on the next read. Fragments that would pass REMOTE_DOWNLINKS in 24 h are held
 * (`held`) and pushed by a later read once there is room. @param {any} st @param {number[]|null} which */
async function rsSend(st, which) {
  const cfg = remoteCfg();
  if (!cfg) return;
  const prev = remoteLoad(REMOTE_SETUP_KEY), gen = rsGen;
  try {
    const frags = await sealTransfer(/** @type {Uint8Array<ArrayBuffer>} */ (packPlain(cfg)), loraUnhex(st.tk), st.devEui, loraUnhex(st.nonce));
    if (gen !== rsGen) return;
    const idx = (which || frags.map((_, i) => i)).filter((i) => i < frags.length);
    const now = Date.now(), pushes = rsPushes(st, now);
    if (pushes.length + idx.length > REMOTE_DOWNLINKS) {
      rsSave({ ...st, step: "sent", held: idx, total: frags.length });
      rsMsg = REMOTE_LIMIT_MSG;
      rsPoll();
      return;
    }
    // stored BEFORE the push: a reload between push and save never queues the fragments twice (10 downlinks a day)
    rsSave({ ...st, step: "sent", queued: idx, held: null, total: frags.length, sentAt: now });
    rsPushSave(st.devEui, [...pushes, ...idx.map(() => now)]);
    try {
      await pushDownlinks(remoteBase(cfg), cfg.cmdKey, cfg.appId, cfg.devId, idx.map((i) => frags[i]), 12);
    } catch (e) {
      // TTS answered with an error: nothing was queued, the next read asks again. No answer (a timeout, the network
      // gone): the batch may be queued at TTS, so it stays sent and counted; the probe after REMOTE_RESEND_MS asks
      // the van what it has
      if (e instanceof TtnError && e.status > 0 && gen === rsGen) {
        if (prev) rsSave(prev);
        rsPushSave(st.devEui, pushes);
      }
      throw e;
    }
    rsMsg = "";
  } catch (e) {
    rsMsg = rsErrText(e);
  }
  rsPoll();
}

/** Steps 6-8, one read every REMOTE_SETUP_POLL_MS: the join (a port 2/3/4 uplink), then the confirmation. */
async function rsPoll() {
  if (rsTimer) clearTimeout(rsTimer);
  rsTimer = null;
  const st = remoteLoad(REMOTE_SETUP_KEY), cfg = remoteCfg();
  if (!st || !cfg || (st.step !== "join" && st.step !== "sent")) { rsRefresh(); return; }
  rsRefresh();
  rsTimer = setTimeout(rsStep, REMOTE_SETUP_POLL_MS);
}

/** One setup read. Under a Web Lock where the browser has one: a second tab of the app waits, then reads the state
 * the first one stored, so two tabs never push the same fragments or both count them. */
function rsStep() {
  rsTimer = null;
  const locks = /** @type {any} */ (navigator).locks;
  return locks ? locks.request(REMOTE_LOCK, rsStepLocked) : rsStepLocked();
}

async function rsStepLocked() {
  const st = remoteLoad(REMOTE_SETUP_KEY), cfg = remoteCfg(), gen = rsGen;
  if (!st || !cfg) return;
  /** @type {Awaited<ReturnType<typeof readUplinks>>} */
  let ups;
  try {
    ups = (await readUplinks(remoteBase(cfg), cfg.readKey, cfg.appId, cfg.devId, st.cursor ? { after: st.cursor } : {}))
      .filter((u) => u.receivedAt !== st.cursor);   // `after` may be inclusive: never act on the same uplink twice
    rsMsg = "";
  } catch (e) { rsMsg = rsErrText(e); rsPoll(); return; }
  if (gen !== rsGen) return;
  if (st.step === "join") {   // newest first
    const j = ups.find((u) => [2, 3, 4].includes(u.port) && u.at >= st.startedAt - REMOTE_JOIN_SKEW_MS);
    if (j) { await rsSend({ ...st, cursor: j.receivedAt, resends: 0 }, null); return; }
  } else {
    const u = ups.find((x) => x.port === 12);
    if (u) {
      const d = loraDecodeUplink(12, u.bytes);
      const c = d.confirm ? parseConfirm(Uint8Array.from(d.confirm), st.total) : null;
      if (c && c.status === 2 && c.missing && c.missing.length) { await rsSend({ ...st, cursor: u.receivedAt, resends: 0 }, c.missing); return; }
      st.cursor = u.receivedAt;
      const ok = c && c.status === 0 && c.hash4
        && rsHex(c.hash4) === rsHex(await plainHash4(/** @type {Uint8Array<ArrayBuffer>} */ (packPlain(cfg))));
      if (gen !== rsGen) return;
      if (ok) { rsDone(); return; }
      if (c && c.status === 0) {
        // 00 with another plaintext's hash: a confirmation of earlier keys (a pending one from a setup before) --
        // not a failure of this transfer; wait on (the probe after REMOTE_RESEND_MS asks the van again)
        rsSave(st);
      } else {
        // 1 bad tag, 3 no transfer key: the van needs a new transfer key (a new screen open)
        delete st.tk;
        st.step = "rescan";
        rsSave(st);
        rsMsg = "";
      }
    } else if (st.held && st.held.length) {
      // over the day's downlinks: push once there is room again
      if (rsPushes(st, Date.now()).length + st.held.length <= REMOTE_DOWNLINKS) { await rsSend(st, st.held); return; }
      rsMsg = REMOTE_LIMIT_MSG;
    } else if (Date.now() - (st.sentAt || 0) >= REMOTE_RESEND_MS) {
      // no answer (also: a page closed between storing "sent" and the push): the last fragment again as a probe
      // (same TK and nonce: the van takes identical duplicates) -- the van answers with the bitmap of what it has
      // (status 2), or 00 when the keys are already there; after REMOTE_RESENDS a new scan
      if ((st.resends || 0) < REMOTE_RESENDS) { await rsSend({ ...st, resends: (st.resends || 0) + 1 }, [st.total - 1]); return; }
      delete st.tk;
      st.step = "rescan";
      rsSave(st);
      rsMsg = "The van did not confirm. Hold its screen for 3 seconds and scan the setup code again.";
    }
  }
  rsPoll();
}

/** A scanned repair QR (spec 5.2): the phone is configured, no TTN console work. @param {string} text */
function rsRepairQr(text) {
  const r = parseRepairQr(text.trim());
  if (!r) { rsMsg = "This is not the van's code."; rsRefresh(); return; }
  /** @type {Record<string, string>} */
  const cfg = { ...r };
  const seam = rsSeam();
  if (seam) cfg.base = seam;
  if (!remoteSave(REMOTE_CFG_KEY, cfg)) { rsMsg = REMOTE_STORE_MSG; rsRefresh(); return; }   // as rsSetupQr: name the storage cause
  // the van shows this code once it has the keys: a setup still waiting here (a lost confirmation) is over
  try { localStorage.removeItem(REMOTE_SETUP_KEY); } catch (e) { /* storage blocked */ }
  rsMsg = "";
  goto("home");
  refreshState(true);
}

/** "Remove this van" confirmed: the phone forgets the van (the device stays at TTN). */
function rsRemove() {
  rsCamStop();
  if (rsTimer) clearTimeout(rsTimer);
  rsTimer = null;
  rsGen++;
  rsKey = rsKeyId = "";
  rsPhase = "key";
  // and app.js's offline snapshot: a reload never shows the removed van again (oc.remote.pushes stays: TTN counts on)
  for (const k of [REMOTE_CFG_KEY, REMOTE_SETUP_KEY, REMOTE_ENERGY_KEY, REMOTE_LAST_KEY, LAST_STATE_KEY])
    try { localStorage.removeItem(k); } catch (e) { /* storage blocked */ }
  STATE = {};
  lastRender = "";
  goto("home");
  toast(T("This phone no longer shows the van."));
}

// --- the screens -----------------------------------------------------------------------------------------------

/** The scan box: a photo (always) and the camera (a secure origin only, on a tap). @param {(text: string) => any} onText */
function rsScanner(onText) {
  const QS = /** @type {any} */ (window).QrScanner;
  const file = h("input", { type: "file", accept: "image/*", capture: "environment", id: "rs-file" });
  file.onchange = async () => {
    const f = file.files && file.files[0];
    file.value = "";
    if (!f) return;
    let text;
    try { text = (await QS.scanImage(f, { returnDetailedScanResult: true })).data; }
    catch (e) { rsMsg = "No code found in the photo."; rsRefresh(); return; }
    onText(String(text));
  };
  const box = h("div", null, h("label", { for: "rs-file" }, T("Take a photo of the code")), file);
  if (window.isSecureContext && QS) {
    const video = h("video", { muted: "", playsinline: "" });
    video.hidden = true;
    const cam = satBtn(T("Use the camera"), "primary", "rs-camera");
    cam.onclick = () => {
      rsCamStop();
      video.hidden = false;
      rsCam = new QS(video, (/** @type {any} */ r) => { rsCamStop(); video.hidden = true; onText(String(r.data)); },
        { returnDetailedScanResult: true, preferredCamera: "environment" });
      rsCam.start().catch(() => { rsCamStop(); video.hidden = true; rsMsg = "The camera does not start: take a photo instead."; rsRefresh(); });
    };
    box.prepend(cam, video);
  }
  return box;
}

/** @param {string} v @param {string} phase @returns {HTMLElement} */
function rsBuild(v, phase) {
  const msg = h("p", { class: "warn", id: "rs-msg", "aria-live": "polite" });
  const card = (/** @type {(Node|string|null)[]} */ ...kids) => h("div", { class: "sat" }, h("div", { class: "card dcard" }, ...kids));
  if (v === "remotescan")
    return card(h("p", null, T("Open the LoRa screen on the van (press its screen for 3 s) and scan the code it shows.")),
      rsScanner(rsRepairQr), msg);
  if (v === "remoteremove") {
    const ok = satBtn(T("Remove"), "danger"), no = satBtn(T("Cancel"));
    ok.onclick = rsRemove;
    no.onclick = () => goto("home");
    return card(h("p", null, T("Remove this van from this phone? The app deletes its keys and data here. The device stays registered at The Things Network until you delete it in the console.")),
      h("div", { class: "acts" }, ok, no));
  }
  const status = h("p", { id: "rs-status", role: "status" });
  if (phase === "key") {
    const sel = h("select", { id: "rs-cluster" });
    for (const c of REMOTE_CLUSTERS) sel.append(h("option", { value: c }, c));
    sel.append(h("option", { value: REMOTE_OTHER }, T("Other (TTS Cloud tenant)")));
    const host = h("input", { type: "text", id: "rs-host", autocomplete: "off", autocapitalize: "none", spellcheck: "false",
      placeholder: "tenant.eu1.cloud.thethings.industries" });
    const hostBox = h("div", null, h("label", { for: "rs-host" }, T("Cluster host")), host);
    hostBox.hidden = true;
    sel.onchange = () => { hostBox.hidden = sel.value !== REMOTE_OTHER; };
    if (rsCluster && !REMOTE_CLUSTERS.includes(rsCluster)) { sel.value = REMOTE_OTHER; host.value = rsCluster; hostBox.hidden = false; }
    else if (rsCluster) sel.value = rsCluster;
    // one-time-code: no password manager offers to save or fill it
    const key = h("input", { type: "password", id: "rs-key", autocomplete: "one-time-code", autocapitalize: "none", spellcheck: "false" });
    const go = satBtn(T("Check the key"), "primary", "rs-check");
    const form = h("form", { autocomplete: "off" },
      h("p", null, T("In the TTN console, create an API key of your application with “Grant all current and future rights” and paste it here. The app uses it for a few calls and deletes it right after.")),
      h("label", { for: "rs-cluster" }, T("Cluster")), sel, hostBox,
      h("label", { for: "rs-key" }, T("Full-rights API key")), key, h("div", { class: "acts" }, go), msg);
    form.onsubmit = (ev) => {
      ev.preventDefault();
      const k = key.value.trim();
      key.value = "";   // the key leaves the DOM at once: from here on it is only in rsKey
      rsCheck((sel.value === REMOTE_OTHER ? host.value : sel.value).trim().toLowerCase(), k);
    };
    go.setAttribute("type", "submit");
    return card(form);
  }
  if (phase === "scan") {
    const st = remoteLoad(REMOTE_SETUP_KEY);
    return card(h("p", null, T(st ? "The van could not read the keys. Open its LoRa screen again (press its screen for 3 s) and scan the new setup code."
      : "Open the LoRa screen on the van (press its screen for 3 s) and scan its setup code.")), rsScanner(rsSetupQr), msg);
  }
  if (phase === "done") {
    const show = satBtn(T("Show the van"), "primary");
    show.onclick = () => { goto("home"); refreshState(true); };
    return card(status, h("div", { class: "acts" }, show));
  }
  const hint = h("p", { class: "note", id: "rs-hint" }, T("No uplink yet after 10 min: check the antenna, the gateway coverage at the van, and the LoRa box in the van's Satellite settings."));
  return card(status, hint, msg);
}

/** The status line (what the screen waits for, since when), the message and the key warning. */
function rsPaint() {
  if (!rsDom) return;
  const q = (/** @type {string} */ id) => /** @type {HTMLElement|null} */ (rsDom && rsDom.el.querySelector("#" + id));
  const st = remoteLoad(REMOTE_SETUP_KEY);
  const msg = q("rs-msg"), status = q("rs-status"), hint = q("rs-hint");
  if (msg) msg.textContent = rsMsg ? T(rsMsg) : "";
  if (!status) return;
  const since = st && (st.step === "sent" ? st.sentAt : st.startedAt);
  const vars = since ? { clock: clockText(since / 1000), ago: agoText(Math.max(0, (Date.now() - since) / 1000)) } : {};
  const phase = rsPhaseNow(rsDom.view);
  status.textContent = phase === "work" ? T("Talking to TTN …")
    : phase === "join" ? tf("Registered. Waiting for the van's first uplink since {clock} ({ago}).", vars)
    : phase === "sent" ? (st && st.held && st.held.length ? "" : tf("Keys sent to the van. Waiting for its confirmation since {clock} ({ago}).", vars))
    : phase === "done" ? (rsKeysLeft().length ? "" : T("The van has the keys.")) : "";   // a live key: no success
  if (hint) hint.hidden = !(phase === "join" && st && Date.now() - st.startedAt >= REMOTE_JOIN_HINT_MS);
}

/** app.js render() for the remote screens: the screen's DOM is kept while its phase stays (a typed key, the camera).
 * @param {string} v */
function remoteRender(v) {
  titleEl.textContent = T(v === "remotesetup" ? "Set up LoRa" : v === "remotescan" ? "Scan the van's code" : "Remove this van");
  const phase = rsPhaseNow(v);
  if (!rsDom || rsDom.view !== v || rsDom.phase !== phase || rsDom.lang !== LANG) {
    rsCamStop();
    rsDom = { view: v, phase, lang: LANG, el: rsBuild(v, phase) };
  }
  app.appendChild(rsDom.el);
  rsPaint();
}
