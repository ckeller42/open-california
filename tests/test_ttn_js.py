"""calictl/webui/ttn.js, the remote app's The Things Stack client, against the fake TTN (tests/e2e/fake_ttn.py).

``ttn.js`` is a classic script; this loads it into a ``node`` vm context with ``fetch`` and runs its calls against
``FakeTtn`` on a free port, then reads the fake's call log. No real TTN server is contacted. Skipped without node.

.. test:: The TTN client sets a van up, refuses wrong keys before any write, is idempotent and respects key rights
   :id: T_UI_TTN_CLIENT
   :links: R_UI_REMOTE_APP
"""

import json
import shutil
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests" / "e2e"))
from fake_ttn import FakeTtn  # noqa: E402

JS = ROOT / "calictl" / "webui" / "ttn.js"
DEVEUI = "70B3D57ED0000001"
APPKEY = bytes(range(0, 0x100, 0x11)).hex().upper()
DEV = {"devId": "van-000001", "devEui": DEVEUI, "appKey": APPKEY}

_HARNESS = (
    'const fs=require("fs"),vm=require("vm");'
    "const ctx=vm.createContext({fetch,Uint8Array,atob,btoa,URL,AbortSignal});"
    'vm.runInContext(fs.readFileSync(process.argv[1],"utf8"),ctx,{filename:process.argv[1]});'
    "ctx.B=process.argv[3];ctx.APP=process.argv[4];"
    "ctx.err=e=>({err:e.name,kind:e.kind,status:e.status,msg:e.message});"
    "vm.runInContext(`(async()=>(${process.argv[2]}))()`,ctx).then("
    "r=>process.stdout.write(JSON.stringify(r)??'null'),e=>{console.error(e);process.exit(1)});"
)


@pytest.fixture
def fake():
    """A fake TTN on a free port, stopped at teardown."""
    with FakeTtn() as f:
        yield f


def js(fake, expr):
    """Evaluate ``expr`` with ttn.js loaded; ``B`` is the fake's base URL, ``APP`` its application id."""
    node = shutil.which("node") or pytest.skip("no node")
    argv = [node, "-e", _HARNESS, str(JS), expr, fake.base, fake.app]
    return json.loads(subprocess.run(argv, capture_output=True, text=True, check=True).stdout)


def full_setup(fake, secret):
    """Run the whole first setup with ``secret``; returns the phone keys."""
    d = json.dumps({**DEV, "appId": fake.app})
    return js(
        fake,
        f"""(async()=>{{
      const k={json.dumps(secret)}, c=await checkFullKey(B,k), d={d};
      await registerDevice(B,k,d); await enableStorage(B,k,APP);
      const p=await makePhoneKeys(B,k,APP,d.devId); await deleteOwnKey(B,k,APP,c.keyId); return {{c,p}}}})()""",
    )


def test_full_setup_calls_in_order(fake):
    kid, secret = fake.make_key("all")
    out = full_setup(fake, secret)
    a, d = fake.app, "van-000001"
    assert out["c"] == {"appId": a, "keyId": kid}
    assert [(m, p.split("?")[0]) for m, p, _ in fake.calls] == [
        ("GET", "/api/v3/auth_info"),
        ("POST", f"/api/v3/applications/{a}/devices"),
        ("PUT", f"/api/v3/js/applications/{a}/devices/{d}"),
        ("PUT", f"/api/v3/ns/applications/{a}/devices/{d}"),
        ("PUT", f"/api/v3/as/applications/{a}/devices/{d}"),
        ("PUT", f"/api/v3/as/applications/{a}/packages/associations/100"),
        ("GET", f"/api/v3/applications/{a}/api-keys"),
        ("POST", f"/api/v3/applications/{a}/api-keys"),
        ("POST", f"/api/v3/applications/{a}/api-keys"),
        ("DELETE", f"/api/v3/applications/{a}/api-keys/{kid}"),
    ]
    assert {c[2] for c in fake.calls} == {kid}  # every setup call used the full key; the log holds ids only
    assert fake.associations == {100: "storage-integration"}
    dev = fake.devices[d]
    assert dev["registry"] == ["is", "js", "ns", "as"]
    assert dev["ns"]["frequency_plan_id"] == "EU_863_870_TTN" and dev["ns"]["supports_class_c"] is True
    assert dev["js"]["root_keys"]["app_key"]["key"] == APPKEY
    assert dev["ns"]["supports_class_c"] is True and dev["js"]["root_keys"]["app_key"]["key"] == APPKEY
    assert dev["masks"]["ns"][-3:] == ["ids.device_id", "ids.dev_eui", "ids.join_eui"]
    # the full key is gone: 401
    again = js(fake, f"checkFullKey(B,{json.dumps(secret)}).catch(err)")
    assert again["status"] == 401 and again["kind"] == "auth"
    assert secret not in json.dumps(fake.calls) and secret not in again["msg"]
    # the new keys have the Global-Constraints rights
    rights = {v["name"]: v["rights"] for v in fake.keys.values()}
    assert rights["oc-remote-read-van-000001"] == [
        "RIGHT_APPLICATION_INFO",
        "RIGHT_APPLICATION_DEVICES_READ",
        "RIGHT_APPLICATION_TRAFFIC_READ",
    ]
    assert rights["oc-remote-cmd-van-000001"][-1] == "RIGHT_APPLICATION_TRAFFIC_DOWN_WRITE"


def test_setup_refuses_wrong_key_kinds(fake):
    for kind, want in [("user", "user_key"), ("read", "not_all_rights")]:
        _, secret = fake.make_key(kind)
        r = js(fake, f"checkFullKey(B,{json.dumps(secret)}).catch(err)")
        assert r["kind"] == want, kind
    wrong = "NNSXS." + "A" * 39 + "." + "B" * 52  # a key of another cluster: unknown here, answered 401
    assert js(fake, f"checkFullKey(B,{json.dumps(wrong)}).catch(err)")["kind"] == "auth"
    assert fake.writes() == []
    assert wrong not in json.dumps(fake.calls)


def test_rerun_is_idempotent(fake):
    for _ in range(2):
        _, secret = fake.make_key("all")
        full_setup(fake, secret)
    assert list(fake.devices) == ["van-000001"]
    assert sorted(v["name"] for v in fake.keys.values()) == [
        "oc-remote-cmd-van-000001",
        "oc-remote-read-van-000001",
    ]


def test_phone_keys_are_per_van_and_made_before_the_old_ones_go(fake):
    """A second van in the same application keeps the first van's keys; a failed create keeps the old keys."""
    _, secret = fake.make_key("all")
    first = full_setup(fake, secret)["p"]
    _, k2 = fake.make_key("all")
    js(fake, f"makePhoneKeys(B,{json.dumps(k2)},APP,'van-000002')")
    live = {v["name"] for v in fake.keys.values()}
    assert {"oc-remote-read-van-000001", "oc-remote-cmd-van-000001"} <= live
    assert {"oc-remote-read-van-000002", "oc-remote-cmd-van-000002"} <= live
    fake.refuse_key_create = True
    r = js(fake, f"makePhoneKeys(B,{json.dumps(k2)},APP,'van-000001').catch(err)")
    assert r["status"] == 500
    assert first["readKey"] in fake.keys and first["cmdKey"] in fake.keys, "the van's working keys stay"
    fake.refuse_key_create = False
    again = js(fake, f"makePhoneKeys(B,{json.dumps(k2)},APP,'van-000001')")
    assert (
        first["readKey"] not in fake.keys and again["readKey"] in fake.keys
    )  # a new setup revokes the old keys
    assert [v["name"] for v in fake.keys.values()].count("oc-remote-read-van-000001") == 1


def test_phone_keys_of_the_unsuffixed_names_are_revoked(fake):
    """Phone keys made under the first versions' names (oc-remote-read / oc-remote-cmd) do not outlive a setup."""
    old = [fake._add_key("created", n, [])[1] for n in ("oc-remote-read", "oc-remote-cmd")]
    _, secret = fake.make_key("all")
    full_setup(fake, secret)
    assert not [s for s in old if s in fake.keys]
    assert {v["name"] for v in fake.keys.values()} == {
        "oc-remote-read-van-000001",
        "oc-remote-cmd-van-000001",
    }


def test_read_key_cannot_push(fake):
    _, secret = fake.make_key("all")
    keys = full_setup(fake, secret)["p"]
    r = js(
        fake,
        f"pushDownlinks(B,{json.dumps(keys['readKey'])},APP,'van-000001',[new Uint8Array([1])],12).catch(err)",
    )
    assert r["status"] == 403 and r["kind"] == "forbidden"
    assert fake.downlinks == []
    js(
        fake,
        f"pushDownlinks(B,{json.dumps(keys['cmdKey'])},APP,'van-000001',[new Uint8Array([1,2]),new Uint8Array([3])],12)",
    )
    assert [(d, p, b) for d, p, b, _ in fake.downlinks] == [
        ("van-000001", 12, b"\x01\x02"),
        ("van-000001", 12, b"\x03"),
    ]
    # a read key cannot make keys either
    r = js(fake, f"makePhoneKeys(B,{json.dumps(keys['readKey'])},APP,'van-000001').catch(err)")
    assert r["status"] == 403


def test_read_uplinks(fake):
    _, secret = fake.make_key("all")
    read = full_setup(fake, secret)["p"]["readKey"]
    t0 = datetime(2026, 11, 4, 12, 0, 0, tzinfo=UTC)
    for i, (port, data) in enumerate([(2, b"\x01\x02"), (12, b"\x00abcd"), (3, b"")]):
        fake.add_uplink("van-000001", port, data, t0 + timedelta(minutes=i))
    r = js(
        fake,
        f"readUplinks(B,{json.dumps(read)},APP,'van-000001',{{limit:10}}).then(a=>a.map(u=>({{port:u.port,hex:[...u.bytes].map(x=>x.toString(16).padStart(2,'0')).join(''),at:u.at}})))",
    )
    ms = int(t0.timestamp() * 1000)
    assert r == [{"port": 3, "hex": "", "at": ms + 120000}, {"port": 12, "hex": "0061626364", "at": ms + 60000},
                 {"port": 2, "hex": "0102", "at": ms}]  # fmt: skip
    r = js(
        fake,
        f"readUplinks(B,{json.dumps(read)},APP,'van-000001',{{after:{ms + 60000}}}).then(a=>a.map(u=>u.port))",
    )
    assert r == [3]


def test_stalled_read_ends_as_timeout(fake):
    """A storage read that TTS never answers ends as TtnError kind ``timeout`` (the caller's signal, here 300 ms,
    replaces the 20 s default), so the app's poll goes on; the next read is answered."""
    _, read = fake.make_key("read")
    fake.add_uplink("van-000001", 2, b"\x01")
    fake.stall_reads = 1
    r = js(
        fake,
        f"readUplinks(B,{json.dumps(read)},APP,'van-000001',{{signal:AbortSignal.timeout(300)}}).catch(err)",
    )
    assert (r["err"], r["kind"], r["status"]) == ("TtnError", "timeout", 0)
    assert js(fake, f"readUplinks(B,{json.dumps(read)},APP,'van-000001').then(a=>a.length)") == 1


def test_cursor_is_nanosecond_exact(fake):
    """TTS sends RFC3339Nano; the raw ``receivedAt`` as ``after`` returns no duplicate, and ``at`` is in ms."""
    _, secret = fake.make_key("all")
    read = full_setup(fake, secret)["p"]["readKey"]
    t0 = datetime(2026, 10, 31, 7, 34, 5, 998108, tzinfo=UTC)
    fake.add_uplink("van-000001", 2, b"a", t0, ns=63)
    fake.add_uplink("van-000001", 2, b"b", t0, ns=64)  # same millisecond, one nanosecond later
    q = "readUplinks(B,%s,APP,'van-000001',%s)"
    r = js(fake, q % (json.dumps(read), "{}") + ".then(a=>a.map(u=>[u.receivedAt,u.at,u.bytes[0]]))")
    ms = int(t0.timestamp() * 1000)
    assert r == [["2026-10-31T07:34:05.998108064Z", ms, 98], ["2026-10-31T07:34:05.998108063Z", ms, 97]]
    # the cursor of the first (older) uplink returns only the second; the newest returns nothing
    cur = "{after:%s}" % json.dumps(r[1][0])
    assert js(fake, q % (json.dumps(read), cur) + ".then(a=>a.map(u=>u.bytes[0]))") == [98]
    cur = "{after:%s}" % json.dumps(r[0][0])
    assert js(fake, q % (json.dumps(read), cur) + ".then(a=>a.length)") == 0
    # the by-hand parser: offsets, short fractions, junk
    assert js(fake, "[ttnParseTime('2026-10-31T09:34:05.5+02:00'), ttnParseTime('2026-10-31T07:34:05Z'), ttnParseTime('x')]") == [
        ms - 998 + 500, ms - 998, None]  # fmt: skip


@pytest.mark.parametrize(
    ("body", "want"),
    [
        (b"", []),
        (
            b'{"result":{"received_at":"2026-10-31T07:34:05.1Z","uplink_message":{"f_port":2,"frm_payload":"AQ=="}}}\r\n\r\n',
            [2],
        ),
        (b'{"result":{"received_at":"2026-10-31T07:34:05.1Z","uplink_message":{"f_port":3}}}\n', [3]),
    ],
)
def test_storage_stream_shapes(fake, body, want):
    """An empty body, CRLF line ends and a trailing newline read cleanly."""
    _, secret = fake.make_key("all")
    read = full_setup(fake, secret)["p"]["readKey"]
    fake.storage_body = body
    assert js(fake, f"readUplinks(B,{json.dumps(read)},APP,'van-000001').then(a=>a.map(u=>u.port))") == want


@pytest.mark.parametrize(
    "body", [b'{"error":{"code":2,"message":"secret-looking text"}}\n', b'{"result":{"rece']
)
def test_storage_stream_errors(fake, body):
    """An error line and a truncated line throw TtnError kind ``stream``, without the payload."""
    _, secret = fake.make_key("all")
    read = full_setup(fake, secret)["p"]["readKey"]
    fake.storage_body = body
    r = js(fake, f"readUplinks(B,{json.dumps(read)},APP,'van-000001').catch(err)")
    assert r["err"] == "TtnError" and r["kind"] == "stream" and r["status"] == 200
    assert "secret-looking" not in r["msg"] and read not in r["msg"]


def test_key_create_refuses_rights_the_caller_lacks(fake):
    """A cmd key may not mint a key with rights it does not hold (403)."""
    _, secret = fake.make_key("all")
    keys = full_setup(fake, secret)["p"]
    assert fake.keys[keys["cmdKey"]]["rights"][-1] == "RIGHT_APPLICATION_TRAFFIC_DOWN_WRITE"
    fake.keys[keys["cmdKey"]]["rights"].append("RIGHT_APPLICATION_SETTINGS_API_KEYS")
    r = js(
        fake,
        f"ttnCall(B,{json.dumps(keys['cmdKey'])},'POST','/applications/'+APP+'/api-keys',{{name:'x',rights:['RIGHT_APPLICATION_ALL']}}).catch(err)",
    )
    assert r["status"] == 403
