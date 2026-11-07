// @ts-check
// ttn.js — the public remote app's client for The Things Stack HTTP API v3 (R_UI_REMOTE_APP): checks the
// full-rights key, registers the van, turns the Storage Integration on, makes and deletes keys, reads uplinks and
// pushes downlinks. A classic script (no module); tests/test_ttn_js.py runs it against tests/e2e/fake_ttn.py.
// NOT served by the ESP and not in index.html. A key is only ever sent in the Authorization header: it is
// never put in an error, a URL or a log.

const TTN_READ_RIGHTS = ["RIGHT_APPLICATION_INFO", "RIGHT_APPLICATION_DEVICES_READ", "RIGHT_APPLICATION_TRAFFIC_READ"];
const TTN_CMD_RIGHTS = [...TTN_READ_RIGHTS, "RIGHT_APPLICATION_TRAFFIC_DOWN_WRITE"];
const TTN_READ_NAME = "oc-remote-read";
const TTN_CMD_NAME = "oc-remote-cmd";
const TTN_ID_PATHS = ["ids.device_id", "ids.dev_eui", "ids.join_eui"];
const TTN_TIMEOUT_MS = 20000;   // a call (and its body) that takes longer ends as TtnError kind "timeout"

class TtnError extends Error {
  /** @param {number} status HTTP status (0 = refused before any call) @param {string} message @param {string} [kind] */
  constructor(status, message, kind) {
    super(message);
    this.name = "TtnError";
    this.status = status;
    this.kind = kind || (status === 401 ? "auth" : status === 403 ? "forbidden" : "http");
  }
}

/** A stalled call or body read (the signal fired) as TtnError kind `timeout`; any other error unchanged. @param {any} e */
function ttnAbortErr(e) {
  return e && (e.name === "TimeoutError" || e.name === "AbortError") ? new TtnError(0, "TTN did not answer in time", "timeout") : e;
}

/**
 * One API call. Throws TtnError for a status that is not 2xx or in `allow` (kind `timeout` when `signal` fires,
 * by default after TTN_TIMEOUT_MS); returns the Response, whose body read the same signal still ends.
 * @param {string} base @param {string} key @param {string} method @param {string} path
 * @param {any} [body] @param {number[]} [allow] @param {AbortSignal} [signal]
 */
async function ttnCall(base, key, method, path, body, allow, signal) {
  /** @type {Record<string, string>} */
  const headers = { Authorization: "Bearer " + key };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  let res;
  try {
    res = await fetch(base + "/api/v3" + path, {
      method, headers, body: body === undefined ? undefined : JSON.stringify(body),
      signal: signal || AbortSignal.timeout(TTN_TIMEOUT_MS),
    });
  } catch (e) { throw ttnAbortErr(e); }
  if (!res.ok && !(allow || []).includes(res.status)) throw new TtnError(res.status, `TTN ${res.status} ${method} ${path}`);
  return res;
}

/** @param {Uint8Array} b @returns {string} */
function ttnB64(b) {
  return btoa(String.fromCharCode(...b));
}

/**
 * Checks that `key` is an application key with `RIGHT_APPLICATION_ALL`, before any write.
 * TtnError kind `auth` (401), `user_key` (a user key or no application), `not_all_rights`.
 * @param {string} base @param {string} key
 * @returns {Promise<{appId: string, keyId: string}>}
 */
async function checkFullKey(base, key) {
  const j = (await (await ttnCall(base, key, "GET", "/auth_info")).json()).api_key || {};
  const appId = j.entity_ids && j.entity_ids.application_ids && j.entity_ids.application_ids.application_id;
  if (!appId) throw new TtnError(0, "This is not an application key: create the key in the application's API keys.", "user_key");
  const k = j.api_key || {};
  if (!k.id) throw new TtnError(0, "TTN did not report the key's id.", "auth");
  if (!(k.rights || []).includes("RIGHT_APPLICATION_ALL"))
    throw new TtnError(0, 'The key needs "Grant all current and future rights".', "not_all_rights");
  return { appId, keyId: k.id };
}

/**
 * Registers the van: IS create (409 = exists, go on), then JS, NS and AS set.
 * @param {string} base @param {string} key
 * @param {{appId: string, devId: string, devEui: string, appKey: string}} dev
 */
async function registerDevice(base, key, { appId, devId, devEui, appKey }) {
  const host = new URL(base).hostname;
  const ids = { device_id: devId, dev_eui: devEui, join_eui: "0000000000000000", application_ids: { application_id: appId } };
  /** @param {string} reg @param {object} end_device @param {string[]} paths */
  const put = (reg, end_device, paths) => ttnCall(base, key, "PUT", `/${reg}/applications/${appId}/devices/${devId}`,
    { end_device: { ids, ...end_device }, field_mask: { paths: [...paths, ...TTN_ID_PATHS] } });
  const hosts = { network_server_address: host, application_server_address: host };
  await ttnCall(base, key, "POST", `/applications/${appId}/devices`, {
    end_device: { ids, join_server_address: host, ...hosts },
    field_mask: { paths: ["join_server_address", "network_server_address", "application_server_address", ...TTN_ID_PATHS] },
  }, [409]);
  await put("js", { ...hosts, root_keys: { app_key: { key: appKey } } },
    ["network_server_address", "application_server_address", "root_keys.app_key.key"]);
  await put("ns", {
    frequency_plan_id: "EU_863_870_TTN", lorawan_version: "MAC_V1_0_3", lorawan_phy_version: "PHY_V1_0_3_REV_A",
    supports_join: true, supports_class_c: true,
  }, ["frequency_plan_id", "lorawan_version", "lorawan_phy_version", "supports_join", "supports_class_c"]);
  await put("as", {}, []);
}

/**
 * Turns the Storage Integration on for the application (the one call verified live on 2026-11-04).
 * @param {string} base @param {string} key @param {string} appId
 */
async function enableStorage(base, key, appId) {
  await ttnCall(base, key, "PUT", `/as/applications/${appId}/packages/associations/100`,
    { default: { package_name: "storage-integration" }, field_mask: { paths: ["package_name"] } });
}

/**
 * Makes the phone's read and command keys of the van `devId` (`oc-remote-read-<devId>`, `oc-remote-cmd-<devId>`),
 * then deletes that van's older ones: another van in the same application keeps its keys, and a failed create
 * leaves the old keys working. A lost phone's keys go by a new setup of the van (or in the console).
 * @param {string} base @param {string} key @param {string} appId @param {string} devId
 * @returns {Promise<{readKey: string, cmdKey: string}>}
 */
async function makePhoneKeys(base, key, appId, devId) {
  const names = [TTN_READ_NAME + "-" + devId, TTN_CMD_NAME + "-" + devId];
  const old = (await (await ttnCall(base, key, "GET", `/applications/${appId}/api-keys`)).json()).api_keys || [];
  /** @param {string} name @param {string[]} rights */
  const make = async (name, rights) =>
    (await (await ttnCall(base, key, "POST", `/applications/${appId}/api-keys`, { name, rights })).json()).key;
  const readKey = await make(names[0], TTN_READ_RIGHTS), cmdKey = await make(names[1], TTN_CMD_RIGHTS);
  for (const k of old)
    if (names.includes(k.name)) await ttnCall(base, key, "DELETE", `/applications/${appId}/api-keys/${k.id}`, undefined, [404]);
  return { readKey, cmdKey };
}

/**
 * Deletes the key `keyId` (the caller's own key may delete itself; it gets 401 afterwards).
 * @param {string} base @param {string} key @param {string} appId @param {string} keyId
 */
async function deleteOwnKey(base, key, appId, keyId) {
  await ttnCall(base, key, "DELETE", `/applications/${appId}/api-keys/${keyId}`);
}

/**
 * Reads stored uplinks, newest first (Storage API, newline-delimited JSON).
 * @param {string} base @param {string} readKey @param {string} appId @param {string} devId
 * @param {{after?: number | string, limit?: number, signal?: AbortSignal}} [opt] `after` = ms since epoch, or a raw
 *   RFC3339(Nano) string passed through unchanged; `signal` replaces the default timeout
 * @returns {Promise<Array<{port: number, bytes: Uint8Array, at: number, receivedAt: string}>>} `receivedAt` is the raw
 *   timestamp: pass the newest one as `after` (unchanged) for the next read; `at` is its ms value
 */
async function readUplinks(base, readKey, appId, devId, opt) {
  const { after, limit, signal } = opt || {};
  let q = "?order=-received_at";
  if (limit) q += "&limit=" + limit;
  if (after !== undefined) q += "&after=" + encodeURIComponent(typeof after === "number" ? new Date(after).toISOString() : after);
  const res = await ttnCall(base, readKey, "GET", `/as/applications/${appId}/devices/${devId}/packages/storage/uplink_message${q}`, undefined, undefined, signal);
  const bad = (/** @type {string} */ why) => new TtnError(res.status, "TTN storage stream: " + why, "stream");
  /** @type {Array<{port: number, bytes: Uint8Array, at: number, receivedAt: string}>} */
  const out = [];
  let text;
  try { text = await res.text(); } catch (e) { throw ttnAbortErr(e); }
  for (const line of text.split("\n")) {
    if (!line.trim()) continue;
    let o;
    try { o = JSON.parse(line); } catch (e) { throw bad("unparsable line"); }
    if (o.error) throw bad("error line" + (o.error.code === undefined ? "" : " code " + o.error.code));
    const r = o.result;
    if (!r || !r.uplink_message) continue;
    const at = ttnParseTime(r.received_at);
    if (Number.isNaN(at)) throw bad("bad received_at");
    out.push({
      port: r.uplink_message.f_port,
      bytes: Uint8Array.from(atob(r.uplink_message.frm_payload || ""), (c) => c.charCodeAt(0)),
      at, receivedAt: r.received_at,
    });
  }
  return out;
}

/**
 * RFC3339(Nano) to ms since epoch, by hand (seconds + the first 3 fraction digits), so every browser agrees.
 * NaN when malformed.
 * @param {string} t @returns {number}
 */
function ttnParseTime(t) {
  const m = /^(\d{4})-(\d\d)-(\d\d)T(\d\d):(\d\d):(\d\d)(?:\.(\d+))?(?:Z|([+-])(\d\d):(\d\d))$/.exec(String(t));
  if (!m) return NaN;
  const [y, mo, d, h, mi, sec] = m.slice(1, 7).map(Number);
  const ms = Number(((m[7] || "") + "000").slice(0, 3));
  const off = m[8] ? (m[8] === "-" ? -1 : 1) * (Number(m[9]) * 60 + Number(m[10])) * 60000 : 0;
  return Date.UTC(y, mo - 1, d, h, mi, sec, ms) - off;
}

/**
 * Queues unconfirmed downlinks on `port`.
 * @param {string} base @param {string} cmdKey @param {string} appId @param {string} devId
 * @param {Uint8Array[]} frames @param {number} port
 */
async function pushDownlinks(base, cmdKey, appId, devId, frames, port) {
  await ttnCall(base, cmdKey, "POST", `/as/applications/${appId}/devices/${devId}/down/push`,
    { downlinks: frames.map((f) => ({ f_port: port, frm_payload: ttnB64(f), priority: "NORMAL" })) });
}
