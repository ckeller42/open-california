// @ts-check
// lora_setup.js — the browser twin of firmware/components/cali_core/lora_setup.c (R_FW_LORA_SETUP):
// the LoRa onboarding QR texts, the packed key form, the key-transfer plaintext and its AES-128-GCM
// fragments. C is the reader of what this writes: tests/test_lora_setup_js.py holds the two byte-equal.
// A classic script (like semantics.js, no module): the public names are the functions below; the helpers
// are lora*-prefixed. Not served by the ESP — the remote app loads it.

const LORA_FRAG_BODY = 50;   // CALI_LORA_FRAG_BODY: body bytes per fragment (a whole fragment is 51 with seq)
const LORA_B32 = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
const LORA_KEY_RE = /^NNSXS\.([A-Z2-7]{39})\.([A-Z2-7]{52})$/;
const LORA_ID_RE = /^[a-z0-9.-]+$/;

/** @param {string} hex @returns {Uint8Array<ArrayBuffer>} */
function loraUnhex(hex) {
  const out = new Uint8Array(hex.length / 2);
  for (let i = 0; i < out.length; i++) out[i] = parseInt(hex.substr(i * 2, 2), 16);
  return out;
}

/**
 * Packs `NNSXS.<39 b32>.<52 b32>` into 25 + 33 bytes: the bits in whole bytes, the leftover bits
 * right-aligned in one more byte per part (= cali_lora_key_pack). Null for another form.
 * @param {string} key @returns {Uint8Array | null}
 */
function packKey(key) {
  const m = LORA_KEY_RE.exec(key);
  if (!m) return null;
  const out = [];
  for (const part of [m[1], m[2]]) {
    const bits = [...part].map((c) => LORA_B32.indexOf(c).toString(2).padStart(5, "0")).join("");
    const full = bits.length - (bits.length % 8);
    for (let i = 0; i < full; i += 8) out.push(parseInt(bits.slice(i, i + 8), 2));
    out.push(parseInt(bits.slice(full), 2));
  }
  return Uint8Array.from(out);
}

/**
 * The setup QR text `OCS1*<DEVEUI>*<APPKEY>*<TK>`, any case. Null for anything else.
 * @param {string} text @returns {{devEui: string, appKey: string, tk: Uint8Array} | null}
 */
function parseSetupQr(text) {
  const m = /^OCS1\*([0-9A-F]{16})\*([0-9A-F]{32})\*([0-9A-F]{32})$/i.exec(text);
  return m ? { devEui: m[1].toUpperCase(), appKey: m[2].toUpperCase(), tk: loraUnhex(m[3]) } : null;
}

/**
 * The repair QR text `OCR1*<CLUSTER>*<APP>*<DEV>*<READ_KEY>*<CMD_KEY>`, any case; ids come back lower case.
 * @param {string} text
 * @returns {{cluster: string, appId: string, devId: string, readKey: string, cmdKey: string} | null}
 */
function parseRepairQr(text) {
  const p = text.split("*");
  if (p.length !== 6 || p[0].toUpperCase() !== "OCR1") return null;
  const [cluster, appId, devId] = p.slice(1, 4).map((s) => s.toLowerCase());
  const [readKey, cmdKey] = p.slice(4).map((s) => s.toUpperCase());
  const idOk = (/** @type {string} */ s, /** @type {number} */ max) => s.length > 0 && s.length <= max && LORA_ID_RE.test(s);
  if (!idOk(cluster, 64) || !idOk(appId, 36) || !idOk(devId, 36) || !packKey(readKey) || !packKey(cmdKey)) return null;
  return { cluster, appId, devId, readKey, cmdKey };
}

/**
 * The transfer plaintext v1: ver | len cluster | len app | len dev | read_key(58) | cmd_key(58).
 * Throws on a field the C side would refuse.
 * @param {{cluster: string, appId: string, devId: string, readKey: string, cmdKey: string}} f
 * @returns {Uint8Array}
 */
function packPlain(f) {
  const rk = packKey(f.readKey), ck = packKey(f.cmdKey);
  if (!rk || !ck) throw new Error("bad key");
  const out = [1];
  for (const [s, max] of /** @type {[string, number][]} */ ([[f.cluster, 64], [f.appId, 36], [f.devId, 36]])) {
    if (!s.length || s.length > max || !LORA_ID_RE.test(s.toLowerCase())) throw new Error("bad id");
    out.push(s.length, ...[...s].map((c) => c.charCodeAt(0)));
  }
  return Uint8Array.from([...out, ...rk, ...ck]);
}

/**
 * sha256(plain)[0..3], the check value of the confirmation uplink.
 * @param {Uint8Array<ArrayBuffer>} plain @returns {Promise<Uint8Array>}
 */
async function plainHash4(plain) {
  return new Uint8Array(await crypto.subtle.digest("SHA-256", plain)).slice(0, 4);
}

/**
 * Encrypts the plaintext (AES-128-GCM, AD = the 8 DevEUI bytes) as nonce | body | tag and cuts it into
 * fragments `seq | <=50 bytes` (seq: bit 7 = last, bits 0..2 = index), each at most 51 bytes.
 * @param {Uint8Array<ArrayBuffer>} plain @param {Uint8Array<ArrayBuffer>} tk 16 bytes @param {string} devEui 16 hex
 * @param {Uint8Array<ArrayBuffer>} [nonce] 12 bytes; random when omitted
 * @returns {Promise<Uint8Array[]>}
 */
async function sealTransfer(plain, tk, devEui, nonce) {
  const iv = nonce || crypto.getRandomValues(new Uint8Array(12));
  const key = await crypto.subtle.importKey("raw", tk, "AES-GCM", false, ["encrypt"]);
  const sealed = new Uint8Array(await crypto.subtle.encrypt(
    { name: "AES-GCM", iv, additionalData: loraUnhex(devEui), tagLength: 128 }, key, plain));
  const blob = Uint8Array.from([...iv, ...sealed]);
  const n = Math.ceil(blob.length / LORA_FRAG_BODY);
  if (n > 8) throw new Error("transfer too long");
  return Array.from({ length: n }, (_, i) =>
    Uint8Array.from([i | (i === n - 1 ? 0x80 : 0), ...blob.slice(i * LORA_FRAG_BODY, (i + 1) * LORA_FRAG_BODY)]));
}

/**
 * The confirmation uplink: status 0 `00|hash4`, 1 bad tag, 2 `02|bitmap` (bit i = fragment i received),
 * 3 no transfer key. `total` (fragments sent) turns the bitmap into `missing`. Null when malformed.
 * @param {Uint8Array} b @param {number} [total]
 * @returns {{status: number, hash4?: Uint8Array, missing?: number[]} | null}
 */
function parseConfirm(b, total) {
  const s = b[0];
  if (s === 0 && b.length === 5) return { status: 0, hash4: b.slice(1) };
  if (s === 2 && b.length === 2) {
    const missing = [];
    for (let i = 0; i < (total || 8); i++) if (!(b[1] & (1 << i))) missing.push(i);
    return { status: 2, missing };
  }
  return (s === 1 || s === 3) && b.length === 1 ? { status: s } : null;
}
