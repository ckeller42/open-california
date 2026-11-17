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
hub.client={connected:true,publish:(t,b)=>pubs.push({t,b:Array.from(b)}),end(){}};
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


# the ESP's /api/state?hub=1 body as the hub publishes it: two functions, writes allowed, the LoRa DevEUI, the last
# accepted LoRa command counter 7; st(dt) adds its wall clock hub_t = now + dt ms
STATE = {
    "lora_ctr": 7,
    "fn": {"cooler": {"State": 0, "Gone": 5}, "water": {"FreshWaterLevel": 40}},
    "ext": {
        "Kitchen": {"type": "govee", "temp_c": 21.5, "age_s": 10},
        "Solar": {"type": "victron_mppt", "pv_power": 3, "age_s": 2},
    },
    "device": {"control": {"writes": True}, "lora": {"dev_eui": EUI}, "link": {"last_snap_age_ms": 1000}},
}
SETUP = (
    f"remoteSave(HUB_CFG_KEY,{json.dumps(CFG)}); const S={json.dumps(STATE)};"
    "const st=(dt=0)=>({...S,hub_t:Date.now()+dt})"
)


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
    """Ruling R26: live only with a sealed state whose hub_t is at most 90 s old; the online flag can only take
    "live" away. A state without a clock (the ESP has no SNTP time) ages from its receive time, except a retained one:
    its receive time is the subscribe, so its age is unknown and it is never live."""
    r = js(
        f"""(async()=>{{ {SETUP}; const out=[], s=()=>{{const h=hubState(Date.now());return [h.src,h.online]}};
        await deliver("online","1"); await deliver("state",st(-5000));
        out.push(s(), hubState(Date.now()+86000).src, Math.round(hubState().ageMs/1000));
        await deliver("online","0"); out.push(s());
        await deliver("online","1"); await deliver("state",st(-91000)); out.push(hubState().src);   // older: not taken
        await deliver("state",{{...S,hub_t:null}},true); out.push(hubState().src, hubState().ageMs);   // no clock, retained
        await deliver("state",{{...S,hub_t:null}}); out.push(hubState().src, hubState(Date.now()+91000).src);   // fresh
        hubStop(); await deliver("state",st(-100000)); out.push(hubState().src);   // only the flag says online
        return out; }})()"""
    )
    assert r == [["wifi", "1"], "lora", 5, ["lora", "0"], "wifi", "lora", None, "wifi", "lora", "lora"]


def test_reload_rejects_replays_and_keeps_what_was_shown():
    """Ruling R26: the ESP sessions' counters, the fcnt per port, the state and the LoRa frames survive a reload
    (hubStop forgets the page's memory, oc.hub.seen stays): a replayed state or LoRa frame is not taken again."""
    r = js(
        f"""(async()=>{{ {SETUP}; const cfg=hubCfg(), t=new Date(Date.now()+5000).toISOString();
        const a=await deliver("state",st()), b=await deliver("lora/3",{{fcnt:9,t,hex:"02020101"}});
        hubStop(); await deliver("online","0");   // a reload; the van has gone offline since
        const ra=await hubOpen(cfg,a); await hubMessage(cfg,"lora/3",b,true);
        await deliver("lora/3",{{fcnt:9,t,hex:"0202ffff"}},true);   // the same fcnt, other bytes: ignored
        const h=hubState(); return [ra.error, h.state.fn.cooler.State, h.state.fn.water, hubSeen().fcnt]; }})()"""
    )
    assert r[:3] == ["replay", 1, STATE["fn"]["water"]] and r[3]["3"]["fcnt"] == 9


def test_lora_fields_marked():
    """Per signal: a LoRa frame newer than the WiFi state updates the fields it carries (LORA_FIELDS) inside the
    WiFi function; every other field keeps its WiFi value, is marked stale and the function shows the WiFi age. The
    ext rule is the same: a WiFi-only sensor is stale and its age grows by the WiFi age."""
    r = js(
        f"""(async()=>{{ {SETUP};
        await deliver("online","0"); await deliver("state",st(-60000));
        const t=new Date(Date.now()+5000).toISOString();
        await deliver("lora/3",{{fcnt:7,t,hex:"0202"+"0101"}});
        await deliver("lora/3",{{fcnt:7,t,hex:"0202"+"ffff"}});   // a byte-identical replay's twin: ignored
        await deliver("lora/2",{{fcnt:1,t,hex:"01"+"00".repeat(24)+"ff".repeat(6)+"0017005700000000ffff"+"00".repeat(9)}});
        const h=hubState(Date.now()+6000), d=h.state.device;
        return {{fields: LORA_FIELDS, cooler: h.state.fn.cooler, water: h.state.fn.water, src: h.src, ext: h.state.ext,
          source: d.source, stale: h.stale, ages: d.ages_s, grey: ["cooler","water","energy","ext.Kitchen","ext.Solar"].map(hubStale)}}; }})()"""
    )
    f = set(r["fields"])
    assert {"fn.cooler.State", "fn.cooler.Mode", "fn.water.FreshWaterLevel", "fn.airheater.HeatingLevel"} <= f
    assert {"fn.general.CmSwVersion", "ext.victron_mppt.pv_power", "ext.victron_battery.battery_voltage"} <= f
    assert not any(x.startswith("fn.cooler.Gone") or x.startswith("ext.govee") for x in f)
    assert r["src"] == "lora" and r["source"] == "lora"
    # merged per field: State from the LoRa frame 0x0101, the WiFi-only field stays (greyed)
    assert r["cooler"]["State"] == 1 and r["cooler"]["Installed"] == 0 and r["cooler"]["Gone"] == 5
    assert r["water"] == STATE["fn"]["water"]
    assert r["stale"] == ["ext.Kitchen", "fn.cooler.Gone", "fn.water.FreshWaterLevel"]
    assert r["grey"] == [True, True, False, True, False]
    assert r["ages"]["water"] == 60 and r["ages"]["cooler"] == 60 and r["ages"]["energy"] == 0
    assert r["ext"]["Kitchen"]["age_s"] == 70  # 10 s at the publish + the WiFi state's 60 s
    assert r["ext"]["Solar"]["pv_power"] == 87 and r["ext"]["Solar"]["age_s"] == 0  # LoRa port 2


def test_empty_function_object_in_lora_mode():
    """I1: the ESP writes `"<fn>":{}` for a frame it cannot decode (cali_snapshot_fn); in LoRa mode with no LoRa frame
    for that function hubState must not throw, and the empty function keeps the WiFi age."""
    r = js(
        f"""(async()=>{{ {SETUP}; await deliver("online","0");
        await deliver("state",{{...st(-60000),fn:{{...S.fn,roof:{{}}}}}});
        const h=hubState(); return [h.src, h.state.fn.roof, h.state.device.ages_s.roof]; }})()"""
    )
    assert r == ["lora", {}, 60]


def test_wrong_van_clock_does_not_lock_out_later_states():
    """I2: a state whose hub_t is far ahead of the phone (a wrong unit clock, or a store from an older firmware) is
    not trusted: it neither blocks the next, correct state nor counts as fresh; a stored one heals at the next state."""
    r = js(
        f"""(async()=>{{ {SETUP}; await deliver("online","1");
        await deliver("state",{{...st(86400000),n:1}}); const a=[hubState().src, Math.round(hubState().ageMs/1000)];
        const b=hubState(Date.now()+91000).src;   // aged from its receive time, not its future hub_t
        await deliver("state",{{...st(-5000),n:2}}); const c=[hubSeen().state.n, hubState().src];
        await deliver("state",{{...st(-20000),n:3}}); const d=hubSeen().state.n;   // a trusted older one: dropped
        return [a,b,c,d]; }})()"""
    )
    assert r == [["wifi", 0], "lora", [2, "wifi"], 2]


def test_no_network_time_on_the_van():
    """I3: the ESP answers a command with 503 while it has no SNTP time; the app says so and does not use LoRa."""
    r = js(
        f"""(async()=>{{ {SETUP}; await deliver("online","1"); await deliver("state",st());
        const answer=async(b)=>{{ while(!pubs.length) await new Promise(r=>setTimeout(r,1));
          const q=await espOpen(Uint8Array.from(pubs.shift().b)); await deliver("res",{{id:q.id,s:503,b}}); }};
        const post=()=>hubApi("/api/command",{{method:"POST",body:{json.dumps(HEATER_ON)}}});
        const [a]=await Promise.all([post(), answer("no_time")]);
        const [b]=await Promise.all([post(), answer('{{"ok":false,"error":"not_connected"}}')]);
        return [a, HUB_NOTIME_MSG, b]; }})()"""
    )
    assert r[0] == {"ok": False, "note": r[1]}
    # the camper link's 503 not_connected passes through as JSON: app.js retries it
    assert r[2] == {"ok": False, "error": "not_connected"}


def test_rejoin_restarts_fcnt():
    """R12 after a rejoin: a frame with a lower fcnt but a newer (sealed) uplink time is taken; a byte-identical
    replay and an older one are not."""
    r = js(
        f"""(async()=>{{ {SETUP}; await deliver("online","0");
        const t0=new Date(Date.now()-60000).toISOString(), t1=new Date().toISOString(), c=()=>hubSeen().lora.fn.cooler.d.State;
        await deliver("lora/3",{{fcnt:900,t:t0,hex:"02020000"}}); const a=c();
        const f=await deliver("lora/3",{{fcnt:1,t:t1,hex:"02020101"}}); const b=c();
        await deliver("lora/3",{{fcnt:900,t:t0,hex:"02020000"}}); const d=c();   // the old frame again
        await hubMessage(hubCfg(),"lora/3",f,true); return [a,b,d,hubSeen().fcnt["3"]]; }})()"""
    )
    assert r[:3] == [0, 1, 1] and r[3]["fcnt"] == 1


def test_not_connected_goes_to_lora_at_once():
    """No broker connection: hubRequest rejects at once (MQTT.js would queue a QoS 0 req and send it after the LoRa
    command); the heater goes over LoRa without the 30 s wait, a light command says there is no connection."""
    with FakeTtn() as fake:
        r = js(
            f"""(async()=>{{ {_lora_setup(fake)} hub.client.connected=false;
            const q=await hubRequest("GET","/api/state").then(()=>"answer",e=>e.message);
            const st0=setTimeout; setTimeout=(f,ms)=>ms===HUB_CMD_WAIT_MS?0:st0(f,ms);   // the 30 s wait never ends
            const a=await hubApi("/api/command",{{method:"POST",body:{json.dumps(HEATER_ON)}}});
            const b=await hubApi("/api/command",{{method:"POST",body:{json.dumps(LIGHT)}}});
            return [q, a.note, pubs.length, b.note===HUB_NOCONN_MSG]; }})()""",
            fake.base,
        )
        assert len(fake.downlinks) == 1
    assert r == ["hub: not connected", "Sent over LoRa.", 0, False]  # offline: the light is LoRa-only


def test_late_phone_clock_does_not_keep_a_state_live():
    """R28: age = max(now - hub_t, now - local receive time): a phone clock 5 min behind the van still ages."""
    r = js(
        f"""(async()=>{{ {SETUP}; await deliver("online","1"); await deliver("state",st(300000));
        return [hubState().src, hubState(Date.now()+91000).src, Math.round(hubState(Date.now()+91000).ageMs/1000)]; }})()"""
    )
    assert r == ["wifi", "lora", 91]


def test_messages_open_in_arrival_order():
    """R28: frames are opened one after the other: a big res (counter 1) that decrypts slower than the state after it
    (counter 2) of the same ESP session is not taken as a replay."""
    r = js(
        f"""(async()=>{{ {SETUP}; let got=null; hub.pending.set(77,(x)=>{{got=x.s}});
        const a=await seal(1,{{id:77,s:200,b:"x".repeat(4000000)}}), b=await seal(1,st());
        await Promise.all([hubMessage(hubCfg(),"res",a,false), hubMessage(hubCfg(),"state",b,false)]);
        return [got, hubSeen().state.lora_ctr]; }})()"""
    )
    assert r == [200, 7]


def test_key_changed_message():
    r = js(
        f"""(async()=>{{ {SETUP}; const bad="ff".repeat(16);
        await deliver("state",st(),true,1,bad); const a=hub.problem;   // a retained frame: no verdict
        await deliver("state",st(),false,1,bad); const b=hub.problem;
        await deliver("state",st()); return [a,b,hub.problem,HUB_KEY_MSG]; }})()"""
    )
    assert r == ["", "key", "", "Key changed: scan the hub QR"]


def _lora_setup(fake, pushes=0):
    _, cmd = fake.make_key("cmd")
    rc = {"cluster": "eu1.cloud.thethings.network", "appId": fake.app, "devId": "van-000001", "readKey": cmd}
    rc |= {"cmdKey": cmd, "base": fake.base}
    old = f'{{"{EUI}":Array.from({{length:{pushes}}},(_,i)=>Date.now()-i*1000)}}'  # setup downlinks of the last 24 h
    return (
        f"{SETUP}; remoteSave(REMOTE_CFG_KEY,{json.dumps(rc)}); remoteSave(REMOTE_PUSH_KEY,{old});"
        f'await deliver("online","0"); await deliver("state",st());'
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
    assert (n1, n, topic, m, p, t, ctr) == (1, 2, "oc/van1/req", "POST", "/api/command", "number", 9)
    # R25: phone(4) | ctr | cmd | tag; the counter goes on from the ESP's lora_ctr 7; one phone id
    assert [(d[1], len(d[2]), d[2][4:6]) for d in downs] == [(20, 15, b"\x00\x08"), (20, 15, b"\x00\x09")]
    assert downs[0][2][:4] == downs[1][2][:4]


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
        f"""(async()=>{{ {SETUP}; await deliver("online","1"); await deliver("state",st());
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
        f"""(async()=>{{ {SETUP}; remoteSave(HUB_LORA_KEY,{{ctr:5,devEui:"{EUI}",phone:"0badf00d"}});
        hubSaveCfg({json.dumps(CFG)}); const a=remoteLoad(HUB_LORA_KEY).ctr;
        hubSaveCfg({json.dumps({**CFG, "key": "ff" * 16})}); return [a, remoteLoad(HUB_LORA_KEY)]; }})()"""
    )
    assert r == [5, {"ctr": 0, "devEui": EUI, "phone": "0badf00d"}]  # the phone id stays (R25)
