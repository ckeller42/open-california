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
import re

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
    EV_CONNECT_FAIL,
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

from . import log as _log

log = _log.get(__name__)

# Actions whose transport-call failure means "the pairing attempt failed" (-> EV_PAIR_FAIL);
# ACT_VERIFY is handled separately since it also has a non-exception failure mode (None result).
_PAIR_FAIL_ACTS = (ACT_PAIR, ACT_SEND_PASSKEY)


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
                    log.warning("pairing: transport aclose failed: %r" % e)

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
                log.warning("pairing: address-cache write failed, bond itself is intact: %r" % e)
                return
            log.warning("pairing: transport action %d failed: %r" % (act, e))
            if act == ACT_CONNECT:
                await self.handle(EV_CONNECT_FAIL)
            elif act in _PAIR_FAIL_ACTS:
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

        :returns: ``{"state", "attempts", "error", "address", "radio_busy"}``. If ``state`` is
            ``"bonded"`` and ``address`` is ``None``, the bond itself succeeded but the
            address-cache write (``transport.persist_bond()``) failed -- pairing is
            still fully bonded, just without a cached address. ``radio_busy`` mirrors the
            transport's ``radio_busy`` flag (default ``False`` for transports without one,
            e.g. ``FakeTransport`` in tests): True when another BlueZ client (e.g. the Home
            Assistant Bluetooth integration or a BLE reader) was still holding
            ``Adapter1.Discovering`` after our own scan stopped, which makes a subsequent
            LE connect likely to fail (HCI 0x3e) -- see :meth:`BluezTransport.stop_scan`.
        """
        return {
            "state": STATE_NAMES[self._ps.st],
            "attempts": self._ps.attempts,
            "error": ERR_NAMES[self._ps.error],
            "address": self.address,
            "radio_busy": bool(getattr(self._transport, "radio_busy", False)),
        }


AGENT_PATH = "/org/calictl/pairing_agent"

# BluezTransport.connect() budget. Everything connect() does shares ONE deadline,
# pairing.TIMEOUT_S[CONNECTING] - CONNECT_MARGIN_S, so it finishes (or fails) before the SM's own
# CONNECTING timer fires. With CONNECTING = 20 s the worst case (a stale bond) splits the 19 s as:
# bond probe <= BOND_PROBE_S (5) + RemoveDevice (~ms) + re-discover <= REDISCOVER_S (5) + the final
# connect, which always keeps >= MIN_CONNECT_S (8) — bleak's own default connect timeout is 10 s.
CONNECT_MARGIN_S = 1.0
BOND_PROBE_S = 5.0      # connect + auth-gated read over an existing bond; a stale key hangs, so bound it
REDISCOVER_S = 5.0      # find the unit again after RemoveDevice dropped its object
MIN_CONNECT_S = 8.0     # always left for the final BleakClient.connect()
LINK_POLL_S = 0.1       # how often the bond probe samples Device1.Connected (see _LinkWatch)
CLOSE_DISCONNECT_S = 5.0  # aclose(): bound on releasing the wizard's own link at flow end

# Error texts/codes that mean "the link came up but the peer rejected our stored key": BlueZ/kernel
# "PIN or Key Missing" (HCI 0x06), "Authentication Failed" (HCI 0x05), a read refused for missing
# authentication/encryption (ATT 0x05 / 0x0F, bluez NotPermitted/NotAuthorized). Only these (or a
# probe that timed out AFTER the link was up) prove a stale bond; everything else — HCI 0x3e, an
# asleep unit, a busy radio — is a connect failure that must keep the bond.
_AUTH_FAILURE_RE = re.compile(
    r"pin or key missing|authenticat|notpermitted|not permitted|notauthorized|not authorized"
    r"|insufficient (authentication|encryption)|\b0x0?[56f]\b", re.IGNORECASE)


def is_auth_failure(exc) -> bool:
    """Whether ``exc`` is an authentication-class failure (the peer rejected our stored key).

    :param exc: an exception from a bleak/BlueZ connect or GATT read, or a reason string.
    :returns: True for "PIN or Key Missing" / HCI 0x05, 0x06 / ATT 0x05, 0x0F / "Authentication" /
        ``NotPermitted`` / ``NotAuthorized`` / "Insufficient Authentication|Encryption"; False for
        everything else (e.g. HCI 0x3e ``le-connection-abort-by-local``, a timeout).
    """
    if exc is None:
        return False
    text = exc if isinstance(exc, str) else " ".join(
        str(x) for x in (exc, getattr(exc, "dbus_error", ""), getattr(exc, "dbus_error_details", "")))
    return bool(_AUTH_FAILURE_RE.search(text))


class StaleBond(Exception):
    """The bond probe proved BlueZ's stored key is stale (auth-class failure, or the link came up
    but the auth-gated read never completed) — the only case :meth:`BluezTransport.connect`
    drops a bond on its own."""


def _log_task_exception(task):
    """Done-callback: retrieve a background task's exception so it is logged, never lost."""
    if not task.cancelled() and task.exception() is not None:
        log.warning("pairing: background task failed: %r" % task.exception())


def _make_bledevice(address, name, details, rssi):
    """Build a bleak ``BLEDevice`` across bleak versions: < 1.0 requires a positional ``rssi``,
    1.x dropped it (``BLEDevice(address, name, details)``)."""
    import inspect

    from bleak.backends.device import BLEDevice

    try:
        params = inspect.signature(BLEDevice.__init__).parameters
    except (TypeError, ValueError):
        params = {}
    if "rssi" in params:
        return BLEDevice(address, name, details, rssi)
    return BLEDevice(address, name, details)


class _LinkWatch:
    """Observes ``Device1.Connected`` (and BlueZ's ``Device1.Disconnected`` reason, where the
    BlueZ version has it) for the duration of a bond probe.

    A stale key shows as "the link came up, then encryption failed and it dropped": BlueZ flips
    ``Connected`` True for a moment and reconnects forever. Both a ``PropertiesChanged``
    subscription and a :data:`LINK_POLL_S` sampler record it, since BlueZ may coalesce a quick
    True->False into one property emission. Best-effort: without a bus it records nothing (then
    only an auth-class error text can prove a stale bond — never a bare timeout).
    """

    def __init__(self):
        self.seen_up = False
        self.auth_reason = None   # a Device1.Disconnected reason naming authentication
        self._props = None
        self._on_changed = None
        self._dev = None
        self._on_disc = None
        self._poller = None
        self._props_get = None    # the Properties proxy the sampler reads Connected through

    async def start(self, transport, path):
        try:
            props = await transport._get_interface(path, "org.freedesktop.DBus.Properties")
        except Exception as e:
            log.debug("pairing: link watch unavailable: %r" % e)
            return self

        def _on_changed(iface, changed, invalidated):
            if iface == "org.bluez.Device1" and "Connected" in changed:
                if getattr(changed["Connected"], "value", changed["Connected"]):
                    self.seen_up = True

        try:
            props.on_properties_changed(_on_changed)
            self._props, self._on_changed = props, _on_changed
        except Exception as e:
            log.debug("pairing: PropertiesChanged subscription failed: %r" % e)
        try:
            dev = await transport._get_interface(path, "org.bluez.Device1")
            on_disc = getattr(dev, "on_disconnected", None)   # BlueZ >= 5.8x only
            if on_disc is not None:
                def _disc(name, message=""):
                    if is_auth_failure("%s %s" % (name, message)):
                        self.auth_reason = "%s %s" % (name, message)
                on_disc(_disc)
                self._dev, self._on_disc = dev, _disc
        except Exception:
            pass

        async def _poll():
            while True:
                await self.sample()
                await asyncio.sleep(LINK_POLL_S)

        self._props_get = props
        self._poller = asyncio.ensure_future(_poll())
        self._poller.add_done_callback(_log_task_exception)
        return self

    async def sample(self):
        """Read ``Device1.Connected`` once (best-effort) and record a True."""
        if self._props_get is None:
            return
        try:
            if (await self._props_get.call_get("org.bluez.Device1", "Connected")).value:
                self.seen_up = True
        except Exception:
            pass

    def close(self):
        if self._poller is not None:
            self._poller.cancel()
            self._poller = None
        try:
            if self._props is not None:
                self._props.off_properties_changed(self._on_changed)
        except Exception:
            pass
        try:
            if self._dev is not None:
                self._dev.off_disconnected(self._on_disc)
        except Exception:
            pass
        self._props = self._dev = None


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
       A bond BlueZ already holds is dropped ONLY when the bond probe proves the key stale
       (an auth-class failure, or a probe timeout after ``Device1.Connected`` was seen True);
       any other probe failure (HCI 0x3e, an asleep unit adopted via a stale RSSI, no probe
       budget) is ``EV_CONNECT_FAIL`` with the bond AND ``pairing.json`` kept. The in-flow
       stale-bond drop never clears ``pairing.json``; only the explicit reset
       (``ACT_REMOVE_BOND``) does. At flow end (``aclose``) the wizard releases its own link so
       the daemon can read right after ``BONDED``.
       **CI-verified against real BlueZ** (``T_PAIRING_REALSTACK``, the
       ``pairing-real-stack`` job): inside a VM, the real dbus/bleak/BlueZ sequence
       (agent, scan, connect, Pair, verify, persist, link release at flow end, stale-bond
       probe/removal, an asleep unit keeping its bond, ``radio_busy``) runs against the Bumble
       fake unit over a virtual controller;
       the unit tests cover the stdlib-only logic with stubs. CI still does NOT cover a
       real unit's radio behaviour: RSSI jitter, its advertising policy and deep sleep,
       WiFi coexistence on buspi's shared radio, or other clients scanning at full duty
       and starving LE connects (HCI 0x3e) — those remain for the #157 van session.

    :param on_event: async ``callable(ev, arg=0)`` fed pairing-SM events as
        the transport observes them (device found, connected, passkey
        requested, pair success) — normally :meth:`PairingRunner.handle`.
        The runner's constructor takes the transport, so wire this up after
        both exist: ``t = BluezTransport(); r = PairingRunner(t);
        t.on_event = r.handle``.
    :param device_name: BLE advertised name to scan for.
    :param adapter_path: BlueZ adapter D-Bus object path. :attr:`adapter` (bleak's own name for
        it, e.g. ``"hci1"``) is derived from this and threaded into every ``bleak`` scanner/
        client this transport constructs -- a later CI job pairs over a virtual ``hciN`` adapter.
    """

    def __init__(self, on_event=None, device_name="VWCAMPER", adapter_path="/org/bluez/hci0"):
        self.on_event = on_event
        self._device_name = device_name
        self._adapter_path = adapter_path
        self.adapter = adapter_path.rsplit("/", 1)[-1]   # "/org/bluez/hci1" -> "hci1" (bleak's name)
        self.radio_busy = False    # another BlueZ client kept discovery on after our scan stopped
        self._address = None       # discovered device's BLE address (identity, once bonded)
        self._found_device = None  # bleak BLEDevice set by the scan detection callback
        self._path = None          # BlueZ's D-Bus object path for it (see _device_path)
        self._scanner = None
        self._adopt_task = None    # start_scan()'s already-known-device lookup (see _adopt_known_device)
        self._client = None        # bleak BleakClient set by connect()
        self._bus = None           # dbus_fast system MessageBus, set by _ensure_bus()
        self._agent = None
        self._agent_mgr = None
        self._passkey_future = None  # resolved by send_passkey(); awaited by the dbus agent
        self._gen = 0              # flow generation: bumped by start_scan()/aclose() (see _abandoned)
        self._bond_valid = False   # connect() found an existing bond that works -> pair() is a no-op

    async def _emit(self, ev, arg=0):
        if self.on_event is not None:
            await self.on_event(ev, arg)

    # --- scan --------------------------------------------------------------
    async def start_scan(self):
        from bleak import BleakScanner

        async def _on_detect(device, adv_data):
            if self._found_device is not None or device.name != self._device_name:
                return
            self._set_found(device)
            await self._emit(EV_DEVICE_FOUND)

        self._gen += 1             # a new attempt: anything still in flight from the last one is stale
        self._cancel_adopt()
        self.radio_busy = False
        self._found_device = None
        self._path = None
        self._bond_valid = False
        self._scanner = BleakScanner(detection_callback=_on_detect, adapter=self.adapter)
        await self._scanner.start()
        # Own task, like bleak's detection callback: the SM runs connect -> pair -> passkey wait
        # off EV_DEVICE_FOUND, which must not block start_scan() (and so the runner's start()).
        # The done-callback retrieves any exception so it is logged, never lost.
        self._adopt_task = asyncio.ensure_future(self._adopt_known_device(self._gen))
        self._adopt_task.add_done_callback(_log_task_exception)

    def _cancel_adopt(self):
        """Cancel a known-device lookup that is still looking (never the task we are running in:
        once the lookup emits ``EV_DEVICE_FOUND`` the whole flow runs inside it, and it detaches
        itself from :attr:`_adopt_task` first so a later stop/close can't cancel the flow)."""
        task, self._adopt_task = self._adopt_task, None
        if task is not None and not task.done() and task is not asyncio.current_task():
            task.cancel()

    async def _adopt_known_device(self, gen=None):
        """Emit ``EV_DEVICE_FOUND`` for a unit BlueZ ALREADY knows and is currently hearing.

        bleak reports a device only on ``InterfacesAdded`` or a ``PropertiesChanged``. While
        another client (the Home Assistant Bluetooth integration, a BLE reader) keeps discovery
        running, BlueZ neither restarts the scan for us nor resets the device's RSSI, so a unit it
        already has an object for — bonded before, or seen by that client — with a steady signal
        (BlueZ only signals an RSSI change of >= 8 dBm) and static advertising data raises NO new
        callback: the wizard scanned until its timeout (found by the real-BlueZ CI rig). A
        ``Device1`` on our adapter with our name AND an ``RSSI`` (BlueZ drops RSSI when discovery
        stops, so its presence means "heard in the current discovery") is adopted directly.
        Best-effort: any D-Bus or bleak failure (including building the ``BLEDevice``, whose
        constructor differs across bleak versions) is logged and leaves discovery to the normal
        callback.

        Such an RSSI can be stale — an asleep unit keeps the RSSI it had while another client's
        discovery is still running — so adoption only means "try to connect"; the bond probe in
        :meth:`connect` never treats that unit's silence as a stale bond.
        """
        try:
            om = await self._get_interface("/", "org.freedesktop.DBus.ObjectManager")
            objects = await om.call_get_managed_objects()
            if gen is not None and self._abandoned(gen):
                return
            prefix = self._adapter_path + "/"
            found = None
            for path, ifaces in objects.items():
                dev = ifaces.get("org.bluez.Device1")
                if not dev or not path.startswith(prefix):
                    continue
                props = {k: getattr(v, "value", v) for k, v in dev.items()}
                if props.get("Name") == self._device_name and "RSSI" in props:
                    found = (path, props)
                    break
            if found is None or self._found_device is not None:   # the scan callback got there first
                return
            path, props = found
            device = _make_bledevice(props.get("Address"), props.get("Name"),
                                     {"path": path, "props": props}, props.get("RSSI"))
        except Exception as e:
            log.warning("pairing: known-device lookup failed: %r" % e)
            return
        self._set_found(device)
        if self._adopt_task is asyncio.current_task():
            # the flow (connect -> pair -> ...) now runs inside this task: detach it so a later
            # stop_scan()/aclose() can't cancel the flow it is part of
            self._adopt_task = None
        await self._emit(EV_DEVICE_FOUND)

    async def stop_scan(self):
        self._cancel_adopt()
        if self._scanner is not None:
            await self._scanner.stop()
            self._scanner = None
        self.radio_busy = await self._adapter_discovering()
        if self.radio_busy:
            log.warning(
                "pairing: %s still discovering after our scan stopped — another Bluetooth client "
                "(e.g. the Home Assistant Bluetooth integration or a BLE reader) is scanning; a new "
                "LE connection will likely fail (HCI 0x3e)" % self.adapter)

    async def _adapter_discovering(self) -> bool:
        """``Adapter1.Discovering`` on our adapter, or False when it can't be read."""
        try:
            props = await self._get_interface(self._adapter_path, "org.freedesktop.DBus.Properties")
            return bool((await props.call_get("org.bluez.Adapter1", "Discovering")).value)
        except Exception:
            return False

    # --- connect -------------------------------------------------------------
    def _abandoned(self, gen) -> bool:
        """True once the flow that started an operation has moved on: :meth:`start_scan` (a new
        attempt) or :meth:`aclose` (flow end) bumped the generation while that operation awaited.
        The runner's state timer can fire mid-``connect()``/``pair()``; a late result must then
        neither emit an event nor keep a link (the unit has ONE connection slot, and ``serve``'s
        poll resumes as soon as the wizard ends)."""
        return gen != self._gen

    async def connect(self):
        """Connect to the discovered unit within the SM's CONNECTING budget.

        When BlueZ already holds a bond, a bounded probe (connect + the auth-gated ``AUTH_CHAR``
        read) tells a valid bond from a stale one: a valid bond is KEPT (its link becomes
        :attr:`_client` and :meth:`pair` short-circuits to ``EV_PAIR_OK``) — starting the wizard
        must never destroy a working bond. The bond is dropped (and the unit re-discovered) ONLY
        when the probe proves the key stale (:class:`StaleBond`): an auth-class failure
        (:func:`is_auth_failure` — "PIN or Key Missing", HCI 0x05/0x06, ATT 0x05/0x0F, a
        ``NotPermitted`` read) or a probe that timed out after ``Device1.Connected`` was seen True
        (the link came up but encryption never completed: a unit that forgot us rejects the key
        and BlueZ reconnects forever). Any other probe failure — HCI 0x3e on a busy radio, an
        asleep unit adopted via a stale RSSI, no probe budget left — raises (-> ``EV_CONNECT_FAIL``)
        with the bond and the address cache kept. Every step shares one deadline,
        ``TIMEOUT_S[CONNECTING]`` minus :data:`CONNECT_MARGIN_S`, so ``connect()`` finishes (or
        raises) before the SM's own timer; an abandoned attempt releases any link it made.
        """
        gen = self._gen
        self._bond_valid = False
        loop = asyncio.get_running_loop()
        deadline = loop.time() + pairing.TIMEOUT_S[pairing.CONNECTING] - CONNECT_MARGIN_S

        def left():
            return deadline - loop.time()

        if await self._device_bonded():
            if self._abandoned(gen):
                return
            try:
                probe = await self._probe_bond(min(BOND_PROBE_S, left() - MIN_CONNECT_S))
            except StaleBond as e:
                if self._abandoned(gen):
                    return
                log.warning("pairing: the bond BlueZ holds for %s no longer works (%s; the unit "
                            "forgot it, e.g. a Bluetooth reset) — removing it and pairing afresh"
                            % (self._address, e))
                await self._drop_bond_and_rediscover(min(REDISCOVER_S, left() - MIN_CONNECT_S))
                if self._abandoned(gen):
                    return
            else:
                if self._abandoned(gen):
                    await self._quiet_disconnect(probe)
                    return
                log.info("pairing: the existing bond with %s is valid — keeping it" % self._address)
                self._client = probe
                self._bond_valid = True
                await self._emit(EV_CONNECTED)
                return
        from bleak import BleakClient

        budget = left()
        if budget <= 0:
            raise TimeoutError("no time left in the CONNECTING budget")
        client = BleakClient(self._found_device, adapter=self.adapter, timeout=budget)
        try:
            await asyncio.wait_for(client.connect(), budget)
        except BaseException:
            await self._quiet_disconnect(client)
            raise
        if self._abandoned(gen):
            await self._quiet_disconnect(client)
            return
        self._client = client
        await self._emit(EV_CONNECTED)

    async def _probe_bond(self, timeout: float):
        """Connect and read the auth-gated ``AUTH_CHAR`` (1004) within ``timeout`` s.

        :param timeout: the probe's budget; ``<= 0`` raises ``TimeoutError`` (-> ``EV_CONNECT_FAIL``)
            — a probe that never ran proves nothing, so it must never lead to dropping the bond.
        :returns: the connected client when the bond works.
        :raises StaleBond: the key is provably stale — an auth-class failure
            (:func:`is_auth_failure`), or a timeout after ``Device1.Connected`` was seen True
            (the link came up but the auth-gated read never completed).
        :raises Exception: any other failure (HCI 0x3e, the unit asleep or out of range, a plain
            connect timeout) propagates unchanged: the bond is presumed good and kept.
        The client is released before either exception propagates.
        """
        from bleak import BleakClient

        from calictl import device as device_mod

        if timeout <= 0:
            raise TimeoutError("no time left in the CONNECTING budget to probe the existing bond")
        client = BleakClient(self._found_device, adapter=self.adapter, timeout=timeout)
        watch = await self._watch_link()

        async def _probe():
            await client.connect()
            await client.read_gatt_char(device_mod.AUTH_CHAR)

        try:
            await asyncio.wait_for(_probe(), timeout)
            return client
        except Exception as e:
            await watch.sample()        # read Connected once more, just before giving up
            link_up = watch.seen_up or bool(getattr(client, "is_connected", False))
            await self._quiet_disconnect(client)
            if is_auth_failure(e) or watch.auth_reason:
                raise StaleBond("auth failure: %r" % (watch.auth_reason or e)) from e
            if isinstance(e, (asyncio.TimeoutError, TimeoutError)) and link_up:
                raise StaleBond("the link came up but the auth-gated read timed out") from e
            log.info("pairing: bond probe failed without proving the bond stale (kept): %r" % e)
            raise
        finally:
            watch.close()

    async def _watch_link(self):
        """A started :class:`_LinkWatch` on the discovered device (a no-op watch without a bus)."""
        try:
            path = self._device_path()
        except Exception:
            return _LinkWatch()
        return await _LinkWatch().start(self, path)

    @staticmethod
    async def _quiet_disconnect(client, timeout: float = CONNECT_MARGIN_S):
        """Best-effort release of a (possibly half-open) client, bounded so connect() still ends
        inside its deadline: the final connect's cleanup is what CONNECT_MARGIN_S is for."""
        if client is None:
            return
        try:
            await asyncio.wait_for(client.disconnect(), timeout)
        except BaseException:
            pass

    async def _device_bonded(self) -> bool:
        """Whether BlueZ holds a bond for the discovered device (``Device1.Bonded``, falling back
        to ``Paired`` on a BlueZ without ``Bonded``); False when it can't be read."""
        try:
            props = await self._get_interface(self._device_path(), "org.freedesktop.DBus.Properties")
        except Exception:
            return False
        for name in ("Bonded", "Paired"):
            try:
                return bool((await props.call_get("org.bluez.Device1", name)).value)
            except Exception:
                continue
        return False

    async def _rediscover(self, timeout: float):
        """Scan once more for the unit after its BlueZ device object was removed (``RemoveDevice``
        drops the object and its IRK, so the unit reappears as a NEW device under its current
        resolvable address). Raises when it doesn't reappear within ``timeout`` s — the caller's
        failure event lets the SM retry from a fresh scan."""
        from bleak import BleakScanner

        if timeout <= 0:
            raise TimeoutError("no time left to re-discover %s" % self._device_name)
        device = await BleakScanner.find_device_by_filter(
            lambda d, ad: d.name == self._device_name, timeout=timeout, adapter=self.adapter)
        if device is None:
            raise RuntimeError("%s did not reappear after removing its stale bond" % self._device_name)
        self._set_found(device)

    async def _drop_bond_and_rediscover(self, timeout: float):
        # In-flow stale-bond drop: the unit's identity doesn't change, and persist_bond() rewrites
        # the cache on success — so keep pairing.json (a failed re-pair must not strand the daemon
        # without its address). Only the explicit reset (ACT_REMOVE_BOND) clears the cache.
        await self.remove_bond(clear_cache=False)   # clears _found_device/_address/_client
        await self._rediscover(timeout)

    def _set_found(self, device):
        self._found_device = device
        self._address = device.address
        details = getattr(device, "details", None)
        self._path = details.get("path") if isinstance(details, dict) else None

    def _device_path(self):
        """BlueZ's D-Bus object path for the discovered device.

        Prefer the path bleak reported (``BLEDevice.details["path"]``): BlueZ names the object
        after the address it was FIRST seen under, and never renames it. A unit that advertises
        from a resolvable private address gets ``dev_<RPA>``; once bonded, BlueZ resolves later
        adverts to its identity, so the scan reports the identity address while the object stays
        at ``dev_<RPA>`` — a path rebuilt from the address names an object that doesn't exist
        (found by the real-BlueZ CI rig: the stale-bond check silently read "not bonded").
        """
        if self._path:
            return self._path
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
        """Pair the discovered device, self-healing a stale local bond.

        .. req:: Pairing self-heals a stale local bond
           :id: R_PAIRING_STALE_BOND_RECOVERY

           BlueZ refuses ``Device1.Pair()`` with ``org.bluez.Error.AlreadyExists`` when a bond
           for the device already exists. After a unit-side Bluetooth reset the peer forgets us
           but OUR bond persists, so the wizard would fail, retry ``MAX_ATTEMPTS`` times, and end
           on a generic ``pairing_failed`` the user cannot act on. On that one error, clear the
           stale bond and retry ``Pair()`` exactly once; any other error is a genuine failure and
           propagates unchanged. Before connecting, a bond BlueZ still holds is PROBED (bounded
           connect + auth-gated read, :meth:`connect`): a working bond is kept and ``pair()``
           reports success without pairing again — starting the wizard never destroys a working
           bond. Only a probe that PROVES the key stale (auth-class failure, or the link came up
           but the read timed out — with a stale LTK the link never becomes usable, so ``Pair()``
           would never be reached) drops it; an unreachable/asleep unit or a busy radio is a
           connect failure that keeps the bond. Removing a bond removes BlueZ's device object, so
           the unit is re-discovered (under its current address) before pairing again; the
           address cache is kept (the identity is unchanged).
        """
        gen = self._gen
        if self._bond_valid:
            # connect() proved the existing bond works (an auth-gated read succeeded over it):
            # keep it — there is nothing to pair, and VERIFY confirms the link next.
            await self._emit(EV_PAIR_OK)
            return
        await self._ensure_agent()
        device_iface = await self._get_interface(self._device_path(), "org.bluez.Device1")
        if self._abandoned(gen):
            return
        try:
            await device_iface.call_pair()
        except Exception as e:
            # BlueZ refuses Pair() outright when a bond already exists (org.bluez.Error.AlreadyExists),
            # so a STALE bond makes the wizard unusable: it fails, retries MAX_ATTEMPTS times and ends
            # on a generic "pairing_failed" with nothing the user can act on. Hit for real on
            # 2026-09-18 when re-pairing at the van. Clear that bond and pair once more — the user
            # explicitly asked to pair, and a bond that blocks pairing is worthless by definition.
            # Only this error is recovered: anything else is a genuine pairing failure and propagates.
            if "AlreadyExists" not in str(e) and "already exists" not in str(e).lower():
                raise
            log.warning("pair: bond already exists — removing the stale bond and retrying once")
            # remove_bond() clears _address and BlueZ drops the device object with the bond, so the
            # retry needs the unit re-discovered — the old path no longer exists.
            # Bounded like connect(): re-discovery must finish inside the SM's PAIRING budget.
            await self._drop_bond_and_rediscover(
                min(REDISCOVER_S, pairing.TIMEOUT_S[pairing.PAIRING] - CONNECT_MARGIN_S))
            if self._abandoned(gen):
                return
            device_iface = await self._get_interface(self._device_path(), "org.bluez.Device1")
            if self._abandoned(gen):
                return
            await device_iface.call_pair()
        if self._abandoned(gen):
            return
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

                client = BleakClient(self._found_device, adapter=self.adapter)
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
            cache_path.chmod(0o600)  # the identity address is owner PII (matches install.sh)
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

    async def remove_bond(self, clear_cache: bool = True):
        """Remove BlueZ's bond (``Adapter1.RemoveDevice``) and forget the discovered device.

        :param clear_cache: also delete the persisted address cache (``pairing.json``). True for
            the explicit reset/unpair (``ACT_REMOVE_BOND``, the runner's call); the in-flow
            stale-bond drop passes False — the identity is unchanged and ``persist_bond()``
            rewrites the cache once the re-pair succeeds.
        """
        try:
            adapter = await self._get_interface(self._adapter_path, "org.bluez.Adapter1")
            await adapter.call_remove_device(self._device_path())
        except Exception:
            pass
        finally:
            self._found_device = None
            self._address = None
            self._path = None
            self._client = None
            self._bond_valid = False
        # Clear the persisted address too, else resolve_addr() re-reads it after a reboot and the
        # daemon re-targets the bond we just removed. Independent of the bluez call above (which
        # no-ops off-hardware), and best-effort: a cache-clear failure must not surface as a
        # pairing error.
        if not clear_cache:
            return
        try:
            pairing_cache_path().unlink(missing_ok=True)
        except OSError:
            pass

    async def aclose(self):
        """Flow end: release the wizard's own link, unregister the D-Bus agent, drop the bus.

        The link :meth:`connect`/:meth:`verify` kept open is disconnected here (bounded by
        :data:`CLOSE_DISCONNECT_S`, best-effort): the unit has ONE connection slot, and ``serve``'s
        poll must be able to read right after the wizard reaches ``BONDED``. A still-looking
        known-device lookup is cancelled.
        """
        self._gen += 1             # the flow ended: an in-flight connect()/pair() must not revive it
        self._cancel_adopt()
        client, self._client = self._client, None
        await self._quiet_disconnect(client, CLOSE_DISCONNECT_S)
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
