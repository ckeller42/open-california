"""Async runner: drives the pure pairing SM (`calictl.pairing`) against a transport.

.. req:: Transport runner drives the pairing SM
   :id: R_PAIRING_RUNNER

   Wraps ``pairing.step`` with asyncio glue: dispatches each ``ACT_*`` to a
   same-named coroutine on a pluggable transport, arms/cancels a per-state
   ``asyncio`` timeout timer from ``TIMEOUT_S``, and turns transport results
   (``verify()``/``persist_bond()``) and transport-call exceptions back into
   events. No dbus/bleak imports here — stdlib-only at import (Task 3 adds the
   concrete BlueZ transport in this same module, with its own lazy imports).
"""
import asyncio
import json

from calictl import pairing
from calictl.device import pairing_cache_path
from calictl.pairing import (
    ACT_CONNECT,
    ACT_DISCONNECT,
    ACT_PAIR,
    ACT_PERSIST_BOND,
    ACT_REMOVE_BOND,
    ACT_SEND_PASSKEY,
    ACT_START_SCAN,
    ACT_STOP_SCAN,
    ACT_VERIFY,
    BONDED,
    ERR_NAMES,
    ERR_NONE,
    ERROR,
    EV_CANCEL,
    EV_CONNECTED,
    EV_DEVICE_FOUND,
    EV_PAIR_FAIL,
    EV_PAIR_OK,
    EV_PASSKEY_ENTERED,
    EV_PASSKEY_REQUESTED,
    EV_RESET,
    EV_RESET_DONE,
    EV_START,
    EV_TIMEOUT,
    EV_VERIFY_FAIL,
    EV_VERIFY_OK,
    IDLE,
    STATE_NAMES,
    PairingState,
    step,
)

# Actions whose transport-call failure means "the pairing attempt failed" (-> EV_PAIR_FAIL);
# ACT_VERIFY is handled separately since it also has a non-exception failure mode (None result).
_PAIR_FAIL_ACTS = (ACT_CONNECT, ACT_PAIR, ACT_SEND_PASSKEY)


class PairingRunner:
    """Drives :func:`calictl.pairing.step` against a transport; owns the asyncio timeout timer.

    :param transport: object with async ``start_scan/stop_scan/connect/pair/
        send_passkey(pk)/verify()->int|None/persist_bond()->str|None/disconnect/
        remove_bond``. Transport callbacks (device found, connected, passkey
        requested, pair result) feed back in through :meth:`handle`.
    """

    def __init__(self, transport):
        self._transport = transport
        self._ps = PairingState(IDLE, 0, ERR_NONE)
        self._timer_task = None
        self.address = None
        self.on_bonded = None  # optional sync callable(address); fired once persist_bond lands

    @property
    def state(self):
        return self._ps

    async def handle(self, ev, arg=0):
        """Advance the SM by one event, dispatch its actions, (re)arm the timeout timer."""
        if ev in (EV_CANCEL, EV_RESET):
            self.address = None  # don't let a stale bond address survive an abandon/reset
        old = self._ps
        self._ps, actions = step(self._ps, ev, arg)
        mine = self._ps   # THIS frame's own transition target -- ACT_VERIFY dispatches a NESTED
        # handle() call (VERIFYING -> BONDED/ERROR) from inside the loop below, which mutates
        # self._ps out from under us; comparing against `self._ps` after the loop would see the
        # nested call's state and double-fire the cleanup below. `mine` pins what THIS call
        # actually transitioned to, so each frame closes the transport at most once.
        if mine != old:
            self._rearm_timer()
        for act, act_arg in actions:
            await self._dispatch(act, act_arg)
        # Flow-end cleanup: bonded/error/idle (cancel or post-reset) all mean the radio is free
        # again -- unregister our KeyboardOnly D-Bus agent so it doesn't squat as the system
        # default for the daemon's whole remaining lifetime (hci0 is shared; another host's pair
        # attempt would otherwise block on OUR passkey future forever). Best-effort: transports
        # without aclose() (e.g. FakeTransport in tests) are skipped via hasattr.
        if mine != old and mine.st in (BONDED, ERROR, IDLE):
            aclose = getattr(self._transport, "aclose", None)
            if aclose is not None:
                try:
                    await aclose()
                except Exception as e:
                    print("pairing: transport aclose failed: %r" % e, flush=True)

    def _rearm_timer(self):
        if self._timer_task is not None:
            self._timer_task.cancel()
            self._timer_task = None
        if self._ps.st in pairing.TIMEOUT_S:
            seconds = pairing.TIMEOUT_S[self._ps.st]
            self._timer_task = asyncio.ensure_future(self._timeout_after(self._ps, seconds))

    async def _timeout_after(self, armed_ps, seconds):
        await asyncio.sleep(seconds)
        if self._ps == armed_ps:  # guard: state must not have moved on since arming
            await self.handle(EV_TIMEOUT)

    async def _dispatch(self, act, arg):
        t = self._transport
        try:
            if act == ACT_START_SCAN:
                await t.start_scan()
            elif act == ACT_STOP_SCAN:
                await t.stop_scan()
            elif act == ACT_CONNECT:
                await t.connect()
            elif act == ACT_PAIR:
                await t.pair()
            elif act == ACT_SEND_PASSKEY:
                await t.send_passkey(arg)
            elif act == ACT_VERIFY:
                result = await t.verify()
            elif act == ACT_PERSIST_BOND:
                result = await t.persist_bond()
            elif act == ACT_DISCONNECT:
                await t.disconnect()
            elif act == ACT_REMOVE_BOND:
                await t.remove_bond()
        except Exception as e:
            if act == ACT_PERSIST_BOND:
                # BlueZ owns the real bond regardless of this cache write -- the bond
                # itself is intact, only our convenience address cache failed. Stay
                # BONDED; self.address stays None (see snapshot()'s docstring).
                print("pairing: address-cache write failed, bond itself is intact: %r" % e, flush=True)
                return
            print("pairing: transport action %d failed: %r" % (act, e), flush=True)
            if act in _PAIR_FAIL_ACTS:
                await self.handle(EV_PAIR_FAIL)
            elif act == ACT_VERIFY:
                await self.handle(EV_VERIFY_FAIL)
            # scan/disconnect/remove_bond errors: nothing else can retry them here, just log
            return

        if act == ACT_VERIFY:
            if result is not None:
                await self.handle(EV_VERIFY_OK, result)
            else:
                await self.handle(EV_VERIFY_FAIL)
        elif act == ACT_PERSIST_BOND:
            self.address = result
            if result is not None and self.on_bonded is not None:
                self.on_bonded(result)

    async def start(self):
        await self.handle(EV_START)

    async def cancel(self):
        await self.handle(EV_CANCEL)

    async def reset(self):
        await self.handle(EV_RESET)
        await self.handle(EV_RESET_DONE)

    async def enter_passkey(self, pk):
        await self.handle(EV_PASSKEY_ENTERED, pk)

    def snapshot(self):
        """Return the SM state as plain names/values for a UI to render.

        :returns: ``{"state", "attempts", "error", "address"}``. If ``state`` is
            ``"bonded"`` and ``address`` is ``None``, the bond itself succeeded but the
            address-cache write (``transport.persist_bond()``) failed -- pairing is
            still fully bonded, just without a cached address.
        """
        return {
            "state": STATE_NAMES[self._ps.st],
            "attempts": self._ps.attempts,
            "error": ERR_NAMES[self._ps.error],
            "address": self.address,
        }


AGENT_PATH = "/org/calictl/pairing_agent"


class BluezTransport:
    """Real BlueZ transport for :class:`PairingRunner`: bleak scan/GATT + a dbus-fast agent.

    .. req:: BlueZ transport for guided pairing
       :id: R_PAIRING_BLUEZ_TRANSPORT

       Implements the runner's transport contract (``start_scan``/``stop_scan``/
       ``connect``/``pair``/``send_passkey``/``verify``/``persist_bond``/
       ``disconnect``/``remove_bond``) against real BlueZ: `bleak` for
       scanning + GATT reads, `dbus_fast` for the ``org.bluez.Agent1``
       KeyboardOnly passkey agent and the ``Device1``/``Adapter1`` pair /
       remove-device calls. Every ``bleak``/``dbus_fast`` import is lazy
       (inside methods), so importing this module stays stdlib-only.
       **NOT-LIVE-VERIFIED**: no dbus/BLE stack in CI, so only the
       stdlib-only plumbing (construction, the ``_passkey_future`` hookup) is
       test-covered here; the dbus/bleak call sequence is exercised against
       real hardware in the #157 van session.

    :param on_event: async ``callable(ev, arg=0)`` fed pairing-SM events as
        the transport observes them (device found, connected, passkey
        requested, pair success) — normally :meth:`PairingRunner.handle`.
        The runner's constructor takes the transport, so wire this up after
        both exist: ``t = BluezTransport(); r = PairingRunner(t);
        t.on_event = r.handle``.
    :param device_name: BLE advertised name to scan for.
    :param adapter_path: BlueZ adapter D-Bus object path.
    """

    def __init__(self, on_event=None, device_name="VWCAMPER", adapter_path="/org/bluez/hci0"):
        self.on_event = on_event
        self._device_name = device_name
        self._adapter_path = adapter_path
        self._address = None       # discovered device's BLE address (identity, once bonded)
        self._found_device = None  # bleak BLEDevice set by the scan detection callback
        self._scanner = None
        self._client = None        # bleak BleakClient set by connect()
        self._bus = None           # dbus_fast system MessageBus, set by _ensure_bus()
        self._agent = None
        self._agent_mgr = None
        self._passkey_future = None  # resolved by send_passkey(); awaited by the dbus agent

    async def _emit(self, ev, arg=0):
        if self.on_event is not None:
            await self.on_event(ev, arg)

    # --- scan --------------------------------------------------------------
    async def start_scan(self):
        from bleak import BleakScanner

        async def _on_detect(device, adv_data):
            if self._found_device is not None or device.name != self._device_name:
                return
            self._found_device = device
            self._address = device.address
            await self._emit(EV_DEVICE_FOUND)

        self._found_device = None
        self._scanner = BleakScanner(detection_callback=_on_detect)
        await self._scanner.start()

    async def stop_scan(self):
        if self._scanner is not None:
            await self._scanner.stop()
            self._scanner = None

    # --- connect -------------------------------------------------------------
    async def connect(self):
        from bleak import BleakClient

        self._client = BleakClient(self._found_device)
        await self._client.connect()
        await self._emit(EV_CONNECTED)

    def _device_path(self):
        if not self._address:
            raise RuntimeError("no discovered device address")
        return "%s/dev_%s" % (self._adapter_path, self._address.replace(":", "_").upper())

    async def _ensure_bus(self):
        if self._bus is not None:
            return self._bus
        from dbus_fast import BusType
        from dbus_fast.aio import MessageBus

        self._bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        return self._bus

    async def _get_interface(self, path, iface):
        bus = await self._ensure_bus()
        intro = await bus.introspect("org.bluez", path)
        obj = bus.get_proxy_object("org.bluez", path, intro)
        return obj.get_interface(iface)

    # --- pairing agent (LE Passkey Entry, KeyboardOnly: we SEND the passkey) --
    async def _ensure_agent(self):
        if self._agent is not None:
            return
        from dbus_fast.service import ServiceInterface, method

        transport = self

        class _PairingAgent(ServiceInterface):
            def __init__(self):
                super().__init__("org.bluez.Agent1")

            @method()
            async def RequestPasskey(self, device: "o") -> "u":  # noqa: N802,N803,F821
                transport._passkey_future = asyncio.get_running_loop().create_future()
                await transport._emit(EV_PASSKEY_REQUESTED)
                return await transport._passkey_future

            @method()
            def Cancel(self):  # noqa: N802
                if transport._passkey_future is not None and not transport._passkey_future.done():
                    transport._passkey_future.cancel()

            @method()
            def Release(self):  # noqa: N802
                pass

        bus = await self._ensure_bus()
        self._agent = _PairingAgent()
        bus.export(AGENT_PATH, self._agent)
        self._agent_mgr = await self._get_interface("/org/bluez", "org.bluez.AgentManager1")
        await self._agent_mgr.call_register_agent(AGENT_PATH, "KeyboardOnly")
        await self._agent_mgr.call_request_default_agent(AGENT_PATH)

    async def pair(self):
        await self._ensure_agent()
        device_iface = await self._get_interface(self._device_path(), "org.bluez.Device1")
        await device_iface.call_pair()
        await self._emit(EV_PAIR_OK)

    async def send_passkey(self, pk):
        if self._passkey_future is not None and not self._passkey_future.done():
            self._passkey_future.set_result(pk)

    # --- verify: connect + read VERSION/AUTH + count readable state chars ---
    async def verify(self):
        """Verify policy: version+auth reads must succeed AND at least one state
        characteristic must be readable, else the link is treated as unbonded/dropped.

        :returns: the readable-char count (> 0) on success, ``None`` on any read
            failure (version/auth char, or zero readable state chars — an unbonded
            RPA link that ACKs the connection but drops every real read). The full
            20/20-readable-characteristics signature this policy approximates is
            confirmed against real hardware in the #157 van session, not here.
        """
        from calictl import device as device_mod

        client = self._client
        owns_client = False
        try:
            if client is None or not client.is_connected:
                from bleak import BleakClient

                client = BleakClient(self._found_device)
                await client.connect()
                owns_client = True
            await client.read_gatt_char(device_mod.VERSION_CHAR)
            await client.read_gatt_char(device_mod.AUTH_CHAR)
            count = 0
            for service in client.services:
                for char in service.characteristics:
                    if "read" not in char.properties:
                        continue
                    try:
                        await client.read_gatt_char(char.uuid)
                        count += 1
                    except Exception:
                        pass
            return count if count > 0 else None
        except Exception:
            return None
        finally:
            if owns_client:
                await client.disconnect()

    # --- bond persistence / teardown -----------------------------------------
    async def persist_bond(self):
        try:
            addr = self._address
            try:
                props = await self._get_interface(self._device_path(), "org.freedesktop.DBus.Properties")
                variant = await props.call_get("org.bluez.Device1", "Address")
                addr = variant.value
            except Exception:
                pass  # fall back to the address discovered during scan
            cache_path = pairing_cache_path()
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps({"address": addr}))
            return addr
        except Exception:
            return None

    async def disconnect(self):
        if self._client is not None:
            try:
                await self._client.disconnect()
            except Exception:
                pass
            self._client = None

    async def remove_bond(self):
        try:
            adapter = await self._get_interface(self._adapter_path, "org.bluez.Adapter1")
            await adapter.call_remove_device(self._device_path())
        except Exception:
            pass
        finally:
            self._found_device = None
            self._address = None
            self._client = None
        # Clear the persisted address too, else resolve_addr() re-reads it after a reboot and the
        # daemon re-targets the bond we just removed. Independent of the bluez call above (which
        # no-ops off-hardware), and best-effort: a cache-clear failure must not surface as a
        # pairing error.
        try:
            pairing_cache_path().unlink(missing_ok=True)
        except OSError:
            pass

    async def aclose(self):
        """Unregister the D-Bus agent and drop the system-bus connection (call at flow end)."""
        try:
            if self._agent_mgr is not None:
                await self._agent_mgr.call_unregister_agent(AGENT_PATH)
        except Exception:
            pass
        if self._bus is not None:
            self._bus.disconnect()
            self._bus = None
        self._agent = None
        self._agent_mgr = None
