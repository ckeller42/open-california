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

class TtnError extends Error {
  /** @param {number} status HTTP status (0 = refused before any call) @param {string} message @param {string} [kind] */
  constructor(status, message, kind) {
    super(message);
    this.name = "TtnError";
    this.status = status;
    this.kind = kind || (status === 401 ? "auth" : status === 403 ? "forbidden" : "http");
  }
}

/**
 * One API call. Throws TtnError for a status that is not 2xx or in `allow`; returns the Response.
 * @param {string} base @param {string} key @param {string} method @param {string} path
 * @param {any} [body] @param {number[]} [allow]
 */
async function ttnCall(base, key, method, path, body, allow) {
  /** @type {Record<string, string>} */
  const headers = { Authorization: "Bearer " + key };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const res = await fetch(base + "/api/v3" + path, {
    method, headers, body: body === undefined ? undefined : JSON.stringify(body),
  });
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
 * Makes the phone's read and command keys, after deleting older ones of the same names.
 * @param {string} base @param {string} key @param {string} appId
 * @returns {Promise<{readKey: string, cmdKey: string}>}
 */
async function makePhoneKeys(base, key, appId) {
  const old = (await (await ttnCall(base, key, "GET", `/applications/${appId}/api-keys`)).json()).api_keys || [];
  for (const k of old)
    if (k.name === TTN_READ_NAME || k.name === TTN_CMD_NAME) await ttnCall(base, key, "DELETE", `/applications/${appId}/api-keys/${k.id}`);
  /** @param {string} name @param {string[]} rights */
  const make = async (name, rights) =>
    (await (await ttnCall(base, key, "POST", `/applications/${appId}/api-keys`, { name, rights })).json()).key;
  const readKey = await make(TTN_READ_NAME, TTN_READ_RIGHTS);
  return { readKey, cmdKey: await make(TTN_CMD_NAME, TTN_CMD_RIGHTS) };
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
 * @param {{after?: number | string, limit?: number}} [opt] `after` = ms since epoch or an RFC3339 string
 * @returns {Promise<Array<{port: number, bytes: Uint8Array, at: number}>>}
 */
async function readUplinks(base, readKey, appId, devId, opt) {
  const { after, limit } = opt || {};
  let q = "?order=-received_at";
  if (limit) q += "&limit=" + limit;
  if (after !== undefined) q += "&after=" + encodeURIComponent(typeof after === "number" ? new Date(after).toISOString() : after);
  const res = await ttnCall(base, readKey, "GET", `/as/applications/${appId}/devices/${devId}/packages/storage/uplink_message${q}`);
  return (await res.text()).split("\n").filter((l) => l.trim()).map((l) => JSON.parse(l).result)
    .filter((r) => r && r.uplink_message)
    .map((r) => ({
      port: r.uplink_message.f_port,
      bytes: Uint8Array.from(atob(r.uplink_message.frm_payload || ""), (c) => c.charCodeAt(0)),
      at: Date.parse(r.received_at),
    }));
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
