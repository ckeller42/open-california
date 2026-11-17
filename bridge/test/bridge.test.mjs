// Bridge tests: node --test bridge/test. A fake WebSocket stands in for the broker.
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import { handle } from "../src/index.js";
import { connectPacket, publishPacket, remainingLength } from "../src/mqtt.js";

const KEY = "000102030405060708090a0b0c0d0e0f";
const ENV = { WEBHOOK_SECRET: "s3cret", HUB_KEY: KEY, BROKER_USER: "bridge", BROKER_PASS: "pw", VAN: "van1", BROKER_WSS: "wss://hub.example:8884/mqtt" };
const T = "2026-11-17T10:11:12.345678Z";
const body = (port, extra = {}) => JSON.stringify({ received_at: T, uplink_message: { f_port: port, f_cnt: 42, frm_payload: "AQID/w==", ...extra } });
const req = (b, headers = { "X-OC-Secret": "s3cret" }) => new Request("https://b.example/ttn", { method: "POST", headers, body: b });

/** A fake broker socket: collects what is sent, answers CONNECT with CONNACK (return code `rc`). */
function fake(rc = 0) {
  const sent = [], ls = {};
  const ws = {
    sent, closed: false,
    addEventListener: (n, f) => { ls[n] = f; },
    send(b) { sent.push(Uint8Array.from(b)); if (b[0] === 0x10) queueMicrotask(() => ls.message({ data: Uint8Array.from([0x20, 2, 0, rc]).buffer })); },
    close() { this.closed = true; },
  };
  return { ws, urls: [], connect(u) { this.urls.push(u); return Promise.resolve(ws); } };
}

async function open(frame) {   // the app's own opener (calictl/webui/hub.js), as tests/test_hub_js.py loads it
  const store = new Map();
  const ctx = vm.createContext({ fetch, Uint8Array, Uint32Array, Map, atob, btoa, URL, AbortSignal, TextEncoder, TextDecoder, console,
    crypto: globalThis.crypto, navigator: {}, clearTimeout, setTimeout,
    localStorage: { getItem: (k) => store.has(k) ? store.get(k) : null, setItem: (k, v) => store.set(k, String(v)), removeItem: (k) => store.delete(k) } });
  ctx.window = ctx;
  for (const n of ["lora_decode.js", "lora_setup.js", "ttn.js", "remote.js", "hub.js"])
    vm.runInContext(fs.readFileSync(new URL("../../calictl/webui/" + n, import.meta.url), "utf8"), ctx);
  ctx.F = frame; ctx.K = KEY;
  return vm.runInContext(`hubOpen({key: K}, F).then((o) => ({dir: o.dir, error: o.error, pt: o.pt && new TextDecoder().decode(o.pt), at: ttnParseTime("${T}")}))`, ctx);
}

test("rejects_without_secret", async () => {
  const c = fake();
  assert.equal((await handle(req(body(2), {}), ENV, c.connect.bind(c))).status, 401);
  assert.equal((await handle(req(body(2), { "X-OC-Secret": "nope" }), ENV, c.connect.bind(c))).status, 401);
  assert.equal(c.ws.sent.length, 0);
});

test("publishes_sealed_port2", async () => {
  const c = fake();
  const r = await handle(req(body(2)), ENV, c.connect.bind(c));
  assert.equal(r.status, 204);
  assert.deepEqual(c.urls, [ENV.BROKER_WSS]);
  const [conn, pub, disc] = c.ws.sent;
  assert.equal(conn[0], 0x10);
  assert.equal(pub[0], 0x31);                 // PUBLISH, QoS 0, retain
  assert.deepEqual(disc, Uint8Array.from([0xe0, 0]));
  assert.ok(c.ws.closed);
  const tl = (pub[2] << 8) | pub[3], topic = new TextDecoder().decode(pub.slice(4, 4 + tl));
  assert.equal(topic, "oc/van1/lora/2");
  const frame = pub.slice(4 + tl);            // remaining length is 1 byte here (< 128)
  assert.equal(frame[0], 1); assert.equal(frame[1], 3); assert.equal(frame[2], 3);
  assert.deepEqual([...frame.slice(10, 14)], [0, 0, 0, 1]);
  const o = await open(frame);
  assert.equal(o.dir, 3);
  assert.deepEqual(JSON.parse(o.pt), { fcnt: 42, t: T, hex: "010203ff" });
  assert.ok(!Number.isNaN(o.at));
  // another frame: fresh random bytes, not the same nonce
  const c2 = fake(); await handle(req(body(2)), ENV, c2.connect.bind(c2));
  assert.notDeepEqual(c2.ws.sent[1].slice(-60), pub.slice(-60));
});

test("ignores_port12_and_others", async () => {
  for (const p of [12, 1, 5, 20, undefined]) {
    const c = fake();
    assert.equal((await handle(req(body(p)), ENV, c.connect.bind(c))).status, 204);
    assert.equal(c.urls.length, 0, "port " + p);
  }
  for (const p of [3, 4]) { const c = fake(); await handle(req(body(p)), ENV, c.connect.bind(c)); assert.equal(c.urls.length, 1); }
});

test("bounds_and_bad_input", async () => {
  const c = fake(), h = (r) => handle(r, ENV, c.connect.bind(c));
  assert.equal((await h(req("x".repeat(20000)))).status, 413);
  assert.equal((await h(req("{nope"))).status, 400);
  assert.equal((await h(req(JSON.stringify({ uplink_message: { f_port: 2 } })))).status, 400);       // no received_at/payload
  assert.equal((await h(req(body(2, { frm_payload: "!!!" })))).status, 400);
  assert.equal((await h(new Request("https://b.example/ttn", { method: "GET" }))).status, 405);
  assert.equal(c.urls.length, 0);
});

test("broker_refusal_is_502", async () => {
  const c = fake(5);
  assert.equal((await handle(req(body(2)), ENV, c.connect.bind(c))).status, 502);
});

test("mqtt_connect_bytes", () => {
  const hex = (b) => Buffer.from(b).toString("hex");
  assert.equal(hex(connectPacket({ clientId: "cid", user: "u", pass: "pw", keepalive: 10 })),
    "10" + "16" + "0004" + "4d515454" + "04" + "c2" + "000a" + "0003" + "636964" + "0001" + "75" + "0002" + "7077");
});

test("mqtt_remaining_length_over_127", () => {
  assert.deepEqual([...remainingLength(127)], [0x7f]);
  assert.deepEqual([...remainingLength(128)], [0x80, 0x01]);
  assert.deepEqual([...remainingLength(16383)], [0xff, 0x7f]);
  assert.deepEqual([...remainingLength(16384)], [0x80, 0x80, 0x01]);
  const p = publishPacket("a/b", new Uint8Array(200));
  assert.deepEqual([...p.slice(0, 3)], [0x31, 0xcd, 0x01]);   // 2+3+200 = 205 = 0xcd 0x01
  assert.equal(p.length, 3 + 205);
});
