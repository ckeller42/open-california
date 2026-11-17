"""calictl/webui/hub.js, the remote app's hub client: sealed frames, WiFi/LoRa source, the LoRa command fallback.

``hub.js`` is a classic script. This loads it with its neighbours (lora_decode.js, lora_setup.js, ttn.js,
remote.js) into a ``node`` vm context with a fake MQTT client (the published frames are collected), a
``localStorage`` in memory and timers that run 1000 times faster, so the 30 s command wait takes 30 ms. LoRa
commands go to ``tests/e2e/fake_ttn.py``. The byte parity with the C side is in tests/firmware/test_hub.py and
tests/firmware/test_lora_cmd.py. Skipped without node.

.. test:: The hub client opens only authentic fresh frames, marks LoRa and stale values, falls back to one LoRa
   command per action and keeps the daily downlink limit
   :id: T_UI_HUB_CLIENT
   :links: R_UI_REMOTE_APP
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests" / "e2e"))
from fake_ttn import FakeTtn  # noqa: E402

WEBUI = ROOT / "calictl" / "webui"
FILES = [str(WEBUI / n) for n in ("lora_decode.js", "lora_setup.js", "ttn.js", "remote.js", "hub.js")]
KEY = "000102030405060708090a0b0c0d0e0f"
EUI = "70B3D57ED0000001"
CFG = {"van": "van1", "url": "wss://hub.example:8884/mqtt", "user": "app", "pass": "pw", "key": KEY}

# The harness: the scripts in one context; `pubs` collects what the app publishes; `seal(dir, obj)` seals as the
# ESP (dir 1) or the bridge (dir 3) would; `deliver(sub, obj|bytes, retained, dir, key)` hands a frame to the app.
_HARNESS = r"""
const fs=require("fs"),vm=require("vm"),store=new Map();
const ctx=vm.createContext({fetch,Uint8Array,Uint32Array,Map,atob,btoa,URL,AbortSignal,TextEncoder,TextDecoder,console,
  crypto:globalThis.crypto,navigator:{},clearTimeout,setTimeout:(f,ms)=>setTimeout(f,(ms||0)/1000),
  localStorage:{getItem:k=>store.has(k)?store.get(k):null,setItem:(k,v)=>store.set(k,String(v)),removeItem:k=>store.delete(k)}});
ctx.window=ctx;
for(const f of JSON.parse(process.argv[1]))vm.runInContext(fs.readFileSync(f,"utf8"),ctx,{filename:f});
ctx.B=process.argv[3];
vm.runInContext(`
var pubs=[], sess=crypto.getRandomValues(new Uint8Array(7)), ctr=0;
hub.client={publish:(t,b)=>pubs.push({t,b:Array.from(b)}),end(){}};
async function seal(dir,obj,key){
  const pt=obj instanceof Uint8Array?obj:new TextEncoder().encode(JSON.stringify(obj)),c=++ctr;
  const head=Uint8Array.from([1,dir,dir,...sess,c>>>24,(c>>>16)&255,(c>>>8)&255,c&255]);
  const k=await crypto.subtle.importKey("raw",loraUnhex(key||hubCfg().key),"AES-GCM",false,["encrypt"]);
  return Uint8Array.from([...head,...new Uint8Array(await crypto.subtle.encrypt({name:"AES-GCM",iv:head.slice(2),additionalData:head,tagLength:128},k,pt))]);
}
async function deliver(sub,obj,retained,dir,key){
  const b=sub==="online"?new TextEncoder().encode(obj):await seal(dir||(sub.startsWith("lora/")?3:1),obj,key);
  await hubMessage(hubCfg(),sub,b,!!retained); return b;
}
async function espOpen(f){
  const k=await crypto.subtle.importKey("raw",loraUnhex(hubCfg().key),"AES-GCM",false,["decrypt"]);
  return JSON.parse(new TextDecoder().decode(await crypto.subtle.decrypt({name:"AES-GCM",iv:f.slice(2,14),additionalData:f.slice(0,14),tagLength:128},k,f.slice(14))));
}
`,ctx);
vm.runInContext(`(async()=>(${process.argv[2]}))()`,ctx).then(
  r=>process.stdout.write(JSON.stringify(r)??'null'),e=>{console.error(e);process.exit(1)});
"""


def js(expr, base=""):
    """Evaluate ``expr`` (an expression, may await) with the scripts loaded; ``B`` is ``base``."""
    node = shutil.which("node") or pytest.skip("no node")
    argv = [node, "-e", _HARNESS, json.dumps(FILES), expr, base]
    return json.loads(subprocess.run(argv, capture_output=True, text=True, check=True).stdout)


SETUP = f"remoteSave(HUB_CFG_KEY,{json.dumps(CFG)})"
# the ESP's /api/state body as the hub publishes it: two functions, writes allowed, the LoRa DevEUI
STATE = {
    "fn": {"cooler": {"State": 0}, "water": {"FreshWaterLevel": 40}},
    "device": {"control": {"writes": True}, "lora": {"dev_eui": EUI}, "link": {"last_snap_age_ms": 1000}},
}


def test_parse_hub_qr():
    r = js(
        """[parseHubQr("OCH1*van_1*wss://h.example:8884/mqtt*app*p@ss*000102030405060708090A0B0C0D0E0F"),
        parseHubQr("OCH1*van1*ws://h.example/mqtt*a*p*000102030405060708090a0b0c0d0e0f"),
        parseHubQr("OCH1*van1*ws://127.0.0.1:9001*a*p*000102030405060708090a0b0c0d0e0f"),
        parseHubQr("OCH1*van/1*wss://h*a*p*000102030405060708090a0b0c0d0e0f"),
        parseHubQr("OCH1*van1*wss://h*a*p*00010203"),
        parseHubQr("OCR1*a*b*c*d*e")]"""
    )
    assert r[0] == {
        "van": "van_1",
        "url": "wss://h.example:8884/mqtt",
        "user": "app",
        "pass": "p@ss",
        "key": KEY,
    }
    assert r[1] is None  # plain ws:// only for the tests' 127.0.0.1
    assert r[2]["url"] == "ws://127.0.0.1:9001"
    assert r[3:] == [None, None, None]


def test_open_rejects_replay_and_wrong_direction():
    r = js(
        f"""(async()=>{{ {SETUP}; const cfg=hubCfg();
        const f=await seal(1,{{a:1}}), a=await hubOpen(cfg,f), again=await hubOpen(cfg,f);
        const b=await hubOpen(cfg,await seal(2,{{a:2}})), c=await hubOpen(cfg,await seal(1,{{a:3}},"ff".repeat(16)));
        const d=await hubOpen(cfg,await seal(3,{{a:4}})), e=await hubOpen(cfg,await seal(3,{{a:4}}));
        return [a.dir, new TextDecoder().decode(a.pt), again.error, b.error, c.error, d.dir, e.dir]; }})()"""
    )
    # dir 3 has no session ring (the bridge is stateless): the app dedupes it by fcnt instead
    assert r == [1, '{"a":1}', "replay", "format", "key", 3, 3]


def test_stale_when_offline():
    r = js(
        f"""(async()=>{{ {SETUP}; const out=[], now=Date.now(), s=()=>{{const h=hubState(Date.now());return [h.src,h.online]}};
        await deliver("online","1"); await deliver("state",{json.dumps(STATE)});
        out.push(s(), hubState(Date.now()+91000).src);
        await deliver("online","0"); out.push(s());
        // after a reload: a retained state while the van is offline has no known age
        hub.state=null; await deliver("state",{json.dumps(STATE)},true); out.push(hubState().ageMs);
        await deliver("online","1"); out.push(s());
        return out; }})()"""
    )
    assert r == [["wifi", "1"], "lora", ["lora", "0"], None, ["wifi", "1"]]


def test_lora_fields_marked():
    # a port-3 uplink with the cooler (function index 2) newer than the WiFi state; the water stays WiFi-only
    r = js(
        f"""(async()=>{{ {SETUP};
        await deliver("online","0"); await deliver("state",{json.dumps(STATE)});
        const t=new Date(Date.now()+5000).toISOString();
        await deliver("lora/3",{{fcnt:7,t,hex:"0202"+"0101"}});
        await deliver("lora/3",{{fcnt:7,t,hex:"0202"+"ffff"}});   // the same fcnt again (retained): ignored
        const h=hubState(Date.now()+6000), d=h.state.device;
        return {{fields: LORA_FIELDS, cooler: h.state.fn.cooler, water: h.state.fn.water, src: h.src,
          source: d.source, stale: h.stale, ages: Object.keys(d.ages_s).sort()}}; }})()"""
    )
    assert {"fn.energy", "fn.water", "fn.general", "fn.cooler", "fn.airheater", "fn.campingmode"} <= set(
        r["fields"]
    )
    assert {"ext.victron_mppt", "ext.victron_battery"} <= set(r["fields"])
    assert r["src"] == "lora" and r["source"] == "lora"
    assert r["cooler"]["Installed"] == 0 and r["cooler"]["State"] == 1  # from the LoRa frame 0x0101
    assert r["water"] == STATE["fn"]["water"]  # the WiFi value, marked stale
    assert r["stale"] == ["water"]
    assert r["ages"] == ["cooler", "water"]


def test_key_changed_message():
    r = js(
        f"""(async()=>{{ {SETUP}; const bad="ff".repeat(16);
        await deliver("state",{json.dumps(STATE)},true,1,bad); const a=hub.problem;   // a retained frame: no verdict
        await deliver("state",{json.dumps(STATE)},false,1,bad); const b=hub.problem;
        await deliver("state",{json.dumps(STATE)}); return [a,b,hub.problem,HUB_KEY_MSG]; }})()"""
    )
    assert r == ["", "key", "", "Key changed: scan the hub QR"]


def _lora_setup(fake, pushes=0):
    _, cmd = fake.make_key("cmd")
    rc = {"cluster": "eu1.cloud.thethings.network", "appId": fake.app, "devId": "van-000001", "readKey": cmd}
    rc |= {"cmdKey": cmd, "base": fake.base}
    old = f'{{"{EUI}":Array.from({{length:{pushes}}},(_,i)=>Date.now()-i*1000)}}'  # setup downlinks of the last 24 h
    return (
        f"{SETUP}; remoteSave(REMOTE_CFG_KEY,{json.dumps(rc)}); remoteSave(REMOTE_PUSH_KEY,{old});"
        f'await deliver("online","0"); await deliver("state",{json.dumps(STATE)});'
    )


HEATER_ON = json.dumps({"function": "airheater", "what": "power", "value": "on", "confirm": True})
LIGHT = json.dumps({"function": "lighting", "what": "power", "value": "on", "confirm": True})


def test_one_lora_send_per_action():
    with FakeTtn() as fake:
        r = js(
            f"""(async()=>{{ {_lora_setup(fake)}
            const post=(b)=>hubApi("/api/command",{{method:"POST",body:b}});
            const a=await post({json.dumps(HEATER_ON)}), n1=pubs.length, req=await espOpen(Uint8Array.from(pubs[0].b));
            const b=await post({json.dumps(HEATER_ON)}), c=await post({json.dumps(LIGHT)});
            return [a,b,c,n1,pubs.length,pubs[0].t,req.m,req.p,typeof req.t,remoteLoad(HUB_LORA_KEY).ctr]; }})()""",
            fake.base,
        )
        downs = list(fake.downlinks)
    a, b, c, n1, n, topic, m, p, t, ctr = r
    assert a == {"ok": True, "note": "Sent over LoRa."} == b
    assert c["ok"] is False and "LoRa" in c["note"]  # only the heater and camping mode go over LoRa: no send
    assert (n1, n, topic, m, p, t, ctr) == (1, 2, "oc/van1/req", "POST", "/api/command", "number", 2)
    assert [(d[1], len(d[2]), d[2][:2]) for d in downs] == [(20, 11, b"\x00\x01"), (20, 11, b"\x00\x02")]


def test_lora_limit():
    with FakeTtn() as fake:
        r = js(
            f"""(async()=>{{ {_lora_setup(fake, pushes=8)}
            const a=await hubApi("/api/command",{{method:"POST",body:{json.dumps(HEATER_ON)}}});
            return [a, a.note===REMOTE_LIMIT_MSG, remoteLoad(HUB_LORA_KEY).ctr || 0]; }})()""",
            fake.base,
        )
        assert fake.downlinks == []
    assert r[0]["ok"] is False and r[1] is True and r[2] == 0


def test_answer_and_504_over_the_hub():
    # the ESP answers: 200 passes through, 504 is "result unknown" (the command may still run), 403 local_only
    r = js(
        f"""(async()=>{{ {SETUP}; await deliver("online","1"); await deliver("state",{json.dumps(STATE)});
        const answer=async(s,b)=>{{ while(!pubs.length) await new Promise(r=>setTimeout(r,1));
          const q=await espOpen(Uint8Array.from(pubs.shift().b)); await deliver("res",{{id:q.id,s,b}}); }};
        const post=()=>hubApi("/api/command",{{method:"POST",body:{json.dumps(HEATER_ON)}}});
        const [a]=await Promise.all([post(), answer(200,'{{"ok":true,"applied":null}}')]);
        const [b]=await Promise.all([post(), answer(504,'{{"error":"timeout"}}')]);
        const c=await hubApi("/api/wifi",{{method:"POST",body:"{{}}"}});
        return [a,b,c,pubs.length]; }})()"""
    )
    assert r[0] == {"ok": True, "applied": None}
    assert r[1]["ok"] is False and "unknown" in r[1]["note"]
    assert r[2]["error"] == "local_only" and r[3] == 0  # refused before anything is published


def test_new_key_resets_the_lora_counter():
    r = js(
        f"""(async()=>{{ {SETUP}; remoteSave(HUB_LORA_KEY,{{ctr:5,devEui:"{EUI}"}});
        hubSaveCfg({json.dumps(CFG)}); const a=remoteLoad(HUB_LORA_KEY).ctr;
        hubSaveCfg({json.dumps({**CFG, "key": "ff" * 16})}); return [a, remoteLoad(HUB_LORA_KEY)]; }})()"""
    )
    assert r == [5, {"ctr": 0, "devEui": EUI}]
