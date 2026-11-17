// Minimal MQTT 3.1.1 over WebSocket: CONNECT, wait for CONNACK, PUBLISH QoS 0 (retained), DISCONNECT.
// The transport is injected: connect(url) resolves to a WebSocket-like {send, addEventListener, close}.
const enc = new TextEncoder();

export function remainingLength(n) {
  const out = [];
  do { let b = n & 127; n >>>= 7; if (n) b |= 128; out.push(b); } while (n);
  return Uint8Array.from(out);
}

const str = (s) => { const b = enc.encode(s); return [Uint8Array.from([b.length >> 8, b.length & 255]), b]; };
const cat = (parts) => {
  const body = parts.flat(), len = body.reduce((n, p) => n + p.length, 0), out = new Uint8Array(len);
  let o = 0; for (const p of body) { out.set(p, o); o += p.length; }
  return out;
};
const packet = (first, parts) => { const body = cat(parts); return cat([[Uint8Array.of(first)], [remainingLength(body.length)], [body]]); };

/** CONNECT: clean session, username + password, `keepalive` seconds. */
export const connectPacket = ({ clientId, user, pass, keepalive = 10 }) =>
  packet(0x10, [[Uint8Array.from([0, 4, 0x4d, 0x51, 0x54, 0x54, 4, 0xc2, keepalive >> 8, keepalive & 255])],
    str(clientId), str(user), str(pass)]);

/** PUBLISH QoS 0 (no packet id); retained by default. */
export const publishPacket = (topic, payload, retain = true) => packet(0x30 | (retain ? 1 : 0), [str(topic), [payload]]);

export const DISCONNECT = Uint8Array.of(0xe0, 0);

/** Opens, publishes one retained message, closes. Throws when the broker refuses or does not answer in `ms`. */
export async function publish(connect, { url, user, pass, topic, payload, ms = 5000 }) {
  const ws = await connect(url);
  const connack = new Promise((resolve, reject) => {
    const t = setTimeout(() => reject(new Error("mqtt: no CONNACK")), ms);
    ws.addEventListener("message", async (e) => {
      const b = new Uint8Array(await new Response(e.data).arrayBuffer());
      clearTimeout(t);
      b[0] === 0x20 && b[3] === 0 ? resolve() : reject(new Error("mqtt: refused, code " + b[3]));
    });
    ws.addEventListener("close", () => { clearTimeout(t); reject(new Error("mqtt: closed")); });
    ws.addEventListener("error", () => { clearTimeout(t); reject(new Error("mqtt: socket error")); });
  });
  try {
    ws.send(connectPacket({ clientId: "oc-bridge-" + crypto.randomUUID().slice(0, 8), user, pass }));
    await connack;
    ws.send(publishPacket(topic, payload));
    ws.send(DISCONNECT);
  } finally { ws.close(); }
}
