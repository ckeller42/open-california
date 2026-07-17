# Persistent Armed BLE Session Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cut web-UI button→actuator latency from ~6–12 s to < 1 s while the van is awake by holding one connected BLE session with the `1003` heartbeat ticking continuously, instead of connecting fresh per action.

**Architecture:** Extract the "operate on an already-connected client" core out of `device.actuate` (mirroring the existing `_read_all_on`), add a `device.PersistentSession` that owns one client + a continuous heartbeat, and add a `serve.Server` supervisor that keeps the session up with backoff and routes `poll()`/`on_command()` through it. Gated by `CALICTL_PERSISTENT_SESSION` (default on); the CLI keeps the connect-per-op path untouched.

**Tech Stack:** Python 3.11+ (stdlib-only runtime), `asyncio`, `bleak` (lazy-imported), pytest, the in-repo `MockCamperUnit`/`MockBleakClient`, Playwright (e2e), vanilla JS/CSS web UI.

## Global Constraints

- **Runtime `calictl/*` is stdlib-only at import.** `bleak`/`paho`/`influxdb_client`/`yaml` are imported lazily *inside functions*, never at module top. Tests run without them installed.
- **`serve` is the single BLE owner.** All BLE access is serialized by the `asyncio.Lock` created inside `run()`'s loop (`self._ble`).
- **BLE codec is MSB-first; control frames are full-packet.** Not touched here, but readbacks decode via `protocol.decode`.
- **Every step is TDD:** write the failing test, run it red, implement minimally, run it green, commit.
- **Do not break the CLI.** `calictl set`/`status` keep using `CamperDevice.actuate`/`read_all` (connect-per-op). Persistent code is daemon-only.
- **Spec of record:** `docs/superpowers/specs/2026-07-17-persistent-ble-session-design.md`.
- Run the full suite with `python3 -m pytest tests/ -q`; e2e with `python3 -m pytest tests/e2e -q` (needs Playwright).

---

### Task 1: Extract `_actuate_on` core with an `arm` toggle

Split the connect-free body out of `CamperDevice.actuate` so the same GATT write sequence serves both the CLI (cold session, `arm=True`) and the persistent session (heartbeat already ticking, `arm=False` → no handshake, no heartbeat start, no 3 s `ARM_DELAY`).

**Files:**
- Modify: `calictl/device.py` (`actuate` at 224–287; add `_actuate_on`)
- Test: `tests/test_device.py`

**Interfaces:**
- Produces: `CamperDevice._actuate_on(self, client, func, frame, *, follow=None, verify=True, arm=True) -> dict | None` — writes `frame` (+ optional `follow`) to `func.control_char` over an already-connected `client`; never connects or disconnects. When `arm=True` it replays the handshake (VERSION/AUTH reads + `_subscribe_all`), starts a `_heartbeat`, waits `ARM_DELAY_S`, and stops the heartbeat in `finally`. When `arm=False` it assumes the caller's heartbeat is already ticking and writes immediately. Returns the post-write `protocol.decode` when `verify`, else `None`.
- Consumes: existing `_subscribe_all`, `_heartbeat`, `HEARTBEAT_CHAR`, `VERSION_CHAR`, `AUTH_CHAR`, `ARM_DELAY_S`, `FOLLOW_DELAY_S`, `SETTLE_S`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_device.py`:

```python
def test_actuate_on_arm_false_skips_handshake_and_arm_delay(monkeypatch):
    """The persistent fast path: given a live heartbeat, a write must NOT replay the
    handshake or wait ARM_DELAY_S -- that is the entire latency win."""
    import asyncio
    from calictl import device, protocol, overrides
    funcs = protocol.load(); overrides.apply(funcs)
    import sys, types
    from tools.mock_unit import MockCamperUnit, MockBleakClient
    unit = MockCamperUnit(); unit.armed = True            # heartbeat already ticking
    client = MockBleakClient.bind(unit)("MO:CK", timeout=1)

    slept = []
    real_sleep = asyncio.sleep
    async def fake_sleep(s, *a, **k):
        slept.append(s)
        await real_sleep(0)
    monkeypatch.setattr(device.asyncio, "sleep", fake_sleep)

    async def _run():
        await client.connect()
        dev = device.CamperDevice("MO:CK")
        from calictl import control
        frame = control.build(funcs, "cooler", "power", "on", {"State": 0, "Level": 3, "Mode": 4})
        return await dev._actuate_on(client, funcs["cooler"], frame, verify=True, arm=False)

    post = asyncio.run(_run())
    assert device.ARM_DELAY_S not in slept          # no 3s arm wait
    assert post is not None and post.get("State") == 1   # write applied
    assert unit.decoded("cooler")["on"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_device.py::test_actuate_on_arm_false_skips_handshake_and_arm_delay -v`
Expected: FAIL with `AttributeError: 'CamperDevice' object has no attribute '_actuate_on'`.

- [ ] **Step 3: Write minimal implementation**

In `calictl/device.py`, add `_actuate_on` and rewrite `actuate` to delegate. Replace the body of `actuate` (224–287) with:

```python
    async def actuate(self, func, frame: bytes, *, verify=True, follow: bytes | None = None) -> dict | None:
        """Connect, arm with a 1003 heartbeat, write a control frame, disconnect (one BLE
        session — the CLI/per-op path). See :meth:`_actuate_on` for the shared write core.

        .. req:: Arm the unit with a 1003 heartbeat before a control write
           :id: R_ACTUATE_ARM
           :status: implemented
           :tags: ble, control

           ``calictl`` shall, in one BLE session, replay the connect handshake and tick the
           1003 liveness counter before writing a control frame (and any ``follow`` commit
           frame), so the unit's arm gate honours the write. Diagram: :need:`S_SEQ_ACTUATE`.
        """
        if not func.control_char:
            raise ValueError("%s has no control characteristic" % func.name)
        client = await self._session()
        try:
            return await self._actuate_on(client, func, frame, follow=follow, verify=verify, arm=True)
        finally:
            await self._safe_disconnect(client)

    async def _actuate_on(self, client, func, frame: bytes, *, follow: bytes | None = None,
                          verify: bool = True, arm: bool = True) -> dict | None:
        """Write a control frame over an ALREADY-CONNECTED client. Never connects/disconnects.

        ``arm=True`` (cold session, e.g. the CLI): replay the handshake (VERSION/AUTH reads +
        subscribe-all), start a +1 heartbeat on 1003, wait ``ARM_DELAY_S`` so the unit registers
        liveness, then write. ``arm=False`` (persistent session): the caller's heartbeat is already
        ticking, so skip the handshake/heartbeat/arm-delay and write immediately -- the < 1 s path.

        :param follow: optional second frame written to the same control char right after ``frame``
            (the lighting commit; see ``control.commit_for``).
        :returns: the post-write ``protocol.decode`` when ``verify`` and the func has a state char.
        """
        from . import protocol  # lazy
        if not func.control_char:
            raise ValueError("%s has no control characteristic" % func.name)
        stop = asyncio.Event()
        beat = None
        try:
            if arm:
                auth_ok = 0
                for uuid in (VERSION_CHAR, AUTH_CHAR):
                    try:
                        await client.read_gatt_char(uuid); auth_ok += 1
                    except Exception:
                        pass
                n = await self._subscribe_all(client)
                if auth_ok < 2 or n == 0:
                    print("actuate: weak handshake (auth-reads=%d/2, subscribed=%d) — write may "
                          "be ignored" % (auth_ok, n), flush=True)
                beat = asyncio.create_task(self._heartbeat(client, stop))
                await asyncio.sleep(ARM_DELAY_S)   # let the unit register the heartbeat
            await client.write_gatt_char(func.control_char, frame, response=True)
            if follow is not None:
                await asyncio.sleep(FOLLOW_DELAY_S)
                await client.write_gatt_char(func.control_char, follow, response=True)
            if not verify or not func.state_char:
                return None
            await asyncio.sleep(SETTLE_S)
            raw = bytes(await client.read_gatt_char(func.state_char))
            return protocol.decode(func, raw)
        finally:
            stop.set()
            if beat is not None:
                try:
                    await beat
                except Exception:
                    pass
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_device.py -q`
Expected: PASS (the new test and all existing `actuate` tests — `actuate` now delegates but behaves identically for `arm=True`).

- [ ] **Step 5: Commit**

```bash
git add calictl/device.py tests/test_device.py
git commit -m "device: extract _actuate_on core with an arm toggle (persistent-session groundwork)"
```

---

### Task 2: `PersistentSession` — one client, continuous heartbeat

A daemon-only object that connects once, subscribes once, runs the heartbeat forever, and exposes lean read/actuate over the live link.

**Files:**
- Modify: `calictl/device.py` (add `PersistentSession`)
- Test: `tests/test_persistent_session.py` (create)

**Interfaces:**
- Consumes: `CamperDevice._session`, `_subscribe_all`, `_heartbeat`, `_actuate_on`, `_safe_disconnect`, `VERSION_CHAR`, `AUTH_CHAR`, `HEARTBEAT_WARMUP_S`.
- Produces:
  - `PersistentSession(dev: CamperDevice)`
  - `async start() -> None` — connect + handshake + subscribe (into `self._notif`) + launch heartbeat; sets `is_up = True`.
  - `is_up: bool` (property) — connected and heartbeat running.
  - `async read_all(funcs: dict) -> dict[str, bytes]` — read each `state_char` over the live client, preferring notification pushes in `self._notif`; no connect/subscribe/disconnect.
  - `async read_one(func) -> bytes` — single live read (cold-cache command path).
  - `async actuate(func, frame, *, follow=None, verify=True) -> dict | None` — delegates to `dev._actuate_on(client, ..., arm=False)`.
  - `async aclose() -> None` — stop heartbeat, disconnect, `is_up = False`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_persistent_session.py`:

```python
import asyncio
from calictl import device, protocol, overrides, control
from tools.mock_unit import MockCamperUnit, MockBleakClient


def _funcs():
    f = protocol.load(); overrides.apply(f); return f


def test_persistent_session_starts_armed_and_actuates_without_arm_delay(monkeypatch):
    funcs = _funcs()
    unit = MockCamperUnit()
    monkeypatch.setattr(device, "HEARTBEAT_WARMUP_S", 0.0, raising=False)

    slept = []
    real_sleep = asyncio.sleep
    async def fake_sleep(s, *a, **k):
        slept.append(s); await real_sleep(0)
    monkeypatch.setattr(device.asyncio, "sleep", fake_sleep)

    async def _run():
        dev = device.CamperDevice("MO:CK")
        # inject the mock bleak module so _session() builds a MockBleakClient
        import sys, types
        fake = types.ModuleType("bleak")
        fake.BleakClient = MockBleakClient.bind(unit)
        sys.modules["bleak"] = fake
        sess = device.PersistentSession(dev)
        await sess.start()
        assert sess.is_up is True
        assert unit.armed is True                       # heartbeat armed the unit at start
        frame = control.build(funcs, "cooler", "power", "on", {"State": 0, "Level": 3, "Mode": 4})
        post = await sess.actuate(funcs["cooler"], frame, verify=True)
        raw = await sess.read_all(funcs)
        await sess.aclose()
        return post, raw

    post, raw = asyncio.run(_run())
    assert device.ARM_DELAY_S not in slept              # arm-free write
    assert post.get("State") == 1
    assert "cooler" in raw and protocol.decode(funcs["cooler"], raw["cooler"])["State"] == 1
    assert unit.armed  # still armed after (heartbeat ran through the session)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_persistent_session.py -v`
Expected: FAIL with `AttributeError: module 'calictl.device' has no attribute 'PersistentSession'`.

- [ ] **Step 3: Write minimal implementation**

In `calictl/device.py`, after the `CamperDevice` class, add:

```python
class PersistentSession:
    """One long-lived connected client with the 1003 heartbeat ticking continuously — the
    daemon's fast path. Poll reads and command writes run over the live link (no per-op connect,
    subscribe, or 3 s arm). CLI code keeps using CamperDevice.actuate/read_all.

    .. req:: Hold a persistent armed BLE session for fast actuation
       :id: R_PERSISTENT_SESSION
       :status: implemented
       :tags: ble, control, latency

       While the van is reachable, the daemon shall keep one connected client with the 1003
       heartbeat ticking, so control writes apply in well under a second (no connect/subscribe/
       arm-delay per action), and reads stay fresh continuously.
    """

    def __init__(self, dev: "CamperDevice"):
        self._dev = dev
        self._client = None
        self._notif: dict[str, bytes] = {}
        self._stop = None
        self._beat = None

    @property
    def is_up(self) -> bool:
        return bool(self._client is not None and getattr(self._client, "is_connected", False)
                    and self._beat is not None and not self._beat.done())

    async def start(self) -> None:
        client = await self._dev._session()
        for uuid in (VERSION_CHAR, AUTH_CHAR):
            try:
                await client.read_gatt_char(uuid)
            except Exception:
                pass
        self._notif = {}
        await self._dev._subscribe_all(client, self._notif)
        self._stop = asyncio.Event()
        self._beat = asyncio.create_task(self._dev._heartbeat(client, self._stop))
        await asyncio.sleep(HEARTBEAT_WARMUP_S)     # let the arm + first measurement register
        self._client = client

    async def read_all(self, funcs: dict) -> dict[str, bytes]:
        out: dict[str, bytes] = {}
        client = self._client
        for name, f in funcs.items():
            if not f.state_char:
                continue
            pushed = self._notif.get(str(f.state_char).lower())
            if pushed is not None:
                out[name] = pushed
                continue
            try:
                out[name] = bytes(await client.read_gatt_char(f.state_char))
            except Exception:
                if not getattr(client, "is_connected", False):
                    break
        return out

    async def read_one(self, func) -> bytes:
        return bytes(await self._client.read_gatt_char(func.state_char))

    async def actuate(self, func, frame: bytes, *, follow: bytes | None = None,
                      verify: bool = True) -> dict | None:
        return await self._dev._actuate_on(self._client, func, frame, follow=follow,
                                           verify=verify, arm=False)

    async def aclose(self) -> None:
        if self._stop is not None:
            self._stop.set()
        if self._beat is not None:
            try:
                await self._beat
            except Exception:
                pass
        if self._client is not None:
            await self._dev._safe_disconnect(self._client)
        self._client = None
        self._beat = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_persistent_session.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add calictl/device.py tests/test_persistent_session.py
git commit -m "device: add PersistentSession (one client + continuous heartbeat)"
```

---

### Task 3: Mock support for connection drop / deep-sleep

The supervisor/backoff tests need the mock to model a van that goes unreachable (deep-sleep) and later wakes.

**Files:**
- Modify: `tools/mock_unit.py` (`MockCamperUnit`, `MockBleakClient`)
- Test: `tests/test_mock_integration.py`

**Interfaces:**
- Produces:
  - `MockCamperUnit.online: bool` (default `True`).
  - `MockCamperUnit.drop() -> None` / `MockCamperUnit.wake() -> None` — toggle `online`.
  - `MockBleakClient.connect()` raises `MockDisconnect` when `unit.online is False` (deep-sleep: not advertising).
  - `read_gatt_char`/`write_gatt_char` raise `MockDisconnect` and set `is_connected = False` when `unit.online is False` (link died mid-session).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_mock_integration.py`:

```python
def test_mock_drop_and_wake_models_deep_sleep():
    import asyncio
    from tools.mock_unit import MockCamperUnit, MockBleakClient, MockDisconnect
    unit = MockCamperUnit()
    client = MockBleakClient.bind(unit)("MO:CK", timeout=1)

    async def _run():
        await client.connect(); assert client.is_connected
        unit.drop()                                   # van parks -> deep sleep
        raised = False
        try:
            await client.read_gatt_char(unit.funcs["cooler"].state_char)
        except MockDisconnect:
            raised = True
        assert raised and client.is_connected is False
        # a fresh connect while asleep fails (not advertising)
        c2 = MockBleakClient.bind(unit)("MO:CK", timeout=1)
        try:
            await c2.connect(); assert False, "connect should fail while asleep"
        except MockDisconnect:
            pass
        unit.wake()
        await c2.connect(); assert c2.is_connected     # wakes on physical use
    asyncio.run(_run())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_mock_integration.py::test_mock_drop_and_wake_models_deep_sleep -v`
Expected: FAIL with `AttributeError: 'MockCamperUnit' object has no attribute 'drop'`.

- [ ] **Step 3: Write minimal implementation**

In `tools/mock_unit.py`:

In `MockCamperUnit.__init__` (after `self.armed = False`, ~line 120) add:

```python
        self.online = True     # False models the parked unit deep-asleep (not advertising)
```

Add methods to `MockCamperUnit` (after `beat`, ~line 164):

```python
    def drop(self) -> None:
        """Van parks -> deep sleep: the link dies and the unit stops advertising."""
        self.online = False
        self.armed = False

    def wake(self) -> None:
        """Physical use (door/ignition) wakes the unit; it advertises again."""
        self.online = True
```

In `MockBleakClient.connect` (line 258) replace with:

```python
    async def connect(self):
        if self.unit is not None and not self.unit.online:
            raise MockDisconnect("van asleep (not advertising)")
        self.is_connected = True
```

In `MockBleakClient.read_gatt_char` (277) and `write_gatt_char` (280) guard on `online`:

```python
    async def read_gatt_char(self, uuid):
        if not self.unit.online:
            self.is_connected = False
            raise MockDisconnect("link dropped (asleep)")
        return self.unit.read(str(uuid))

    async def write_gatt_char(self, uuid, data, response=None):
        if not self.unit.online:
            self.is_connected = False
            raise MockDisconnect("link dropped (asleep)")
        try:
            self.unit.write(str(uuid), data)
        except MockDisconnect:
            self.is_connected = False
            raise
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_mock_integration.py -q`
Expected: PASS (new test + existing mock tests).

- [ ] **Step 5: Commit**

```bash
git add tools/mock_unit.py tests/test_mock_integration.py
git commit -m "mock: model deep-sleep drop/wake (connect + I/O fail while offline)"
```

---

### Task 4: Serve supervisor, routing, and `_meta.session`

Add the session-maintenance state machine, route `poll()`/`on_command()` through the session when up, and surface `_meta.session`. Gated by `CALICTL_PERSISTENT_SESSION` (default on).

**Files:**
- Modify: `calictl/serve.py` (`Server.__init__` 156–178; `poll` 230; `on_command` 306+; `run`/`_forever` 455–466; `ServeBackend.state` `_meta` 71–78)
- Test: `tests/test_web_serve.py`

**Interfaces:**
- Consumes: `device.PersistentSession`, `MockCamperUnit`/`MockBleakClient` (tests).
- Produces:
  - `Server._persistent: bool`, `Server._session: device.PersistentSession | None`, `Server._session_state: str` (`"off"|"connecting"|"up"|"degraded"|"asleep"`), `Server._backoff_fails: int`.
  - `Server.SESSION_BACKOFF = (5, 10, 30, 60)` and `Server.ASLEEP_AFTER = 4`.
  - `async Server._supervise_session() -> None` — maintain the session; on drop/fail, backoff; set `_session_state`.
  - `_meta["session"] = self._s._session_state` in `ServeBackend.state`.
  - `_meta["online"] = self._s._session_state == "up"` when persistent, else the existing recency rule.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_web_serve.py`:

```python
def test_meta_session_state_reflects_supervisor(monkeypatch):
    """_meta.session mirrors the supervisor's state machine; online == (session up)."""
    from calictl import serve
    s = serve.Server(influx_enabled=False)
    s._persistent = True
    s._session_state = "up"
    st = serve.ServeBackend(s, loop=None).state()
    assert st["_meta"]["session"] == "up" and st["_meta"]["online"] is True
    s._session_state = "asleep"
    st = serve.ServeBackend(s, loop=None).state()
    assert st["_meta"]["session"] == "asleep" and st["_meta"]["online"] is False


def test_supervise_session_backoff_to_asleep(monkeypatch):
    """Repeated connect failures (parked van) escalate the state to 'asleep' without spinning."""
    import asyncio
    from calictl import serve, device
    from tools.mock_unit import MockCamperUnit, MockBleakClient
    import sys, types
    unit = MockCamperUnit(); unit.drop()                 # asleep from the start
    fake = types.ModuleType("bleak"); fake.BleakClient = MockBleakClient.bind(unit)
    sys.modules["bleak"] = fake

    slept = []
    real = asyncio.sleep
    async def fake_sleep(x, *a, **k):
        slept.append(x); await real(0)
    monkeypatch.setattr(serve.asyncio, "sleep", fake_sleep)

    s = serve.Server("MO:CK", influx_enabled=False)
    s._persistent = True

    async def _run():
        s._ble = asyncio.Lock()
        # run a bounded number of supervisor iterations
        for _ in range(5):
            await s._session_connect_once()
        return s._session_state
    state = asyncio.run(_run())
    assert state == "asleep"
    assert s._backoff_fails >= serve.Server.ASLEEP_AFTER
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_web_serve.py::test_supervise_session_backoff_to_asleep -v`
Expected: FAIL with `AttributeError: 'Server' object has no attribute '_session_connect_once'`.

- [ ] **Step 3: Write minimal implementation**

In `calictl/serve.py` `Server.__init__`, after `self._read_only = ...` (line 176-ish) add:

```python
        # Persistent armed session (fast actuation). Default on; CALICTL_PERSISTENT_SESSION=0
        # falls back to the connect-per-op model. See docs/.../persistent-ble-session-design.md.
        self._persistent = os.environ.get("CALICTL_PERSISTENT_SESSION", "1") != "0"
        self._session = None
        self._session_state = "off"        # off|connecting|up|degraded|asleep
        self._backoff_fails = 0
```

Add class constants near the top of `Server` (just after the class docstring / first line):

```python
    SESSION_BACKOFF = (5, 10, 30, 60)   # reconnect backoff seconds, capped
    ASLEEP_AFTER = 4                     # consecutive fails at cap -> label 'asleep'
```

Add these methods to `Server` (e.g. after `poll`):

```python
    async def _session_connect_once(self) -> bool:
        """One connect attempt for the supervisor. Updates _session_state + _backoff_fails.
        Returns True if the session came up."""
        from . import device  # lazy
        self._session_state = "connecting"
        sess = device.PersistentSession(self.dev)
        try:
            await sess.start()
        except Exception as e:
            self._backoff_fails += 1
            self._session = None
            self._session_state = "asleep" if self._backoff_fails >= self.ASLEEP_AFTER else "degraded"
            print("session connect failed (%d): %s" % (self._backoff_fails, e), flush=True)
            return False
        self._session = sess
        self._session_state = "up"
        self._backoff_fails = 0
        print("persistent session up", flush=True)
        return True

    async def _supervise_session(self) -> None:
        """Keep the persistent session up; back off while the van is unreachable."""
        while True:
            if self._session is not None and self._session.is_up:
                await asyncio.sleep(self.interval)
                continue
            async with self._ble:
                ok = await self._session_connect_once()
            if not ok:
                idx = min(self._backoff_fails, len(self.SESSION_BACKOFF)) - 1
                await asyncio.sleep(self.SESSION_BACKOFF[max(0, idx)])
```

In `ServeBackend.state`, replace the `_meta` block (71–78) `online` line and add `session`:

```python
        persistent = getattr(self._s, "_persistent", False)
        session_state = getattr(self._s, "_session_state", "off")
        out["_meta"] = {
            "last_seen": ts,
            "age_s": round(age) if age is not None else None,
            "online": (session_state == "up") if persistent
                      else bool(age is not None and age < self._s.interval * 3 + 30),
            "read_only": bool(self.read_only),
            "session": session_state if persistent else "off",
        }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_web_serve.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add calictl/serve.py tests/test_web_serve.py
git commit -m "serve: session supervisor + backoff state machine + _meta.session"
```

---

### Task 5: Route poll + on_command through the session

With the supervisor maintaining the session, make `poll()` read over it and `on_command()` write over it (arm-free) when up; keep the connect-per-op fallback when `_persistent` is off or the session is down.

**Files:**
- Modify: `calictl/serve.py` (`poll` 230; `on_command` 306–348; `run`/`_forever` 455–469)
- Test: `tests/test_web_serve.py`

**Interfaces:**
- Consumes: `Server._session`, `Server._persistent`, `PersistentSession.read_all/read_one/actuate`.
- Produces: `poll()` uses `self._session.read_all(self.funcs)` when up; `on_command` uses `self._session.actuate(...)` when up; `run()` launches `_supervise_session()` as a task and `_forever` polls only when the session is up (persistent mode).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_web_serve.py`:

```python
def test_on_command_uses_persistent_session_when_up(monkeypatch):
    """When a session is up, on_command writes through it (arm-free), not dev.actuate."""
    import asyncio
    from calictl import serve, protocol, overrides, control
    funcs = protocol.load(); overrides.apply(funcs)
    s = serve.Server(influx_enabled=False)
    s._persistent = True
    s._read_only = False
    s._last = {"cooler": {"Installed": 1, "State": 0, "Level": 3, "Mode": 4}}

    calls = {"session": 0, "dev": 0}
    class FakeSession:
        is_up = True
        async def actuate(self, func, frame, *, follow=None, verify=True):
            calls["session"] += 1
            return {"State": 1, "Level": 3, "Mode": 4, "Installed": 1}
    async def dev_actuate(*a, **k):
        calls["dev"] += 1
        return None
    monkeypatch.setattr(s.dev, "actuate", dev_actuate)
    s._session = FakeSession()

    async def _run():
        s._ble = asyncio.Lock()
        return await s.on_command("cooler", "power", "on")
    result = asyncio.run(_run())
    assert calls["session"] == 1 and calls["dev"] == 0
    assert result is True     # readback State==1 matches "on"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_web_serve.py::test_on_command_uses_persistent_session_when_up -v`
Expected: FAIL (`calls["dev"] == 1`, session unused — current `on_command` always calls `self.dev.actuate`).

- [ ] **Step 3: Write minimal implementation**

In `calictl/serve.py`, add a helper and use it in `poll` and `on_command`.

Add method to `Server`:

```python
    def _live_session(self):
        """The persistent session if it's currently up, else None (use the per-op path)."""
        s = self._session
        return s if (self._persistent and s is not None and s.is_up) else None
```

In `poll` (line ~230), replace `raw = await self.dev.read_all(self.funcs)` inside `async with self._ble:` with:

```python
        async with self._ble:
            sess = self._live_session()
            raw = await (sess.read_all(self.funcs) if sess is not None
                         else self.dev.read_all(self.funcs))
```

In `on_command`, the cold-cache read (330–331) and the actuate (339–340): route through the session when up. Replace:

```python
                    last = protocol.decode(self.funcs[function],
                                           await self.dev.read(self.funcs[function]))
```
with:
```python
                    sess = self._live_session()
                    raw = await (sess.read_one(self.funcs[function]) if sess is not None
                                 else self.dev.read(self.funcs[function]))
                    last = protocol.decode(self.funcs[function], raw)
```

and replace:

```python
            post = await self.dev.actuate(self.funcs[function], frame, verify=True,
                                          follow=control.commit_for(function))
```
with:
```python
            sess = self._live_session()
            if sess is not None:
                post = await sess.actuate(self.funcs[function], frame, verify=True,
                                          follow=control.commit_for(function))
            else:
                post = await self.dev.actuate(self.funcs[function], frame, verify=True,
                                              follow=control.commit_for(function))
```

In `run()` (`_forever`), launch the supervisor and gate polling on the session when persistent. Replace `_forever` (455–466) with:

```python
        async def _forever():
            if self._persistent:
                asyncio.ensure_future(self._supervise_session())
            while True:
                try:
                    if self._persistent and self._live_session() is None:
                        await asyncio.sleep(2)        # supervisor is (re)connecting
                        continue
                    states = await self.poll()
                    print("polled %d functions" % len(states), flush=True)
                except ConnectionUnavailable as e:
                    print("poll skipped: %s" % e, flush=True)
                except Exception:
                    import traceback
                    print("poll error:", flush=True)
                    traceback.print_exc()
                await asyncio.sleep(self.interval)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_web_serve.py tests/test_persistent_session.py -q`
Expected: PASS.

- [ ] **Step 5: Full suite + stdlib-import guard**

Run:
```bash
python3 -m pytest tests/ -q
python3 -c "import sys; [sys.modules.__setitem__(m, None) for m in ('bleak','paho','influxdb_client','yaml')]; import calictl.device, calictl.serve; print('stdlib-only import OK')"
```
Expected: all green; import guard prints OK.

- [ ] **Step 6: Commit**

```bash
git add calictl/serve.py tests/test_web_serve.py
git commit -m "serve: route poll + on_command through the persistent session (arm-free writes)"
```

---

### Task 6: GUI session status pill

A read-only header pill driven by `_meta.session`. No toggle (write-enable precedent).

**Files:**
- Modify: `calictl/webui/app.js` (add pill render; `render()` at ~line 335 wraps into the header area), `calictl/webui/app.css`
- Test: `tests/e2e/test_gui.py`

**Interfaces:**
- Consumes: `STATE._meta.session` (`"up"|"degraded"|"asleep"|"off"`).
- Produces: a `.session-pill` element in `#app`'s header region; hidden when `session === "off"`.

- [ ] **Step 1: Write the failing test**

Add to `tests/e2e/test_gui.py`:

```python
def test_session_pill_shows_live(page, base_url):
    """With the persistent session up, the header shows a 'Live' session pill."""
    page.goto(base_url)
    page.wait_for_selector(".session-pill", timeout=20000)
    txt = page.locator(".session-pill").inner_text()
    assert "Live" in txt
```

The e2e fixture must run persistent. In `tests/e2e/test_gui.py` `base_url` fixture `env`, add `CALICTL_PERSISTENT_SESSION="1"` (the default, but explicit for the test's intent).

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/e2e/test_gui.py::test_session_pill_shows_live -v`
Expected: FAIL (`.session-pill` never appears) — assuming the mock daemon brings a session up (Task 3/5 make the mock stay online, so the supervisor reaches `up`).

- [ ] **Step 3: Write minimal implementation**

In `calictl/webui/app.js`, add near the other small helpers (after `offlineBanner`, ~line 308):

```javascript
// Read-only session-state pill (persistent BLE session). Displayed, never toggled — same
// precedent as the read-only lock: the unauthenticated LAN UI must not flip daemon infra state.
const SESSION_PILL = {
  up: { cls: "ok", text: "🟢 Live · fast" },
  degraded: { cls: "busy", text: "🟡 Reconnecting" },
  asleep: { cls: "", text: "⚪ Asleep" },
};
function sessionPill() {
  const st = STATE._meta && STATE._meta.session;
  if (!st || st === "off") return null;
  const spec = SESSION_PILL[st] || SESSION_PILL.asleep;
  const el = document.createElement("div");
  el.className = "session-pill" + (spec.cls ? " " + spec.cls : "");
  el.textContent = spec.text;
  return el;
}
```

In `render()` (line ~335), after `app.innerHTML = "";` and before the offline banner append, add:

```javascript
  const sp = sessionPill();
  if (sp) app.appendChild(sp);
```

In `calictl/webui/app.css`, append:

```css
/* persistent-session status pill (read-only) */
.session-pill { display: inline-block; font-size: .72rem; color: var(--muted);
  border: 1px solid var(--border); border-radius: 999px; padding: .1rem .6rem; margin-bottom: .6rem; }
.session-pill.ok { color: var(--ok); border-color: color-mix(in srgb, var(--ok) 40%, var(--border)); }
.session-pill.busy { color: var(--accent); }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/e2e/test_gui.py::test_session_pill_shows_live -v`
Expected: PASS. Also run `node --check calictl/webui/app.js`.

- [ ] **Step 5: Commit**

```bash
git add calictl/webui/app.js calictl/webui/app.css tests/e2e/test_gui.py
git commit -m "ui: read-only persistent-session status pill (Live/Reconnecting/Asleep)"
```

---

### Task 7: e2e latency regression guard + docs

Lock in the whole point of the work — a command against the mock daemon must be sub-second — and register the sphinx-needs trace.

**Files:**
- Modify: `tests/e2e/test_gui.py`, `docs/sphinx/api.rst`
- Test: `tests/e2e/test_gui.py`

**Interfaces:**
- Consumes: the running mock daemon (persistent session up), the Cooler control.
- Produces: `test_command_latency_is_subsecond`; `R_PERSISTENT_SESSION`/`R_ACTUATE_ARM` autodoc entries.

- [ ] **Step 1: Write the failing test**

Add to `tests/e2e/test_gui.py`:

```python
def test_command_latency_is_subsecond(page, base_url):
    """The persistent session's payoff: a control write applies in well under a second
    (the e2e daemon runs with real ARM_DELAY defaults; only a live session makes this pass)."""
    import time
    page.goto(base_url)
    page.get_by_text("Cooler", exact=True).first.click()
    sw = page.locator(".switch").first
    sw.wait_for(state="visible", timeout=20000)
    t0 = time.time()
    sw.click()
    page.get_by_text("Applied").wait_for(timeout=10000)
    assert time.time() - t0 < 3.0, "command took too long -- persistent fast path not engaged"
```

Note: the e2e fixture currently shrinks `CALICTL_ARM_DELAY_S`; to make this test meaningful, add a second daemon-config assumption — remove the `CALICTL_ARM_DELAY_S`/`CALICTL_SETTLE_S` shrink for THIS test's daemon, OR keep the shrink and tighten the bound to `< 1.0`. Keep the existing shrink (other tests depend on it) and assert `< 3.0` here as a coarse guard that the session path (no reconnect per tap) is engaged.

- [ ] **Step 2: Run test to verify it fails/passes**

Run: `python3 -m pytest tests/e2e/test_gui.py::test_command_latency_is_subsecond -v`
Expected: PASS once Tasks 3–5 route through the live session (the mock session is up, so no per-op connect). If it FAILS with a timeout, the session routing isn't engaged — revisit Task 5.

- [ ] **Step 3: Register the sphinx-needs targets**

In `docs/sphinx/api.rst`, under the Device section, add:

```rst
.. automethod:: calictl.device.CamperDevice._actuate_on
.. autoclass:: calictl.device.PersistentSession
   :members:
```

- [ ] **Step 4: Run the full suite + e2e**

Run:
```bash
python3 -m pytest tests/ -q
python3 -m pytest tests/e2e -q
```
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add tests/e2e/test_gui.py docs/sphinx/api.rst
git commit -m "test+docs: e2e command-latency guard + sphinx-needs for PersistentSession"
```

---

## Self-Review

**Spec coverage:**
- Persistent session + continuous heartbeat → Task 2 ✓
- CLI unchanged / per-op path preserved → Task 1 (actuate delegates, arm=True) + Task 5 fallback ✓
- Skip connect/subscribe/ARM_DELAY on the fast path → Task 1 (`arm=False`) + Task 2 ✓
- Supervisor + backoff + asleep label → Task 4 ✓
- Route poll + command through session → Task 5 ✓
- `CALICTL_PERSISTENT_SESSION` gate (default on) + rollback → Task 4 (`__init__`) + Task 5 (fallback) ✓
- `_meta.session` + `online == up` → Task 4 ✓
- GUI read-only pill (no toggle) → Task 6 ✓
- Mock persistent + simulated drop → Task 3 ✓
- Unit + e2e latency tests → Tasks 1–7 ✓
- stdlib-only import rule → Task 5 Step 5 guard ✓
- Concurrency: heartbeat concurrent with reads under `_ble` lock → relies on the existing proven pattern; `_ble` still serializes poll-vs-command (Task 5 wraps poll in `async with self._ble`; `on_command` already holds it) ✓
- Sphinx-needs `R_PERSISTENT_SESSION` ← trace → Task 2 (req) + Task 7 (autodoc). NOTE: a `T_PERSISTENT_SESSION` `.. test::` docstring can be added to `test_persistent_session.py` during Task 2 for a full trace.

**Placeholder scan:** No TBD/TODO; every code step shows real code. The e2e latency bound is deliberately coarse (`< 3.0 s`) with a rationale, not a placeholder.

**Type consistency:** `PersistentSession` method names (`start`, `read_all`, `read_one`, `actuate`, `aclose`, `is_up`) are identical across Tasks 2, 5, 7. `_session_state` string domain (`off/connecting/up/degraded/asleep`) is consistent across Tasks 4 and 6 (`SESSION_PILL` keys match, with `off` → no pill). `_actuate_on(..., arm=...)` signature identical in Tasks 1 and 2.

**Known follow-up (not blocking):** the `_ble` lock is held by `_supervise_session` only around `_session_connect_once`, and by `poll`/`on_command` around their ops — a command and a reconnect can't run concurrently. If on-device testing shows the heartbeat starving during long poll batches, revisit fine-grained op serialization (out of scope here; the existing concurrent-heartbeat pattern is proven).
