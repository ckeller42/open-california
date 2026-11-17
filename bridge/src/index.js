// Cloudflare Worker: TTN uplink webhook -> sealed frame (dir 3) -> retained MQTT message oc/<VAN>/lora/<f_port>.
// Wire format: calictl/webui/hub.js hubOpen / firmware/components/cali_core/hub.c. Stateless: 7 random nonce bytes per
// frame, counter 1 (the app dedupes dir-3 frames by plaintext, ruling R12). Secrets are never logged.
import { publish } from "./mqtt.js";

const MAX_BODY = 16384;
const PORTS = new Set([2, 3, 4]);   // 12 = key transfer: never forwarded
const hex = (s) => Uint8Array.from(s.match(/../g) || [], (h) => parseInt(h, 16));
const bad = (status) => new Response(null, { status });

async function equal(a, b) {   // constant time: compare the SHA-256 of both
  const h = async (s) => new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(s)));
  const [x, y] = [await h(a), await h(b)];
  let d = 0; for (let i = 0; i < x.length; i++) d |= x[i] ^ y[i];
  return d === 0;
}

async function seal(keyHex, text) {
  const head = new Uint8Array(14);
  head.set([1, 3, 3]);
  crypto.getRandomValues(head.subarray(3, 10));
  head.set([0, 0, 0, 1], 10);
  const key = await crypto.subtle.importKey("raw", hex(keyHex), "AES-GCM", false, ["encrypt"]);
  const ct = new Uint8Array(await crypto.subtle.encrypt(
    { name: "AES-GCM", iv: head.slice(2), additionalData: head, tagLength: 128 }, key, new TextEncoder().encode(text)));
  const out = new Uint8Array(14 + ct.length);
  out.set(head); out.set(ct, 14);
  return out;
}

/** Outbound WebSocket in Workers: fetch with Upgrade, then accept(). */
async function wsConnect(url) {
  const r = await fetch(url.replace(/^wss:/, "https:").replace(/^ws:/, "http:"),
    { headers: { Upgrade: "websocket", "Sec-WebSocket-Protocol": "mqtt" } });
  const ws = r.webSocket;
  if (!ws) throw new Error("no websocket");
  ws.accept();
  return ws;
}

export async function handle(request, env, connect = wsConnect) {
  if (new URL(request.url).pathname !== "/ttn") return bad(404);
  if (request.method !== "POST") return bad(405);
  if (!(await equal(request.headers.get("X-OC-Secret") || "", env.WEBHOOK_SECRET || ""))) return bad(401);
  if (Number(request.headers.get("Content-Length")) > MAX_BODY) return bad(413);
  const raw = await request.arrayBuffer();
  if (raw.byteLength > MAX_BODY) return bad(413);
  let j;
  try { j = JSON.parse(new TextDecoder().decode(raw)); } catch { return bad(400); }
  const u = j && j.uplink_message;
  if (!u || typeof u !== "object") return bad(400);
  if (!PORTS.has(u.f_port)) return bad(204);
  const cnt = u.f_cnt === undefined ? 0 : u.f_cnt;   // TTN leaves out a zero f_cnt
  if (typeof j.received_at !== "string" || !Number.isInteger(cnt) || cnt < 0 || typeof u.frm_payload !== "string") return bad(400);
  let bytes;
  try { bytes = Uint8Array.from(atob(u.frm_payload), (c) => c.charCodeAt(0)); } catch { return bad(400); }
  const text = JSON.stringify({ fcnt: cnt, t: j.received_at, hex: Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("") });
  try {
    await publish(connect, { url: env.BROKER_WSS, user: env.BROKER_USER, pass: env.BROKER_PASS,
      topic: `oc/${env.VAN}/lora/${u.f_port}`, payload: await seal(env.HUB_KEY, text) });
  } catch { return bad(502); }   // TTN retries a failed webhook; no detail is logged (it could carry payload or secrets)
  return bad(204);
}

export default { fetch: (req, env) => handle(req, env) };
