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
    # fresh 12/29 L (~41%), waste 3/22 L — so the GUI water readout has real levels
    "water": {"Installed": 1, "FreshWaterUnit": 0, "FreshWaterLevel": 12, "FreshWaterVolume": 29,
              "WasteWaterUnit": 0, "WasteWaterLevel": 3, "WasteWaterVolume": 22},
    # ignition on, a valid RTC, and a small tilt (roll -1.09°, pitch 0.35°) so the vehicle
    # screen shows real data in dev (raw CarLevelRoll -109 = 0.01°-units, ÷100 in semantics).
    "vehicle": {"TerminalOneFive": 1, "CarVariant": 4,
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
        self.last_beat: int | None = None
        self.writes: list[tuple[str, bytes]] = []   # (function, frame) audit trail
        # reverse maps: char UUID -> function, for read/write routing
        self._state_char = {f.state_char: fn for fn, f in self.funcs.items() if f.state_char}
        self._control_char = {f.control_char: fn for fn, f in self.funcs.items() if f.control_char}

    # --- reads -------------------------------------------------------------
    def read(self, uuid: str) -> bytes:
        if uuid in (device.VERSION_CHAR, device.AUTH_CHAR):
            return b"\x04\x10\x02\x07"           # any non-empty payload (version / auth gate)
        fn = self._state_char.get(uuid)
        if fn is None:
            return bytes(6)                      # unknown readable char: benign payload
        return _pack_state(self.funcs[fn], self.state.get(fn, {}))

    def decoded(self, function: str) -> dict:
        """Current decoded state as calictl would read it (for test assertions)."""
        return protocol.decode(self.funcs[function], self.read(self.funcs[function].state_char))

    # --- heartbeat ---------------------------------------------------------
    def beat(self, data: bytes) -> None:
        """A write to char 1003 — the liveness counter. Arms actuation."""
        ctr = int.from_bytes(bytes(data), "big")
        self.last_beat = ctr
        self.armed = True

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

        # Lighting has its own live-verified precondition: SET_PROFILE (Mode 16) activates a
        # profile; SET_BRIGHTNESS (Mode 4) applies zones ONLY while a profile is active
        # (ProfileNumber != 0) — with none active the unit ACKs but ignores it.
        if fn == "lighting":
            mode = ctrl.get("Mode")
            if mode == LIGHT_MODE_SET_PROFILE:
                st["ProfileNumber"] = ctrl.get("ProfileNumber", st.get("ProfileNumber", 0))
                return
            if not st.get("ProfileNumber"):       # 0/None -> no active profile -> ignored
                return
            for cf in func.control_fields:
                if cf.placed and cf.name.startswith("BrightnessL") and cf.name in ctrl \
                        and ctrl[cf.name] != LIGHT_ZONE_UNCHANGED and func.state_field(cf.name):
                    st[cf.name] = ctrl[cf.name]
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
        return self.unit.read(str(uuid))

    async def write_gatt_char(self, uuid, data, response=None):
        try:
            self.unit.write(str(uuid), data)
        except MockDisconnect:
            self.is_connected = False             # mirror the unit dropping the link
            raise

    async def start_notify(self, uuid, cb):
        return None
