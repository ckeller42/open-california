"""A stateful, quirk-faithful mock of the VW California Camper Unit's BLE surface.

This lets calictl's *real* control paths (`device.actuate`, `cli.cmd_set`,
`serve.on_command`) run end-to-end with **no van, no phone, no BLE adapter** — the
mock stands in for the unit's GATT server. It is the fixture behind the offline
integration tests and the `run_against_mock` harness.

Design: two layers.
  * ``MockCamperUnit`` — the "firmware": holds per-function decoded state, models the
    behaviours we have actually reverse-engineered, and reports reads back.
  * ``MockBleakClient`` — a drop-in for ``bleak.BleakClient`` exposing only the surface
    ``calictl/device.py`` uses (connect / disconnect / services / read_gatt_char /
    write_gatt_char / start_notify), delegating to one shared ``MockCamperUnit``.

Fidelity — the mock encodes only what is *known*, and stays honest about what is not:
  * **1003 arm-gate (issue #2, SOLVED):** control writes are honoured only while the
    liveness heartbeat on char 1003 is ticking. Without it, a write is ACKed and
    ignored — exactly the "writes ACK but do nothing" symptom.
  * **Range validation → link drop:** an out-of-range field value raises
    ``MockDisconnect`` (the unit drops the ATT link with 0x0E; e.g. cooler State=3).
    This runs regardless of arming (the firmware's parse layer always validates).
  * **Lighting per-zone SET (cracked 2026-07-08):** SET_PROFILE (Mode 16) activates a profile;
    SET_BRIGHTNESS (Mode 4) applies the changed zone (honouring the ``14`` per-zone
    leave-unchanged sentinel; 0 = set-to-0) but ONLY while a profile is active — the
    live-verified precondition. All from an HCI capture of the app.
  * It does NOT fake what we haven't decoded: the control→state timer offset remap and
    the roof/heater enum semantics are deliberately not modelled (untouched by writes).

Because a mock can only encode already-decoded behaviour, it is a regression harness +
executable documentation — NOT an oracle. New protocol truth comes from Phase 2's
capture of the real app (`tools/capture_diff.py`).

Imports only stdlib + ``calictl.{protocol,control,overrides}`` (no bleak, no PyYAML), so
it stays importable in the bleak-less test environment (a project hard rule).
"""
from __future__ import annotations

from calictl import protocol, control, overrides, device

# short UUIDs the mock advertises / recognises, beyond the per-function chars.
_VERSION_SHORT = "1001"
_AUTH_SHORT = "1004"
_HEARTBEAT_SHORT = "1003"

LEAVE_UNCHANGED_2BIT = 3   # the sg.a 2-bit "leave unchanged" sentinel (see control.SENTINEL)
LIGHT_ZONE_UNCHANGED = 14  # lighting per-zone 4-bit "leave unchanged" sentinel (HCI capture 2026-07-08)
LIGHT_MODE_SET_BRIGHTNESS = 4
LIGHT_MODE_SET_PROFILE = 16
LIGHT_MODE_REQUEST_CONFIG = 12   # the session-arm preamble (the 2026-08-16 physical-apply crack)
LIGHT_MODE_COMMIT = 0        # the 0e00… commit/apply frame (HCI-verified 2026-07-13): a
                             # SET_BRIGHTNESS/SET_PROFILE is only APPLIED once this lands

# Per-function initial decoded state. Installed flags on so semantics reports the
# function as present; loads start OFF. Functions absent here start all-zero.
DEFAULT_SEED = {
    "cooler": {"Installed": 1, "State": 0, "Mode": 4, "Level": 3},
    "campingmode": {"Installed": 1, "State": 0, "UsbCharger": 0,
                    "InteriorLight": 0, "OutsideLight": 0},
    "airheater": {"Installed": 1, "NormalOperation": 0, "PermanentOperation": 0,
                  "HeatingLevel": 0},
    # ProfileNumber 0 = NO active profile: SET_BRIGHTNESS is ACKed but ignored until a profile
    # is activated (SET_PROFILE) — the live-verified precondition. L10-16 hold the constant
    # not-installed default (13) that the real van reports (so any_on must ignore them).
    "lighting": {"ProfileNumber": 0, "Mode": 0, "LightValue": 0,
                 "BrightnessLOneZero": 13, "BrightnessLOneOne": 13, "BrightnessLOneThree": 13,
                 "BrightnessLOneFour": 13, "BrightnessLOneFive": 13, "BrightnessLOneSix": 13},
    # Water uses the van's ABSOLUTE encoding (Unit=1: Level=litres, Volume=capacity), matching
    # a live read: fresh 11/29 L (38%), waste 0/22 L. (The van's fresh sensor is coarse, so a low
    # reading like 1 L can appear without the grey tank rising — modelled as a normal healthy 11 L.)
    "water": {"Installed": 1, "FreshWaterUnit": 1, "FreshWaterLevel": 11, "FreshWaterVolume": 29,
              "WasteWaterUnit": 1, "WasteWaterLevel": 0, "WasteWaterVolume": 22},
    # Engine-OFF energy state (mirrors the real van, live-verified): the STARTER battery is
    # only measured with terminal-15, so its current reads the 0x81 sentinel and Age=255 (stale)
    # -> batt1_v is None (the UI must render "—", never "null V"). Leisure battery is always live
    # (100% / 14.1 V, 58 h remaining). Seeding this makes the null-readout path reachable in tests.
    # DC-DC + shore are fitted but idle (-> "inactive (0 W)"); solar is NOT fitted (-> "not
    # installed") — the real equipment profile of this van.
    "energy": {"SocOneBattAfs": 5, "SocTwoBattAfs": 10, "UTwoBattBemAfs": 141,
               "ITwoBattBemAfs": 0, "UOneBattBemAfs": 48, "IOneBattBemAfs": 0x81,
               "AgeOneBattValuesMinutes": 255, "tTwoBattRemainingh": 58,
               "DcdcInstalled": 1, "LadInstalled": 1, "PvInstalled": 0,
               "StateDcdcAfs": 0, "StateLandAfs": 0},
    # Parked, engine-OFF (COHERENT with the energy seed above: terminal-15 off => starter
    # battery unmeasured). Valid RTC (runs regardless of ignition) + a small tilt (roll -1.09°,
    # pitch 0.35°; raw CarLevelRoll -109 = 0.01°-units, ÷100 in semantics).
    "vehicle": {"TerminalOneFive": 0, "CarVariant": 4,
                "CarTimeYear": 126, "CarTimeMonth": 6, "CarTimeDay": 8,
                "CarTimeHour": 19, "CarTimeMinute": 30, "CarTimeSecond": 0,
                "CarLevelRoll": -109, "CarLevelPitch": 35},
}


class MockDisconnect(RuntimeError):
    """The mock firmware dropped the link (an out-of-range write → 0x0E)."""


def _pack_state(func, values: dict) -> bytes:
    """Pack a decoded-state dict back into the function's state-frame bytes using the
    same MSB-first bit codec as ``protocol`` (mirror of ``protocol.decode``). Fields
    absent from ``values`` pack as 0; the frame spans every placed state field."""
    placed = [f for f in func.state_fields if f.placed]
    total = max((f.offset + f.width for f in placed), default=8)
    bits = [0] * total
    for f in placed:
        if f.name in values and values[f.name] is not None:
            v = int(values[f.name]) & ((1 << f.width) - 1)
            bits[f.offset:f.offset + f.width] = protocol._bits_of(v, f.width)
    return protocol.pack(bits)


class MockCamperUnit:
    """The mock "firmware": decoded per-function state + the known write behaviours."""

    def __init__(self, seed: dict | None = None):
        self.funcs = protocol.load()
        overrides.apply(self.funcs)
        base = {fn: dict(vals) for fn, vals in DEFAULT_SEED.items()}
        for fn, vals in (seed or {}).items():
            base.setdefault(fn, {}).update(vals)
        self.state: dict[str, dict] = base
        self.armed = False
        self.online = True     # False models the parked unit deep-asleep (not advertising)
        self.last_beat: int | None = None
        self._pending_light = None                   # staged lighting change, applied on commit
        self.writes: list[tuple[str, bytes]] = []   # (function, frame) audit trail
        # Per-function STALE-read overrides: a read returns these until the 1003 heartbeat arms
        # the session (self.armed), after which the true `state` is returned — models the real
        # unit latching an old value until the liveness heartbeat drives its measurement loop
        # (observed: fresh-water 1 L latched vs the true 11 L once the app's heartbeat runs).
        self.read_latch: dict[str, dict] = {}
        # Per-function NOTIFICATION push values: what the unit pushes on the state char (vs the
        # bare-read latch). Models push-only-for-freshness chars like water (1302), where a bare
        # read returns the stale latch and the true value arrives only as a notification.
        self.notify_push: dict[str, dict] = {}
        # reverse maps: char UUID -> function, for read/write routing
        self._state_char = {f.state_char: fn for fn, f in self.funcs.items() if f.state_char}
        self._control_char = {f.control_char: fn for fn, f in self.funcs.items() if f.control_char}

    # --- reads -------------------------------------------------------------
    def read(self, uuid: str) -> bytes:
        # A real state characteristic wins over the auth/version stub: `vehicle` reads char
        # 1004 and `general` reads 1001 — the SAME UUIDs device.py reads for the connect
        # handshake — so those must return the packed state, not the placeholder.
        fn = self._state_char.get(uuid)
        if fn is not None:
            vals = dict(self.state.get(fn, {}))
            # Freshness gate (live-verified): a state char decays to a STALE latched value unless
            # the 1003 liveness heartbeat is running — that drives the unit's measurement loop.
            # So a read returns the latch until a heartbeat has armed the session, then the truth.
            if fn in self.read_latch and not self.armed:
                vals.update(self.read_latch[fn])
            return _pack_state(self.funcs[fn], vals)
        if uuid in (device.VERSION_CHAR, device.AUTH_CHAR):
            return b"\x04\x10\x02\x07"           # non-empty payload for the version/auth gate
        return bytes(6)                          # unknown readable char: benign payload

    def decoded(self, function: str) -> dict:
        """Current decoded state as calictl would read it (for test assertions)."""
        return protocol.decode(self.funcs[function], self.read(self.funcs[function].state_char))

    # --- heartbeat ---------------------------------------------------------
    def beat(self, data: bytes) -> None:
        """A write to char 1003 — the liveness counter. Arms actuation."""
        ctr = int.from_bytes(bytes(data), "big")
        self.last_beat = ctr
        self.armed = True

    def drop(self) -> None:
        """Van parks -> deep sleep: the link dies and the unit stops advertising."""
        self.online = False
        self.armed = False
        self._light_cfg_armed = False   # the lighting arm preamble is session-scoped

    def wake(self) -> None:
        """Physical use (door/ignition) wakes the unit; it advertises again."""
        self.online = True

    # --- control writes ----------------------------------------------------
    def write(self, uuid: str, data: bytes) -> None:
        if uuid == device.HEARTBEAT_CHAR:
            return self.beat(data)
        fn = self._control_char.get(uuid)
        if fn is None:
            return                                # write to a non-control char: ignore
        func = self.funcs[fn]
        frame = bytes(data)
        self.writes.append((fn, frame))
        ctrl = control.decode_control(func, frame)

        # 1) firmware parse/validate layer — ALWAYS runs, armed or not. An out-of-range
        #    value is rejected and the unit drops the ATT link (observed 0x0E).
        for cf in func.control_fields:
            if cf.placed and cf.name in ctrl:
                try:
                    protocol.check_value(func, cf.name, cf.width, ctrl[cf.name], cf.valid)
                except ValueError as e:
                    raise MockDisconnect("out-of-range write to %s: %s" % (fn, e))

        # 2) apply layer — gated on the 1003 heartbeat (issue #2). Without a live
        #    heartbeat the write is ACKed and ignored.
        if not self.armed:
            return
        st = self.state.setdefault(fn, {})

        # Lighting is COMMIT-GATED (HCI-verified 2026-07-13). A SET_PROFILE (Mode 16) or
        # SET_BRIGHTNESS (Mode 4) only STAGES the change; the unit APPLIES it when the commit
        # frame (Mode 0, control.LIGHT_COMMIT) lands right after. With no commit, the write is
        # ACKed but never applied (the old gap). The apply gate for SET_BRIGHTNESS is the FRAME's
        # ProfileNumber (the app hardcodes 9), NOT a separately-active profile: a brightness frame
        # carrying PN=0 is ignored, PN=9 applies (and makes profile 9 the active one).
        # Physical-apply gate (the 2026-08-16 crack): the real unit only DRIVES the lamps in a
        # session where a REQUEST_CONFIG (Mode 12) preamble has landed. Without it, SETs are
        # ACKed + echoed but nothing applies — so a call site that forgets pre=preamble_for(fn)
        # fails these tests instead of silently passing.
        if fn == "lighting":
            mode = ctrl.get("Mode")
            if mode == LIGHT_MODE_REQUEST_CONFIG:
                self._light_cfg_armed = True
                return
            if mode in (LIGHT_MODE_SET_BRIGHTNESS, LIGHT_MODE_SET_PROFILE) \
                    and not getattr(self, "_light_cfg_armed", False):
                self._pending_light = None
                return                            # un-armed session: ACKed, never applied
            if mode == LIGHT_MODE_SET_PROFILE:
                self._pending_light = ("profile", ctrl.get("ProfileNumber", st.get("ProfileNumber", 0)))
                return
            if mode == LIGHT_MODE_SET_BRIGHTNESS:
                if not ctrl.get("ProfileNumber"):   # frame carries no working profile (PN=0) -> ignored
                    self._pending_light = None; return
                zones = {cf.name: ctrl[cf.name] for cf in func.control_fields
                         if cf.placed and cf.name.startswith("BrightnessL") and cf.name in ctrl
                         and ctrl[cf.name] != LIGHT_ZONE_UNCHANGED and func.state_field(cf.name)}
                self._pending_light = ("zones", zones, ctrl.get("ProfileNumber"))
                return
            if mode == LIGHT_MODE_COMMIT:         # apply the staged change
                p = getattr(self, "_pending_light", None)
                if p and p[0] == "profile":
                    st["ProfileNumber"] = p[1]
                elif p and p[0] == "zones":
                    st.update(p[1])
                    st["ProfileNumber"] = p[2]    # a brightness set makes its profile the active one
                self._pending_light = None
            return

        for cf in func.control_fields:
            if not (cf.placed and cf.name in ctrl):
                continue
            if cf.width == 2 and ctrl[cf.name] == LEAVE_UNCHANGED_2BIT:
                continue                          # full-packet "leave unchanged" 2-bit sentinel
            if func.state_field(cf.name) is None:
                continue                          # not a name-aligned control→state field
                                                  # (offset-remapped timers are NOT faked)
            st[cf.name] = ctrl[cf.name]


class _Char:
    def __init__(self, uuid: str, properties: list[str]):
        self.uuid, self.properties = uuid, properties


class _Service:
    def __init__(self, characteristics: list[_Char]):
        self.characteristics = characteristics


class MockBleakClient:
    """Drop-in for ``bleak.BleakClient`` over a shared ``MockCamperUnit``.

    Bind a unit and hand this class (not an instance) to the fake ``bleak`` module —
    ``device.py`` constructs it as ``BleakClient(addr, timeout=...)``. All instances
    share the one unit so state persists across calictl's connect-per-operation calls.
    """
    unit: MockCamperUnit | None = None            # set by the harness / fixture

    def __init__(self, addr, timeout=None):
        self.addr = addr
        self.is_connected = False

    @classmethod
    def bind(cls, unit: MockCamperUnit) -> type["MockBleakClient"]:
        cls.unit = unit
        return cls

    async def connect(self):
        if self.unit is not None and not self.unit.online:
            raise MockDisconnect("van asleep (not advertising)")
        self.is_connected = True

    async def disconnect(self):
        self.is_connected = False

    @property
    def services(self):
        unit = self.unit
        chars = [_Char(device.VERSION_CHAR, ["read"]),
                 _Char(device.AUTH_CHAR, ["read"]),
                 _Char(device.HEARTBEAT_CHAR, ["write"])]
        for fn, f in unit.funcs.items():
            if f.state_char:
                chars.append(_Char(f.state_char, ["read", "notify"]))
            if f.control_char:
                chars.append(_Char(f.control_char, ["write"]))
        return [_Service(chars)]

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
            self.is_connected = False             # mirror the unit dropping the link
            raise

    async def start_notify(self, uuid, cb):
        # If the unit has a pending push for this char's function, deliver it immediately (models
        # the real unit pushing the fresh value on/after subscribe — e.g. water 1302).
        fn = self.unit._state_char.get(str(uuid))
        if fn is not None and fn in self.unit.notify_push:
            frame = _pack_state(self.unit.funcs[fn],
                                {**self.unit.state.get(fn, {}), **self.unit.notify_push[fn]})
            cb(_Char(str(uuid), ["notify"]), frame)
        return None

    async def stop_notify(self, uuid):
        return None
