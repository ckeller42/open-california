// @ts-check
// hub.js — the remote app's link over the remote hub (R_UI_REMOTE_APP): an MQTT broker over wss:// (the vendored
// MQTT.js, vendor/mqtt) carries AES-128-GCM frames sealed by the van's ESP (WiFi) and by the bridge (LoRa uplinks
// from The Things Stack). The wire format is firmware/components/cali_core/include/cali_hub.h: ver(1)=1 | dir(1) |
// nonce(12) = dir | session(7) | counter(4, BE) | ct | tag(16); AD = the first 14 bytes. The app sends dir 2 and
// opens dir 1 (ESP) and 3 (bridge) only. Topics oc/<van>/online (clear "1"/"0", retained), state (dir 1, retained:
// the ESP's /api/state body), req (dir 2), res (dir 1), lora/<fport> (dir 3, retained: {fcnt, t, hex}).
// Heater and camping commands go over the hub; when the ESP does not answer in 30 s and is not online, they go
// once over LoRa (FPort 20, ttn.js pushDownlinks, the daily downlink limit of remote.js).
// A classic script (no module), loaded after remote.js; app.js calls it only when window.OC_REMOTE is true and
// oc.hub.cfg is set. NOT served by the ESP. The key and the passwords stay in oc.hub.cfg: never in a log, an
// error or the DOM; no plaintext frame is logged.

const HUB_CFG_KEY = "oc.hub.cfg";     // {van, url, user, pass, key}: the hub QR (OCH1) writes it
const HUB_LORA_KEY = "oc.hub.lora";   // {ctr, devEui}: the last LoRa command counter, the van's DevEUI
const HUB_LIVE_MS = 90000;            // a WiFi state older than this is not live
const HUB_RETAINED_MS = 30000;        // a retained state while the ESP is online: at most one publish period old
const HUB_CMD_WAIT_MS = 30000;        // no answer this long and the ESP not online: the command goes over LoRa
const HUB_GIVEUP_MS = 65000;          // the ESP gives a pending command up after 60 s (CALI_HUB_RPC_PENDING_MS)
const HUB_RING = 8;                   // CALI_HUB_RING: the ESP sessions remembered (replay guard)
const HUB_KEY_MSG = "Key changed: scan the hub QR";
const HUB_LOCAL_MSG = "Settings: only in the van's WiFi";
const HUB_LORA_ONLY_MSG = "Over LoRa only the heater and camping mode can be switched.";
const HUB_LORA_SENT_MSG = "Sent over LoRa.";
const HUB_UNKNOWN_MSG = "Result unknown: the van may still carry out the command.";
const HUB_CLOCK_MSG = "The van refused the command: set this phone's clock to the network time.";
/** @type {Record<string, number>} the LoRa command codes (cali_lora_cmd MAP) by "function what value" */
const HUB_LORA_CODES = { "airheater power on": 1, "airheater power off": 2, "campingmode master on": 3, "campingmode master off": 4 };

/** The link's state (one per page). `lora` is remote.js's frame store, fed by the bridge's frames. */
const hub = {
  /** @type {any} the MQTT.js client (a test sets a fake) */ client: null,
  conn: "",                                   // the configuration the client was made for
  /** @type {Uint8Array|null} */ sess: null,  // this page's 7-byte session; the counter goes on from `ctr`
  ctr: 0,
  /** @type {Array<{s: string, c: number}>} */ ring: [],
  /** @type {Record<string, number>} the newest fcnt shown per fport (R12) */ fcnt: {},
  /** @type {string|null} the clear online flag: "1", "0", null = not yet known */ online: null,
  /** @type {{body: any, at: number, retained: boolean}|null} the newest ESP state */ state: null,
  /** @type {any} */ lora: { fn: {}, ext: null, energy: null, fw: null, newest: null, unknown: null },
  /** @type {Map<number, (r: {s: number, b: string}) => void>} */ pending: new Map(),
  problem: "",                                // "" | "key" (a fresh frame did not open)
  /** @type {string[]} the functions shown with their last WiFi value in LoRa mode */ stale: [],
  last: "",                                   // the body hubPoll answered last (unchanged: null)
  /** @type {{k: string, key: CryptoKey}|null} */ key: null,
};

/** @param {string} s @returns {string} */
const hubHex = (s) => s.toLowerCase();

/** A broker URL the app may send the passwords to: wss:// only; plain ws:// only for the tests' 127.0.0.1.
 * @param {string} u */
const hubUrlOk = (u) => u.length < 96 && (/^wss:\/\/[A-Za-z0-9.-]+(:\d{1,5})?(\/[^\s*]*)?$/.test(u)
  || /^ws:\/\/127\.0\.0\.1:\d{1,5}(\/[^\s*]*)?$/.test(u));

/**
 * The hub QR text `OCH1*<van>*<wss url>*<app user>*<app pass>*<key hex32>` (cali_hub_qr_text). Null for anything
 * else. @param {string} text @returns {{van: string, url: string, user: string, pass: string, key: string} | null}
 */
function parseHubQr(text) {
  const p = text.split("*");
  if (p.length !== 6 || p[0] !== "OCH1") return null;
  const [van, url, user, pass, key] = p.slice(1);
  if (!/^[A-Za-z0-9_-]{1,23}$/.test(van) || !hubUrlOk(url) || !user || !pass || !/^[0-9a-fA-F]{32}$/.test(key)) return null;
  return { van, url, user, pass, key: hubHex(key) };
}

/** @returns {{van: string, url: string, user: string, pass: string, key: string} | null} the stored configuration */
function hubCfg() {
  const c = remoteLoad(HUB_CFG_KEY);
  return c && typeof c.van === "string" && typeof c.url === "string" && hubUrlOk(c.url) && /^[0-9a-f]{32}$/.test(c.key) ? c : null;
}

/** Store a scanned configuration. A new key restarts the LoRa command counter at 0, as the ESP does with a new data
 * key. @param {{van: string, url: string, user: string, pass: string, key: string}} cfg @returns {boolean} false:
 * not stored */
function hubSaveCfg(cfg) {
  const old = hubCfg();
  if (!old || old.key !== cfg.key) {
    const rec = remoteLoad(HUB_LORA_KEY) || {};
    if (!remoteSave(HUB_LORA_KEY, { ...rec, ctr: 0 })) return false;
  }
  if (!remoteSave(HUB_CFG_KEY, cfg)) return false;
  hubStop();
  return true;
}

/** The AES key of `cfg` (imported once). @param {{key: string}} cfg @returns {Promise<CryptoKey>} */
async function hubKey(cfg) {
  if (!hub.key || hub.key.k !== cfg.key)
    hub.key = { k: cfg.key, key: await crypto.subtle.importKey("raw", loraUnhex(cfg.key), "AES-GCM", false, ["encrypt", "decrypt"]) };
  return hub.key.key;
}

/**
 * Seals `bytes` as a frame of `dir` with this page's session (new per page load, and before the counter wraps).
 * @param {{key: string}} cfg @param {number} dir @param {Uint8Array} bytes @returns {Promise<Uint8Array>}
 */
async function hubSeal(cfg, dir, bytes) {
  if (!hub.sess || hub.ctr >= 0xffffffff) { hub.sess = crypto.getRandomValues(new Uint8Array(7)); hub.ctr = 0; }
  const c = ++hub.ctr;
  const head = new Uint8Array(14);
  head.set([1, dir, dir]);
  head.set(hub.sess, 3);
  head.set([c >>> 24, (c >>> 16) & 255, (c >>> 8) & 255, c & 255], 10);
  const ct = new Uint8Array(await crypto.subtle.encrypt(
    { name: "AES-GCM", iv: head.slice(2), additionalData: head, tagLength: 128 }, await hubKey(cfg), /** @type {Uint8Array<ArrayBuffer>} */ (bytes)));
  const out = new Uint8Array(14 + ct.length);
  out.set(head);
  out.set(ct, 14);
  return out;
}

/**
 * Opens a frame of dir 1 (the ESP: replay guard per session, as cali_hub_open) or dir 3 (the bridge: deduped by
 * fcnt later). `error`: "format" (version, dir, size), "key" (the tag: another key), "replay".
 * @param {{key: string}} cfg @param {Uint8Array} f
 * @returns {Promise<{dir: number, pt: Uint8Array, error?: undefined} | {error: string}>}
 */
async function hubOpen(cfg, f) {
  if (f.length < 30 || f[0] !== 1 || (f[1] !== 1 && f[1] !== 3) || f[2] !== f[1]) return { error: "format" };
  const s = Array.from(f.slice(3, 10), (x) => x.toString(16).padStart(2, "0")).join("");
  const c = ((f[10] << 24) | (f[11] << 16) | (f[12] << 8) | f[13]) >>> 0;
  let pt;
  try {
    pt = new Uint8Array(await crypto.subtle.decrypt(
      { name: "AES-GCM", iv: f.slice(2, 14), additionalData: f.slice(0, 14), tagLength: 128 }, await hubKey(cfg), f.slice(14)));
  } catch (e) { return { error: "key" }; }
  if (f[1] === 1) {   // only an authentic frame moves the guard
    const now = hub.ring.find((e) => e.s === s);
    if (now) { if (c <= now.c) return { error: "replay" }; now.c = c; }
    else { hub.ring.push({ s, c }); if (hub.ring.length > HUB_RING) hub.ring.shift(); }
  }
  return { dir: f[1], pt };
}

/** @param {{van: string}} cfg @param {string} sub */
const hubTopic = (cfg, sub) => "oc/" + cfg.van + "/" + sub;

/** Connect (or keep the client) for the stored configuration. */
function hubStart() {
  const cfg = hubCfg(), M = /** @type {any} */ (window).mqtt;
  if (!cfg || !M) return;
  const conn = JSON.stringify(cfg);
  if (hub.client && hub.conn === conn) return;
  hubStop();
  hub.conn = conn;
  const t = hubTopic(cfg, "");
  const c = hub.client = M.connect(cfg.url, { username: cfg.user, password: cfg.pass, protocolVersion: 4, clean: true,
    reconnectPeriod: 5000, connectTimeout: 15000 });
  c.on("connect", () => c.subscribe([t + "online", t + "state", t + "res", t + "lora/+"]));
  c.on("close", () => { hub.online = null; });   // not known until the broker answers again
  c.on("message", (/** @type {string} */ topic, /** @type {Uint8Array} */ payload, /** @type {any} */ packet) => {
    if (topic.startsWith(t)) hubMessage(cfg, topic.slice(t.length), new Uint8Array(payload), !!(packet && packet.retain));
  });
  c.on("error", () => { /* MQTT.js reconnects; a wrong password shows as no data */ });
}

/** Close the client and forget what it brought (a new configuration, "Remove this van"). */
function hubStop() {
  if (hub.client) try { hub.client.end(true); } catch (e) { /* already closed */ }
  Object.assign(hub, { client: null, conn: "", online: null, state: null, ring: [], fcnt: {}, problem: "", stale: [], last: "",
    lora: { fn: {}, ext: null, energy: null, fw: null, newest: null, unknown: null } });
  for (const done of hub.pending.values()) done({ s: 0, b: "" });
  hub.pending.clear();
}

/**
 * One frame from the broker. `sub` is the topic below oc/<van>/. A frame that does not open with the key and is
 * not retained (so the ESP or the bridge sealed it just now) means the van has another key.
 * @param {{key: string}} cfg @param {string} sub @param {Uint8Array} bytes @param {boolean} retained
 */
async function hubMessage(cfg, sub, bytes, retained) {
  if (sub === "online") { hub.online = new TextDecoder().decode(bytes); return; }
  const port = /^lora\/(\d{1,3})$/.exec(sub);
  if (sub !== "state" && sub !== "res" && !port) return;
  const o = await hubOpen(cfg, bytes);
  if (o.error !== undefined) { if (o.error === "key" && !retained) hub.problem = "key"; return; }
  if (o.dir !== (port ? 3 : 1)) return;
  hub.problem = "";
  let j;
  try { j = JSON.parse(new TextDecoder().decode(o.pt)); } catch (e) { return; }
  if (!j || typeof j !== "object") return;
  if (sub === "state") {
    hub.state = { body: j, at: Date.now(), retained };
    const eui = j.device && j.device.lora && j.device.lora.dev_eui;
    const rec = remoteLoad(HUB_LORA_KEY) || {};
    if (typeof eui === "string" && /^[0-9A-Fa-f]{16}$/.test(eui) && rec.devEui !== eui.toUpperCase())
      remoteSave(HUB_LORA_KEY, { ctr: 0, ...rec, devEui: eui.toUpperCase() });
  } else if (sub === "res") {
    const done = hub.pending.get(j.id);
    if (done && typeof j.s === "number") done({ s: j.s, b: typeof j.b === "string" ? j.b : "" });
  } else if (port) {
    const p = Number(port[1]), at = ttnParseTime(j.t);
    if (typeof j.fcnt !== "number" || typeof j.hex !== "string" || !/^([0-9a-fA-F]{2})*$/.test(j.hex) || Number.isNaN(at)) return;
    if (hub.fcnt[p] !== undefined && j.fcnt <= hub.fcnt[p]) return;   // R12: not newer than the one shown (retained)
    hub.fcnt[p] = j.fcnt;
    remoteTake(hub.lora, [], { port: p, bytes: loraUnhex(j.hex), at });
  }
}

/**
 * The `/api/state` paths LoRa ports 2, 3 and 4 can update (from loraDecodeUplink's output): `fn.<function>` and
 * `ext.<type>`. Port 3 carries any function the van changed.
 * @type {string[]}
 */
const LORA_FIELDS = (() => {
  const out = new Set();
  /** @param {any} d */
  const add = (d) => {
    for (const k of Object.keys(d.fn || {})) out.add("fn." + k);
    for (const k of Object.keys(d.ext || {})) out.add("ext." + k);
  };
  if (typeof loraDecodeUplink !== "function") return [];   // a test without the decoder
  const v1 = (/** @type {number} */ n) => { const b = new Uint8Array(n); b[0] = 1; return b; };
  add(loraDecodeUplink(2, v1(50)));
  add(loraDecodeUplink(4, v1(24)));
  for (let i = 0; i < 64; i++) {
    const d = loraDecodeUplink(3, Uint8Array.from([i, 1, 0]));
    if (d.error) break;
    add(d);
  }
  return [...out].sort();
})();

/**
 * The van as the app shows it: the ESP's state while it is live (online and at most 90 s old), else that state
 * with every function a newer LoRa frame carries replaced by it. `ageMs` is the WiFi state's age (null: not
 * known, a retained state while the ESP is not online). In LoRa mode the body is marked `device.source = "lora"`
 * with an age per function (semantics.js: `_meta.remote`), and `stale` lists the functions that show their last
 * WiFi value.
 * @param {number} [now]
 * @returns {{state: any, src: "wifi"|"lora", ageMs: number|null, online: string|null, stale: string[]}}
 */
function hubState(now = Date.now()) {
  const s = hub.state;
  const ageMs = !s ? null : !s.retained ? now - s.at : hub.online === "1" ? HUB_RETAINED_MS + now - s.at : null;
  if (hub.online === "1" && ageMs !== null && ageMs <= HUB_LIVE_MS) {
    hub.stale = [];
    return { state: s && s.body, src: "wifi", ageMs, online: hub.online, stale: [] };
  }
  if (!s && hub.lora.newest == null) return { state: null, src: "lora", ageMs, online: hub.online, stale: [] };
  const wifiAt = ageMs === null ? -Infinity : now - ageMs;
  const body = s ? JSON.parse(JSON.stringify(s.body)) : { fn: {}, device: {} };
  body.fn = body.fn || {};
  body.device = body.device || {};
  const lb = remoteBody(hub.lora, now);
  /** @type {Record<string, number>} */
  const ages = {};
  const stale = [];
  const ten = (/** @type {number} */ sec) => Math.floor(sec / 10) * 10;   // the body changes once in 10 s, not each poll
  for (const name of Object.keys(body.fn)) {
    if (hub.lora.fn[name] && hub.lora.fn[name].at > wifiAt) continue;
    stale.push(name);
    if (ageMs !== null) ages[name] = ten(ageMs / 1000);
  }
  for (const [name, v] of Object.entries(hub.lora.fn)) {
    if (v.at <= wifiAt && body.fn[name]) continue;
    body.fn[name] = v.d;
    ages[name] = ten(lb.device.ages_s[name]);
  }
  if (hub.lora.ext && hub.lora.ext.at > wifiAt) body.ext = { ...(body.ext || {}), ...lb.ext };
  const newest = Math.max(wifiAt, hub.lora.newest == null ? -Infinity : hub.lora.newest);
  body.device.source = "lora";
  body.device.ages_s = ages;
  body.device.lora_version = hub.lora.unknown;
  body.device.link = newest === -Infinity ? {} : { last_snap_age_ms: ten(now - newest) };
  hub.stale = stale.sort();
  return { state: body, src: "lora", ageMs, online: hub.online, stale: hub.stale };
}

/** app.js remotePoll in hub mode: connects, then answers the state body (null: unchanged since the last call).
 * Throws when there is nothing to show yet. */
function hubPoll() {
  hubStart();
  const h = hubState();
  if (!h.state) throw new Error("no hub data yet");
  const j = JSON.stringify(h.state);
  if (j === hub.last) return null;
  hub.last = j;
  return h.state;
}

/** The pill: "WLAN · <age>" while live, "LoRa · <age>" with LoRa data, else "offline". */
function hubPill() {
  const h = hubState();
  if (h.src === "wifi") return tf("WLAN · {ago}", { ago: agoText((h.ageMs || 0) / 1000) });
  if (hub.lora.newest == null) return T("offline");
  return tf("LoRa · {ago}", { ago: agoText(Math.max(0, (Date.now() - hub.lora.newest) / 1000)) });
}

/** In LoRa mode: does `fn` show its last WiFi value (greyed)? @param {string} fn */
const hubStale = (fn) => hub.stale.includes(fn);

/**
 * One API request over the hub: seals `{id, t, m, p, b}` (dir 2) to oc/<van>/req and waits for the answer with
 * that id on oc/<van>/res. Rejects after HUB_GIVEUP_MS.
 * @param {string} method @param {string} path @param {string|null} [body] @returns {Promise<{s: number, b: string}>}
 */
function hubRequest(method, path, body) {
  const cfg = hubCfg();
  hubStart();
  if (!cfg || !hub.client) return Promise.reject(new Error("hub: not set up"));
  const id = crypto.getRandomValues(new Uint32Array(1))[0];
  const pt = new TextEncoder().encode(JSON.stringify({ id, t: Date.now(), m: method, p: path, b: body ?? null }));
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => { hub.pending.delete(id); reject(new Error("hub: no answer")); }, HUB_GIVEUP_MS);
    hub.pending.set(id, (r) => { clearTimeout(timer); hub.pending.delete(id); resolve(r); });
    hubSeal(cfg, 2, pt).then((f) => hub.client.publish(hubTopic(cfg, "req"), f, { qos: 0, retain: false }),
      (e) => { clearTimeout(timer); hub.pending.delete(id); reject(e); });
  });
}

/** `p`'s answer, or null after `ms` (or when it failed). @template T @param {Promise<T>} p @param {number} ms
 * @returns {Promise<T|null>} */
const hubWithin = (p, ms) => Promise.race([p, new Promise((r) => setTimeout(() => r(null), ms))]).catch(() => null);

/** An answer's body as JSON (an empty or other body: `{ok: false, error: "http_<status>"}`). @param {{s: number, b: string}} r */
function hubJson(r) {
  try { const j = JSON.parse(r.b); if (j && typeof j === "object") return j; } catch (e) { /* not JSON */ }
  return { ok: false, error: "http_" + r.s };
}

/**
 * app.js api() in hub mode. Over the hub the ESP serves GET /api/* and POST /api/command only (403 local_only for
 * the rest, ruling R21): anything else is refused here before it is sent. `note`: a sentence app.js shows.
 * @param {string} path @param {RequestInit} [opts] @returns {Promise<any>}
 */
async function hubApi(path, opts) {
  const m = (opts && opts.method) || "GET", body = opts && typeof opts.body === "string" ? opts.body : null;
  if (m === "POST" && path === "/api/command") return hubCommand(body || "{}");
  if (m !== "GET" || !path.startsWith("/api/")) return { ok: false, error: "local_only", note: HUB_LOCAL_MSG };
  const r = await hubRequest(m, path, null);
  return hubJson(r);
}

/**
 * A command: over the hub; no answer in HUB_CMD_WAIT_MS and the ESP not online -> once over LoRa (heater and camping
 * mode only). With the ESP online the app waits until the ESP has given up (60 s) and then says the result is not
 * known, never sends it a second way. A 504 also means the command may still run.
 * @param {string} body the /api/command JSON @returns {Promise<any>}
 */
async function hubCommand(body) {
  let c;
  try { c = JSON.parse(body); } catch (e) { c = {}; }
  const code = HUB_LORA_CODES[[c.function, c.what, c.value].join(" ")] || 0;
  if (hub.online !== "1" && !code) return { ok: false, note: HUB_LORA_ONLY_MSG };
  const p = hubRequest("POST", "/api/command", body);
  let r = await hubWithin(p, HUB_CMD_WAIT_MS);
  if (!r && hub.online !== "1" && code) {
    p.catch(() => { /* a late answer is dropped */ });
    try {
      return (await loraCommand(code)) === "sent" ? { ok: true, note: HUB_LORA_SENT_MSG } : { ok: false, note: REMOTE_LIMIT_MSG };
    } catch (e) {
      return { ok: false, note: e instanceof TtnError || e instanceof TypeError ? rsErrText(e) : "LoRa is not set up on this phone." };
    }
  }
  if (!r) r = await hubWithin(p, HUB_GIVEUP_MS - HUB_CMD_WAIT_MS);
  if (!r || r.s === 504 || r.s === 0) return { ok: false, note: HUB_UNKNOWN_MSG };
  if (r.s === 408) return { ok: false, note: HUB_CLOCK_MSG };
  return hubJson(r);
}

/**
 * The 11-byte LoRa command: ctr(2 BE) | cmd(1) | tag(8); nonce = 0x04 | DevEUI(8) | 0x00 | ctr(2); AD = ctr
 * (cali_lora_cmd). @param {string} keyHex @param {string} devEui 16 hex @param {number} ctr @param {number} code
 * @returns {Promise<Uint8Array>}
 */
async function loraSealCmd(keyHex, devEui, ctr, code) {
  const c = Uint8Array.from([ctr >> 8, ctr & 255]);
  const iv = new Uint8Array(12);
  iv.set([4]);
  iv.set(loraUnhex(devEui), 1);
  iv.set(c, 10);
  const key = await crypto.subtle.importKey("raw", loraUnhex(keyHex), "AES-GCM", false, ["encrypt"]);
  const sealed = new Uint8Array(await crypto.subtle.encrypt({ name: "AES-GCM", iv, additionalData: c, tagLength: 64 }, key, Uint8Array.from([code])));
  const out = new Uint8Array(11);
  out.set(c);
  out.set(sealed, 2);
  return out;
}

/**
 * One LoRa command (code 1..4) to the van: counts against REMOTE_DOWNLINKS per 24 h (the setup's count, remote.js).
 * The counter is stored before the seal (it is the nonce: never twice); a push TTS refused is not counted.
 * Throws without a TTN configuration, the van's DevEUI (from a WiFi state) or the hub key.
 * @param {number} code @returns {Promise<"sent"|"limit">}
 */
async function loraCommand(code) {
  const rc = remoteCfg(), cfg = hubCfg(), dev = (remoteLoad(HUB_LORA_KEY) || {}).devEui;
  if (!rc || !rc.cmdKey || !cfg || !dev) throw new Error("LoRa commands are not set up");
  const run = async () => {
    const now = Date.now(), pushes = rsPushes({ devEui: dev }, now);
    if (pushes.length >= REMOTE_DOWNLINKS) return /** @type {const} */ ("limit");
    const rec = remoteLoad(HUB_LORA_KEY) || {}, ctr = (rec.ctr || 0) + 1;
    if (ctr > 0xffff) throw new Error("LoRa counter used up: make a new hub key");
    if (!remoteSave(HUB_LORA_KEY, { ...rec, ctr })) throw new Error("storage");
    const frame = await loraSealCmd(cfg.key, dev, ctr, code);
    rsPushSave(dev, [...pushes, now]);
    try {
      await pushDownlinks(remoteBase(rc), rc.cmdKey, rc.appId, rc.devId, [frame], 20);
    } catch (e) {
      // TTS answered with an error: nothing was queued; no answer: it may be queued, so it stays counted (rsSend)
      if (e instanceof TtnError && e.status > 0) rsPushSave(dev, pushes);
      throw e;
    }
    return /** @type {const} */ ("sent");
  };
  const locks = /** @type {any} */ (navigator).locks;
  return locks ? locks.request("oc.hub.lora", run) : run();
}
