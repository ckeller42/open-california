"""BluezTransport: only the stdlib-only plumbing is testable without a dbus/BLE stack.

.. test:: BlueZ transport construction + passkey-future plumbing
   :id: T_PAIRING_BLUEZ_TRANSPORT
   :links: R_PAIRING_BLUEZ_TRANSPORT

NOT-LIVE-VERIFIED: the dbus_fast agent registration, bleak scan/GATT, and
Device1/Adapter1 calls are exercised against real hardware in the #157 van
session, not here (no dbus/BLE stack in CI) -- see `pairing_bluez.py`'s
``BluezTransport`` docstring.
"""
import asyncio

import pytest

from calictl.pairing import EV_PAIR_OK
from calictl.pairing_bluez import BluezTransport


def _pair_transport(monkeypatch, call_pair_impl):
    """A BluezTransport wired for pair() unit tests: a fake Device1 whose call_pair() runs
    ``call_pair_impl`` (sharing the counters dict), with the dbus agent / interface lookup /
    remove_bond stubbed. Returns (transport, events, calls)."""
    events = []
    calls = {"pair": 0, "remove_bond": 0}

    class _FakeDevice:
        async def call_pair(self):
            calls["pair"] += 1
            await call_pair_impl(calls["pair"])

    async def _cb(ev, arg=0):
        events.append(ev)

    async def _noop_agent():
        pass

    async def _fake_iface(path, iface):
        return _FakeDevice()

    async def _fake_remove():
        calls["remove_bond"] += 1

    async def _fake_rediscover(timeout):
        calls["rediscover"] = calls.get("rediscover", 0) + 1

    t = BluezTransport(on_event=_cb)
    t._address = "AA:BB:CC:DD:EE:FF"
    monkeypatch.setattr(t, "_ensure_agent", _noop_agent)
    monkeypatch.setattr(t, "_get_interface", _fake_iface)
    monkeypatch.setattr(t, "remove_bond", _fake_remove)
    monkeypatch.setattr(t, "_rediscover", _fake_rediscover)
    return t, events, calls


def test_pair_recovers_from_a_stale_bond_then_pairs_once(monkeypatch):
    """A stale local bond makes BlueZ refuse Pair() with org.bluez.Error.AlreadyExists; pair() must
    clear that bond and retry Pair() exactly once, ending on EV_PAIR_OK (not a generic failure).
    Hit for real 2026-09-18 re-pairing at the van after a unit-side Bluetooth reset.

    .. test:: pair() recovers from a stale bond
       :id: T_PAIRING_STALE_BOND_RECOVERY
       :links: R_PAIRING_STALE_BOND_RECOVERY
    """
    async def _call_pair(n):
        if n == 1:
            raise Exception("org.bluez.Error.AlreadyExists: Already Exists")
        # second attempt succeeds

    t, events, calls = _pair_transport(monkeypatch, _call_pair)
    asyncio.run(t.pair())

    assert calls["pair"] == 2          # first raised AlreadyExists, retried exactly once
    assert calls["remove_bond"] == 1   # the stale bond was cleared before the retry
    assert events == [EV_PAIR_OK]      # ended on success


def test_pair_retry_targets_the_rediscovered_device(monkeypatch, tmp_path):
    """RemoveDevice drops BlueZ's device object together with the bond (and remove_bond() clears
    _address), so the AlreadyExists retry must pair the unit RE-DISCOVERED under its current
    address. It used to call _device_path() with _address=None -> RuntimeError, so the documented
    one-shot retry could never succeed (found by the real-BlueZ CI rig, tests/realstack/rig.py).

    .. test:: pair() retries against the re-discovered device
       :id: T_PAIRING_STALE_BOND_REDISCOVER
       :links: R_PAIRING_STALE_BOND_RECOVERY
    """
    monkeypatch.setenv("CALICTL_PAIRING_CACHE", str(tmp_path / "pairing.json"))
    paths = []

    class _Iface:
        def __init__(self, path):
            self.path = path

        async def call_pair(self):
            paths.append(self.path)
            if len(paths) == 1:
                raise Exception("org.bluez.Error.AlreadyExists: Already Exists")

        async def call_remove_device(self, path):
            pass

    async def _iface(path, iface):
        return _Iface(path)

    async def _noop():
        pass

    async def _rediscover(timeout):
        t._found_device = object()
        t._address = "11:22:33:44:55:66"

    events = []

    async def _cb(ev, arg=0):
        events.append(ev)

    t = BluezTransport(on_event=_cb)
    t._address = "AA:BB:CC:DD:EE:FF"
    monkeypatch.setattr(t, "_ensure_agent", _noop)
    monkeypatch.setattr(t, "_get_interface", _iface)
    monkeypatch.setattr(t, "_rediscover", _rediscover)   # the real remove_bond() runs
    asyncio.run(t.pair())

    assert paths == ["/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF", "/org/bluez/hci0/dev_11_22_33_44_55_66"]
    assert events == [EV_PAIR_OK]


class _FakeBleakClient:
    """Stub bleak client. Class-level knobs per test: ``connect_delay`` (seconds, None = hang
    forever), ``auth_read_fails``. Every instance is recorded in ``made``."""
    made: list = []
    connect_delay: float | None = 0
    auth_read_fails = False

    def __init__(self, device, adapter=None, timeout=None):
        self.device, self.adapter, self.timeout = device, adapter, timeout
        self.is_connected = False
        self.disconnected = False
        _FakeBleakClient.made.append(self)

    async def connect(self):
        while _FakeBleakClient.connect_delay is None:   # a stale-key reconnect loop: hangs
            await asyncio.sleep(0.01)
        await asyncio.sleep(_FakeBleakClient.connect_delay)
        self.is_connected = True

    async def read_gatt_char(self, uuid):
        if _FakeBleakClient.auth_read_fails:
            raise Exception("org.bluez.Error.NotPermitted: Read not permitted")
        return b"\x00"

    async def disconnect(self):
        self.is_connected = False
        self.disconnected = True


def _connect_transport(monkeypatch, bonded, connect_delay=0, auth_read_fails=False):
    """A BluezTransport whose connect() runs against a stub ``bleak`` module; records the order of
    the bond steps. Returns (transport, calls, events)."""
    import sys
    import types

    _FakeBleakClient.made = []
    _FakeBleakClient.connect_delay = connect_delay
    _FakeBleakClient.auth_read_fails = auth_read_fails
    monkeypatch.setitem(sys.modules, "bleak", types.SimpleNamespace(BleakClient=_FakeBleakClient))
    calls, events = [], []

    async def _cb(ev, arg=0):
        events.append(ev)

    async def _bonded():
        calls.append("bonded?")
        return bonded

    async def _remove():
        calls.append("remove_bond")
        t._found_device = t._address = None

    async def _rediscover(timeout):
        calls.append(("rediscover", timeout))
        t._found_device = "fresh-device"
        t._address = "11:22:33:44:55:66"

    t = BluezTransport(on_event=_cb, adapter_path="/org/bluez/hci1")
    t._found_device = "stale-device"
    t._address = "C0:FF:EE:CA:11:F0"
    monkeypatch.setattr(t, "_device_bonded", _bonded)
    monkeypatch.setattr(t, "remove_bond", _remove)
    monkeypatch.setattr(t, "_rediscover", _rediscover)
    return t, calls, events


def test_connect_keeps_a_working_bond_and_pair_short_circuits(monkeypatch):
    """Starting the wizard must never destroy a working bond (ruling R17): over an existing bond a
    bounded probe (connect + the auth-gated AUTH_CHAR read) succeeds -> no RemoveDevice, the probe
    link becomes the transport's client, and pair() reports EV_PAIR_OK without calling Pair().

    .. test:: a working bond survives the wizard
       :id: T_PAIRING_KEEPS_VALID_BOND
       :links: R_PAIRING_STALE_BOND_RECOVERY
    """
    from calictl.pairing import EV_CONNECTED

    t, calls, events = _connect_transport(monkeypatch, bonded=True)
    asyncio.run(t.connect())
    assert calls == ["bonded?"]                                   # no remove_bond, no rediscover
    assert len(_FakeBleakClient.made) == 1 and t._client is _FakeBleakClient.made[0]
    assert t._client.is_connected and t._client.device == "stale-device"

    async def _no_iface(path, iface):
        raise AssertionError("pair() must not touch BlueZ when the bond is valid")
    monkeypatch.setattr(t, "_get_interface", _no_iface)

    async def _no_agent():
        raise AssertionError("pair() must not register an agent when the bond is valid")
    monkeypatch.setattr(t, "_ensure_agent", _no_agent)
    asyncio.run(t.pair())
    assert events == [EV_CONNECTED, EV_PAIR_OK]


def test_connect_drops_a_stale_bond_and_rediscovers(monkeypatch):
    """With a bond on file BlueZ encrypts every new link with the stored LTK; a unit that forgot
    us (Bluetooth reset) rejects it (PIN or Key Missing), the kernel drops the link and BlueZ
    reconnects forever -- connect() never returned and the wizard died on a CONNECTING timeout
    (real-BlueZ CI rig). The probe fails -> drop the bond, re-discover, connect to the NEW object.

    .. test:: connect() clears a stale bond before connecting
       :id: T_PAIRING_STALE_BOND_BEFORE_CONNECT
       :links: R_PAIRING_STALE_BOND_RECOVERY
    """
    from calictl.pairing import EV_CONNECTED

    t, calls, events = _connect_transport(monkeypatch, bonded=True, auth_read_fails=True)
    asyncio.run(t.connect())
    assert calls[:2] == ["bonded?", "remove_bond"] and calls[2][0] == "rediscover"
    probe, final = _FakeBleakClient.made
    assert probe.device == "stale-device" and probe.disconnected      # the probe link is released
    assert final.device == "fresh-device" and final.adapter == "hci1" and t._client is final
    assert events == [EV_CONNECTED]
    assert t._bond_valid is False


def test_connect_budget_splits_the_connecting_timeout(monkeypatch):
    """Every step of connect() fits one deadline derived from TIMEOUT_S[CONNECTING]: a HUNG probe
    (the stale-key loop) is cut at BOND_PROBE_S, re-discovery gets <= REDISCOVER_S, and the final
    connect keeps >= MIN_CONNECT_S -- the whole worst case stays inside the SM's timer."""
    from calictl import pairing, pairing_bluez

    monkeypatch.setattr(pairing_bluez, "BOND_PROBE_S", 0.05)
    monkeypatch.setitem(pairing.TIMEOUT_S, pairing.CONNECTING, 20)
    t, calls, _ = _connect_transport(monkeypatch, bonded=True, connect_delay=None)

    async def _run():
        # the probe hangs; after it times out, let the final connect succeed
        async def _then_ok():
            await asyncio.sleep(0.2)
            _FakeBleakClient.connect_delay = 0
        asyncio.ensure_future(_then_ok())
        await t.connect()
    asyncio.run(_run())
    rediscover_timeout = calls[2][1]
    assert 0 < rediscover_timeout <= pairing_bluez.REDISCOVER_S
    final = _FakeBleakClient.made[-1]
    budget = 20 - pairing_bluez.CONNECT_MARGIN_S
    assert pairing_bluez.MIN_CONNECT_S <= final.timeout <= budget
    assert pairing_bluez.BOND_PROBE_S + pairing_bluez.REDISCOVER_S + pairing_bluez.MIN_CONNECT_S \
        <= 20 - pairing_bluez.CONNECT_MARGIN_S                          # the defaults fit


def test_the_default_connect_budget_fits_the_sm_timer():
    from calictl import pairing
    from calictl import pairing_bluez as pb

    assert pb.BOND_PROBE_S + pb.REDISCOVER_S + pb.MIN_CONNECT_S + pb.CONNECT_MARGIN_S \
        <= pairing.TIMEOUT_S[pairing.CONNECTING]


def test_connect_leaves_an_unbonded_device_alone(monkeypatch):
    t, calls, _ = _connect_transport(monkeypatch, bonded=False)
    asyncio.run(t.connect())
    assert calls == ["bonded?"]
    assert [c.device for c in _FakeBleakClient.made] == ["stale-device"]


def test_a_connect_past_the_deadline_leaves_no_link(monkeypatch):
    """A connect that outlives the CONNECTING budget must fail (-> EV_CONNECT_FAIL) BEFORE the SM
    timer, and release its half-open client: the unit has one connection slot and serve's poll
    resumes as soon as the wizard ends.

    .. test:: connect() is bounded by the CONNECTING budget
       :id: T_PAIRING_CONNECT_BOUNDED
       :links: R_PAIRING_BLUEZ_TRANSPORT
    """
    from calictl import pairing

    monkeypatch.setitem(pairing.TIMEOUT_S, pairing.CONNECTING, 1.2)   # budget = 0.2 s
    t, _, events = _connect_transport(monkeypatch, bonded=False, connect_delay=None)
    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(t.connect())
    (client,) = _FakeBleakClient.made
    assert client.disconnected and not client.is_connected
    assert t._client is None and events == []


def test_an_abandoned_connect_releases_its_link_and_stays_silent(monkeypatch):
    """The SM timer (or a cancel) ends the flow while connect() is still awaiting: aclose() bumps
    the generation, so the late connect must disconnect the client it just made and emit nothing
    -- else it would hold the unit's only slot with no owner.

    .. test:: an abandoned connect() leaves no link
       :id: T_PAIRING_CONNECT_ABANDONED
       :links: R_PAIRING_BLUEZ_TRANSPORT
    """
    t, _, events = _connect_transport(monkeypatch, bonded=False, connect_delay=0.1)

    async def _run():
        task = asyncio.ensure_future(t.connect())
        await asyncio.sleep(0.02)
        await t.aclose()                      # the flow ended (timeout/cancel) mid-connect
        await task
    asyncio.run(_run())
    (client,) = _FakeBleakClient.made
    assert client.disconnected and not client.is_connected
    assert t._client is None and events == []


def test_an_abandoned_probe_releases_its_link(monkeypatch):
    t, calls, events = _connect_transport(monkeypatch, bonded=True, connect_delay=0.1)

    async def _run():
        task = asyncio.ensure_future(t.connect())
        await asyncio.sleep(0.02)
        await t.aclose()
        await task
    asyncio.run(_run())
    (probe,) = _FakeBleakClient.made
    assert probe.disconnected and calls == ["bonded?"] and events == [] and t._client is None


def test_an_abandoned_pair_does_not_report_success(monkeypatch):
    async def _call_pair(n):
        await t.aclose()                      # the flow ended while Pair() was in flight

    t, events, _ = _pair_transport(monkeypatch, _call_pair)
    asyncio.run(t.pair())
    assert events == []


class _Props:
    def __init__(self, values):
        self._values = values

    async def call_get(self, iface, name):
        if name not in self._values:
            raise Exception("org.freedesktop.DBus.Error.InvalidArgs: No such property '%s'" % name)
        return type("V", (), {"value": self._values[name]})()


@pytest.mark.parametrize("values, expected", [
    ({"Bonded": True, "Paired": True}, True),
    ({"Bonded": False, "Paired": True}, False),   # paired without stored keys: nothing blocks
    ({"Paired": True}, True),                     # a BlueZ without Device1.Bonded
    ({}, False),
])
def test_device_bonded_reads_bonded_then_paired(monkeypatch, values, expected):
    t = BluezTransport()
    t._address = "C0:FF:EE:CA:11:F0"

    async def _iface(path, iface):
        return _Props(values)
    monkeypatch.setattr(t, "_get_interface", _iface)
    assert asyncio.run(t._device_bonded()) is expected


def test_device_bonded_is_false_without_a_bus(monkeypatch):
    t = BluezTransport()
    t._address = "C0:FF:EE:CA:11:F0"

    async def _iface(path, iface):
        raise RuntimeError("no system bus")
    monkeypatch.setattr(t, "_get_interface", _iface)
    assert asyncio.run(t._device_bonded()) is False


def test_pair_propagates_a_non_stale_bond_failure(monkeypatch):
    """Any pairing error OTHER than AlreadyExists is a genuine failure: pair() must propagate it
    unchanged and must NOT wipe the bond (which would mask a real problem such as an auth failure).

    .. test:: pair() propagates non-AlreadyExists failures
       :id: T_PAIRING_FAILURE_PROPAGATES
       :links: R_PAIRING_STALE_BOND_RECOVERY
    """
    async def _call_pair(n):
        raise Exception("org.bluez.Error.AuthenticationFailed: Authentication Failed")

    t, events, calls = _pair_transport(monkeypatch, _call_pair)
    with pytest.raises(Exception) as excinfo:
        asyncio.run(t.pair())

    assert "AuthenticationFailed" in str(excinfo.value)
    assert calls["pair"] == 1          # no retry
    assert calls["remove_bond"] == 0   # bond left untouched
    assert events == []                # no EV_PAIR_OK


def test_construction_defaults():
    t = BluezTransport()
    assert t.on_event is None
    assert t._device_name == "VWCAMPER"
    assert t._adapter_path == "/org/bluez/hci0"
    assert t._address is None
    assert t._passkey_future is None


def test_construction_with_overrides():
    async def _cb(ev, arg=0):
        pass

    t = BluezTransport(on_event=_cb, device_name="OTHER", adapter_path="/org/bluez/hci1")
    assert t.on_event is _cb
    assert t._device_name == "OTHER"
    assert t._adapter_path == "/org/bluez/hci1"


def test_send_passkey_resolves_the_pending_future():
    async def _run():
        t = BluezTransport()
        t._passkey_future = asyncio.get_running_loop().create_future()
        await t.send_passkey(123456)
        return t._passkey_future.result()

    assert asyncio.run(_run()) == 123456


def test_send_passkey_without_a_pending_future_is_a_noop():
    async def _run():
        t = BluezTransport()
        await t.send_passkey(123456)  # must not raise
        return t._passkey_future

    assert asyncio.run(_run()) is None


def test_send_passkey_ignores_an_already_resolved_future():
    async def _run():
        t = BluezTransport()
        fut = asyncio.get_running_loop().create_future()
        fut.set_result(1)
        t._passkey_future = fut
        await t.send_passkey(2)  # must not raise InvalidStateError
        return t._passkey_future.result()

    assert asyncio.run(_run()) == 1


class _FakeChar:
    def __init__(self, uuid, readable=True):
        self.uuid = uuid
        self.properties = ["read"] if readable else []


class _FakeService:
    def __init__(self, characteristics):
        self.characteristics = characteristics


class _FakeClient:
    """Pre-connected fake so verify() never touches its `client is None or not
    connected` branch -- that's the only place `from bleak import BleakClient`
    is imported, so this keeps the test bleak-free."""

    def __init__(self, services, unreadable_uuids=()):
        self.is_connected = True
        self.services = services
        self._unreadable = set(unreadable_uuids)

    async def read_gatt_char(self, uuid):
        if uuid in self._unreadable:
            raise RuntimeError("read failed: %s" % uuid)

    async def disconnect(self):
        pass


def test_verify_returns_none_when_zero_state_chars_are_readable():
    """An unbonded RPA link can ACK the connection + version/auth reads yet drop
    every state-char read -- verify() must fail closed (None), not report a
    hollow "0 readable" success (the gap the review caught)."""
    from calictl import device as device_mod

    t = BluezTransport()
    t._client = _FakeClient(services=[_FakeService([_FakeChar("0000f001")])],
                             unreadable_uuids={"0000f001"})
    assert asyncio.run(t.verify()) is None
    # sanity: VERSION/AUTH themselves are readable in this fixture (only the state char isn't)
    assert device_mod.VERSION_CHAR not in t._client._unreadable


def test_verify_returns_positive_count_when_chars_are_readable():
    t = BluezTransport()
    t._client = _FakeClient(services=[
        _FakeService([_FakeChar("0000f001"), _FakeChar("0000f002", readable=False)]),
    ])
    assert asyncio.run(t.verify()) == 1


def test_verify_returns_none_when_version_or_auth_read_fails():
    from calictl import device as device_mod

    t = BluezTransport()
    t._client = _FakeClient(services=[_FakeService([_FakeChar("0000f001")])],
                             unreadable_uuids={device_mod.VERSION_CHAR})
    assert asyncio.run(t.verify()) is None


def test_remove_bond_clears_the_pairing_cache(monkeypatch, tmp_path):
    """Unpair (ACT_REMOVE_BOND -> remove_bond) must delete the persisted pairing cache, else the
    address survives a reboot and the daemon re-targets the bond the user just removed. The dbus
    half no-ops on a dev box (no system bus / dbus_fast); the cache clear must run regardless."""
    cache = tmp_path / "pairing.json"
    cache.write_text('{"address": "AA:BB:CC:DD:EE:FF"}')
    monkeypatch.setenv("CALICTL_PAIRING_CACHE", str(cache))
    t = BluezTransport()
    asyncio.run(t.remove_bond())
    assert not cache.exists()


def test_remove_bond_is_a_noop_when_no_cache_exists(monkeypatch, tmp_path):
    """Unpair when nothing was ever persisted (no wizard bond) must not raise."""
    monkeypatch.setenv("CALICTL_PAIRING_CACHE", str(tmp_path / "missing.json"))
    t = BluezTransport()
    asyncio.run(t.remove_bond())  # no exception


def test_persist_bond_writes_owner_only_cache(monkeypatch, tmp_path):
    """The wizard's persist_bond must write the cache 0600 like install.sh does: the identity
    address is owner PII, and a re-pair must not silently widen the file the installer created
    owner-only. The dbus Address lookup no-ops on a dev box, so the scan address is persisted."""
    cache = tmp_path / "state" / "calictl" / "pairing.json"
    monkeypatch.setenv("CALICTL_PAIRING_CACHE", str(cache))
    t = BluezTransport()
    t._address = "AA:BB:CC:DD:EE:FF"
    assert asyncio.run(t.persist_bond()) == "AA:BB:CC:DD:EE:FF"
    assert cache.read_text() == '{"address": "AA:BB:CC:DD:EE:FF"}'
    assert cache.stat().st_mode & 0o777 == 0o600


def test_adapter_name_is_derived_from_the_adapter_path():
    assert BluezTransport().adapter == "hci0"
    assert BluezTransport(adapter_path="/org/bluez/hci1").adapter == "hci1"


def test_stop_scan_flags_radio_busy_when_another_client_keeps_discovering(monkeypatch):
    """Another BlueZ client (the Home Assistant Bluetooth integration, a BLE reader) holding a
    discovery session keeps the controller scanning at full duty after OUR scan stopped; a new LE
    link then dies with HCI 0x3e (btmon, 2026-09-25). Surface it instead of failing blind."""
    t = BluezTransport()

    async def discovering():
        return True
    monkeypatch.setattr(t, "_adapter_discovering", discovering)
    asyncio.run(t.stop_scan())
    assert t.radio_busy is True


def test_stop_scan_clears_radio_busy_on_a_quiet_radio(monkeypatch):
    t = BluezTransport()
    t.radio_busy = True

    async def discovering():
        return False
    monkeypatch.setattr(t, "_adapter_discovering", discovering)
    asyncio.run(t.stop_scan())
    assert t.radio_busy is False


class _BLEDevice:
    def __init__(self, address, path):
        self.address = address
        self.name = "VWCAMPER"
        self.details = {"path": path, "props": {}}


def test_device_path_is_the_object_bluez_reported_not_one_rebuilt_from_the_address():
    """BlueZ names a device object after the address it was FIRST seen under (the unit's RPA) and
    never renames it; once bonded, later adverts resolve to the identity, so the scan reports the
    identity while the object stays at dev_<RPA>. A path rebuilt from the address named a missing
    object -> the stale-bond check read "not bonded" and the wizard hung (real-BlueZ CI rig).

    .. test:: the transport uses BlueZ's own device object path
       :id: T_PAIRING_DEVICE_OBJECT_PATH
       :links: R_PAIRING_STALE_BOND_RECOVERY
    """
    t = BluezTransport(adapter_path="/org/bluez/hci1")
    t._set_found(_BLEDevice("C0:FF:EE:CA:11:F0", "/org/bluez/hci1/dev_53_B9_AA_7F_BC_C2"))
    assert t._address == "C0:FF:EE:CA:11:F0"
    assert t._device_path() == "/org/bluez/hci1/dev_53_B9_AA_7F_BC_C2"


def test_device_path_falls_back_to_the_address_without_a_reported_path():
    t = BluezTransport(adapter_path="/org/bluez/hci1")
    t._set_found(type("D", (), {"address": "c0:ff:ee:ca:11:f0", "details": None})())
    assert t._device_path() == "/org/bluez/hci1/dev_C0_FF_EE_CA_11_F0"


def test_remove_bond_forgets_the_object_path(monkeypatch, tmp_path):
    monkeypatch.setenv("CALICTL_PAIRING_CACHE", str(tmp_path / "pairing.json"))
    t = BluezTransport()
    t._set_found(_BLEDevice("C0:FF:EE:CA:11:F0", "/org/bluez/hci0/dev_53_B9_AA_7F_BC_C2"))
    asyncio.run(t.remove_bond())    # the dbus half no-ops off-hardware
    assert t._path is None and t._address is None
    with pytest.raises(RuntimeError):
        t._device_path()


class _Variant:
    def __init__(self, value):
        self.value = value


def _device1(name="VWCAMPER", rssi=-50, address="C0:FF:EE:CA:11:F0"):
    props = {"Address": _Variant(address), "Name": _Variant(name)}
    if rssi is not None:
        props["RSSI"] = _Variant(rssi)
    return {"org.bluez.Device1": props}


def _adopt(monkeypatch, objects, found=None):
    """Run _adopt_known_device() over a fake ObjectManager (and a stub bleak BLEDevice); returns
    (transport, events)."""
    import sys
    import types

    class _BLEDevice:
        def __init__(self, address, name, details):
            self.address, self.name, self.details = address, name, details

    monkeypatch.setitem(sys.modules, "bleak", types.ModuleType("bleak"))
    monkeypatch.setitem(sys.modules, "bleak.backends", types.ModuleType("bleak.backends"))
    monkeypatch.setitem(sys.modules, "bleak.backends.device",
                        types.SimpleNamespace(BLEDevice=_BLEDevice))
    events = []

    async def _cb(ev, arg=0):
        events.append(ev)

    class _OM:
        async def call_get_managed_objects(self):
            return objects

    async def _iface(path, iface):
        assert (path, iface) == ("/", "org.freedesktop.DBus.ObjectManager")
        return _OM()

    t = BluezTransport(on_event=_cb, adapter_path="/org/bluez/hci1")
    t._found_device = found
    monkeypatch.setattr(t, "_get_interface", _iface)
    asyncio.run(t._adopt_known_device())
    return t, events


def test_scan_adopts_a_unit_bluez_already_hears(monkeypatch):
    """While another client keeps discovery on, BlueZ neither restarts the scan nor resets RSSI,
    so a unit it already knows (bonded before, or seen by that client) with a steady signal and
    static adverts raises no new bleak callback -- the wizard scanned until its timeout
    (real-BlueZ CI rig). A known Device1 with our name and an RSSI (= heard in the current
    discovery) is adopted directly, on its own object path.

    .. test:: the scan adopts a unit BlueZ already hears
       :id: T_PAIRING_ADOPT_KNOWN_DEVICE
       :links: R_PAIRING_BLUEZ_TRANSPORT
    """
    from calictl.pairing import EV_DEVICE_FOUND

    t, events = _adopt(monkeypatch, {
        "/org/bluez/hci0/dev_11_11_11_11_11_11": _device1(),              # another adapter
        "/org/bluez/hci1/dev_22_22_22_22_22_22": _device1(name="OTHER"),  # not the unit
        "/org/bluez/hci1/dev_33_33_33_33_33_33": _device1(rssi=None),     # known, not heard now
        "/org/bluez/hci1/dev_6A_61_C2_C5_FA_D1": _device1(),              # the unit
        "/org/bluez/hci1": {"org.bluez.Adapter1": {}},
    })
    assert events == [EV_DEVICE_FOUND]
    assert t._address == "C0:FF:EE:CA:11:F0"
    assert t._device_path() == "/org/bluez/hci1/dev_6A_61_C2_C5_FA_D1"
    assert t._found_device.details["path"] == "/org/bluez/hci1/dev_6A_61_C2_C5_FA_D1"


def test_scan_does_not_adopt_a_unit_only_known_from_before(monkeypatch):
    _, events = _adopt(monkeypatch, {"/org/bluez/hci1/dev_33_33_33_33_33_33": _device1(rssi=None)})
    assert events == []


def test_scan_adoption_defers_to_a_device_the_callback_already_found(monkeypatch):
    t, events = _adopt(monkeypatch, {"/org/bluez/hci1/dev_6A_61_C2_C5_FA_D1": _device1()},
                       found="from-callback")
    assert events == [] and t._found_device == "from-callback"


def test_scan_adoption_is_best_effort_without_a_bus(monkeypatch):
    async def _cb(ev, arg=0):
        raise AssertionError("no event expected")

    async def _iface(path, iface):
        raise RuntimeError("no system bus")

    t = BluezTransport(on_event=_cb)
    monkeypatch.setattr(t, "_get_interface", _iface)
    asyncio.run(t._adopt_known_device())    # must not raise
