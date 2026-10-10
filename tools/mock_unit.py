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
    ignored — exactly the "writes ACK but do nothing" symptom. EXCEPT lighting: the real
    unit actuates lighting on an awake unit with a bare SET_BRIGHTNESS + commit and no
    heartbeat (photon-verified 2026-08-16, control-and-actuation.md), so lighting skips it.
  * **Range validation → link drop:** an out-of-range field value raises
    ``MockDisconnect`` (the unit drops the ATT link with 0x0E).
    This runs regardless of arming (the firmware's parse layer always validates).
  * **Lighting per-zone SET (cracked 2026-07-08):** SET_PROFILE (Mode 16) and SET_BRIGHTNESS
    (Mode 4, honouring the ``14`` per-zone leave-unchanged sentinel; 0 = set-to-0) only STAGE a
    change; the ``0e00…`` commit frame (Mode 0) applies it. A brightness frame applies only when
    it carries a non-zero ProfileNumber (the app hardcodes 9) — the frame's own PN, not a
    separately-active profile. The state char is a write-through ECHO; the physical lamps
    (``light_actual``) ramp on ``tick()`` and push 1502 Mode-4 frames, and ``light_applies=False``
    models "ACKed + echoed, lamps dark". Zone 9 (pop-top reading light) is refused while the roof
    is closed. NOT gated on the 1003 heartbeat (see the arm-gate above): the only wake gate is
    that the mock is connectable at all (``drop()`` = deep sleep refuses the link).
  * **Lighting configuration:** favourites 1-7 (save = store without a live change, activate =
    apply + 1502 ack, empty = ACK-and-ignore), the wake-up config (Mode 20) and the door-contact
    flag (Mode 16 / PN 8) are stored, and each write is ECHOED as a one-off 1502 frame (never
    re-readable state); SET_COLOR is acked (Mode 6 / PN N). REQUEST_CONFIG (Mode 12) answers with
    the favourite bits in LightValue, then re-reports the stored wake-up and door frames.
  * **Roof (1401/1402):** the app-style SafetyCounter stream is modelled — validity needs a
    monotonic, still-advancing counter (two increments), a restart drops it, and a counter that
    just validated withholds the motor ``ROOF_WITHHOLD_S`` (~3 s, SEMI-VERIFIED) — a counter
    already streamed by the roof page has paid it before the press. The motion and its 1402
    pushes follow the real unit (CAPTURE 2026-10-10): an open press first raises the pre-open
    checklist ``InfoPopUp`` 2 (no motion, cleared after 4 s; a fresh press within
    ``ROOF_CHECKLIST_WINDOW_S`` moves), the motor starts ``ROOF_START_S`` after a press
    (``InfoPopUp`` 12), leaves the end position after ``ROOF_LEAVE_S`` (Position 2), takes
    ``ROOF_OPEN_S`` / ``ROOF_CLOSE_S`` of held travel, reports ``InfoPopUp`` 8 at the end of
    travel and then the final Position; a release mid-travel reports ``InfoPopUp`` 3 for 4 s.
  * Also modelled: cooler/heater timers (control TimerHour/TimerMin → state ``*Set``, heater
    departure timer), per-minute countdowns, the ignition→camping shed + crank link drop, the
    15 s no-heartbeat link drop, device-confirmed ACK-and-ignore refusals, the one-slot option,
    water freeze while its system is unpowered, and change-pushes only for ``CHANGE_PUSH_FNS``.
    Not modelled: anything undecoded (e.g. roof/heater ``InfoPopUp``/``ErrorCode`` causes — they
    change only when a test or the app-lab console sets them).

Because a mock can only encode already-decoded behaviour, it is a regression harness +
executable documentation — NOT an oracle. New protocol truth comes from Phase 2's
capture of the real app (`tools/capture_diff.py`).

Imports only stdlib + ``calictl.{protocol,control,overrides,pairing}`` (no bleak, no PyYAML;
``calictl.pairing`` is itself stdlib-only), so it stays importable in the bleak-less test
environment (a project hard rule).
"""

from __future__ import annotations

import asyncio
import os

from calictl import control, device, overrides, protocol
from calictl.pairing import (
    EV_CONNECTED,
    EV_DEVICE_FOUND,
    EV_PAIR_FAIL,
    EV_PAIR_OK,
    EV_PASSKEY_REQUESTED,
)

# short UUIDs the mock advertises / recognises, beyond the per-function chars.
_VERSION_SHORT = "1001"
_AUTH_SHORT = "1004"
_HEARTBEAT_SHORT = "1003"

LEAVE_UNCHANGED_2BIT = 3  # the sg.a 2-bit "leave unchanged" sentinel (see control.SENTINEL)
ROOF_RELEASE_S = 1.5  # no roof frame for this long = the button was released (app streams @500 ms)
ROOF_COUNTER_STALE_S = 1.5
# Roof motion on the real unit — CAPTURE 2026-10-10 (real app 5.4.0 on the real unit, owner's finger;
# buspi:~/applog/att-5.txt 13:32–13:34 full open + close, att-3.txt 13:20–13:28 refused presses).
# Module constants so tests can scale them down.
ROOF_OPEN_S = 28.0  # continuous motor time closed -> open (press 13:32:10.8, 2308 at 13:32:39.5)
ROOF_CLOSE_S = 23.0  # continuous motor time open -> closed (4.2 s + 18.7 s of hold)
ROOF_START_S = 0.25  # press -> motor start, 1402 `InfoPopUp` 12 at the old Position (`030c` / `130c`)
ROOF_LEAVE_S = 0.9  # motor start -> Position 2 between (`230c`; ~1.2 s after the press)
ROOF_SETTLE_S = 1.0  # end of travel `2308` -> the final Position (`1300` open / `0300` closed)
ROOF_INFO_CLEAR_S = 4.0  # `0302` -> `0300` and `2303` -> `2300` (each exactly ~4.0 s on the wire)
# InfoPopUp 2 = the pre-open checklist: every OPEN press got `0302` (no motion) unless an earlier `0302`
# was raised within this window (26 s later: moved; 40 s later: `0302` again). Never before a close.
ROOF_CHECKLIST_WINDOW_S = 30.0
# The unit drops a link that carries no 1003 beat for this long (on-device 2026-07-09).
HEARTBEAT_TIMEOUT_S = 15.0
# The BLE link drops for ~1 min at the engine crank (observed at the van). 0 disables the model.
CRANK_DROP_S = 60.0
# The unit withholds the roof motor while it validates a fresh SafetyCounter (~3 s per the app's
# own dead-man, device.ROOF_SAFETY_VALIDATE_S). SEMI-VERIFIED: calictl has never driven the real
# motor, so this is a parameter to tune, not a measured fact. 0 disables the withhold.
ROOF_WITHHOLD_S = 3.0  # SafetyCounter not incremented for this long = no longer valid
LIGHT_ZONE_UNCHANGED = 14  # lighting per-zone 4-bit "leave unchanged" sentinel (HCI capture 2026-07-08)
LIGHT_MODE_SET_BRIGHTNESS = 4
# Functions the real unit was CONFIRMED to push on change (control-and-actuation.md: the genuine
# 1202 camping push + 1004 ignition, "confirmed on real hardware"). Everything else is subscribe-
# push-once only: the 2026-09-16 buspi trace saw no change-driven push on the other 12 chars.
CHANGE_PUSH_FNS = frozenset({"campingmode", "vehicle"})
LIGHT_MODE_SET_PROFILE = 16
LIGHT_MODE_COMMIT = 0  # the 0e00… commit/apply frame (HCI-verified 2026-07-13): a
# SET_BRIGHTNESS/SET_PROFILE is only APPLIED once this lands

# Per-function initial decoded state. Installed flags on so semantics reports the
# function as present; loads start OFF. Functions absent here start all-zero.
DEFAULT_SEED = {
    # general (char 1001): firmware/protocol identity. Realistic reference-van values so the UI
    # renders a clean "0410 · 0207 · 2 ✓ tested" (not a false untested-firmware warning). The SW
    # fields are 4 ASCII bytes packed as a 32-bit int: "0410" = 0x30343130, "0207" = 0x30323037.
    "general": {"AmbSwVersion": 0x30343130, "CmSwVersion": 0x30323037, "CommunicationVersion": 2},
    "cooler": {"Installed": 1, "State": 0, "Mode": 4, "Level": 3},
    "campingmode": {"Installed": 1, "State": 0, "UsbCharger": 0, "InteriorLight": 0, "OutsideLight": 0},
    "airheater": {"Installed": 1, "NormalOperation": 0, "PermanentOperation": 0, "HeatingLevel": 0},
    # Pop-top FITTED (the real van has one, #106), closed, no InfoPopUp alert, counter valid.
    # Seeded so the Roof tile + screen render in the e2e suite: the move buttons are the one
    # safety-sensitive control, and without a roof here CI never executed roofControls() at
    # all — which is how a ReferenceError shipped to buspi in #174. Motion is modelled coarsely
    # from the SafetyCounter stream (write()'s roof branch + tick()), not from a real motor.
    "roof": {"Installed": 1, "Position": 0, "InfoPopUp": 0, "SafetyCounterValid": 1},
    # ProfileNumber 0 = no active profile. The apply gate is the brightness FRAME's own
    # ProfileNumber (non-zero, see write()), not this seed value. L10-16 hold the constant
    # not-installed default (13) that the real van reports (so any_on must ignore them).
    "lighting": {
        "ProfileNumber": 0,
        "Mode": 0,
        "LightValue": 0,
        "BrightnessLOneZero": 13,
        "BrightnessLOneOne": 13,
        "BrightnessLOneThree": 13,
        "BrightnessLOneFour": 13,
        "BrightnessLOneFive": 13,
        "BrightnessLOneSix": 13,
    },
    # Water uses the van's ABSOLUTE encoding (Unit=1: Level=litres, Volume=capacity), matching
    # a live read: fresh 11/29 L (38%), waste 0/22 L. (The van's fresh sensor is coarse, so a low
    # reading like 1 L can appear without the grey tank rising — modelled as a normal healthy 11 L.)
    "water": {
        "Installed": 1,
        "FreshWaterUnit": 1,
        "FreshWaterLevel": 11,
        "FreshWaterVolume": 29,
        "WasteWaterUnit": 1,
        "WasteWaterLevel": 0,
        "WasteWaterVolume": 22,
    },
    # Engine-OFF energy state (mirrors the real van, live-verified): the STARTER battery is
    # only measured with terminal-15, so its current reads the 0x81 sentinel and Age=255 (stale)
    # -> batt1_v is None (the UI must render "—", never "null V"). Leisure battery is always live
    # (100% / 14.1 V, 58 h remaining). Seeding this makes the null-readout path reachable in tests.
    # DC-DC + shore are fitted but idle (-> "inactive (0 W)"); solar is NOT fitted (-> "not
    # installed") — the real equipment profile of this van.
    "energy": {
        "SocOneBattAfs": 5,
        "SocTwoBattAfs": 10,
        "UTwoBattBemAfs": 141,
        "ITwoBattBemAfs": 0,
        "UOneBattBemAfs": 48,
        "IOneBattBemAfs": 0x81,
        "AgeOneBattValuesMinutes": 255,
        "tTwoBattRemainingh": 58,
        "DcdcInstalled": 1,
        "LadInstalled": 1,
        "PvInstalled": 0,
        "StateDcdcAfs": 0,
        "StateLandAfs": 0,
    },
    # Parked, engine-OFF (COHERENT with the energy seed above: terminal-15 off => starter
    # battery unmeasured). Valid RTC (runs regardless of ignition) + a small tilt (roll -1.09°,
    # pitch 0.35°; raw CarLevelRoll -109 = 0.01°-units, ÷100 in semantics).
    "vehicle": {
        "TerminalOneFive": 0,
        "CarVariant": 4,
        "CarTimeYear": 126,
        "CarTimeMonth": 6,
        "CarTimeDay": 8,
        "CarTimeHour": 19,
        "CarTimeMinute": 30,
        "CarTimeSecond": 0,
        "CarLevelRoll": -109,
        "CarLevelPitch": 35,
    },
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
            bits[f.offset : f.offset + f.width] = protocol._bits_of(v, f.width)
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
        self.online = True  # False models the parked unit deep-asleep (not advertising)
        self.last_beat: int | None = None
        self._pending_light = None  # staged lighting change, applied on commit
        # Lighting configuration the unit STORES (dg/h l3/u0/m0/n4 — DECOMPILE + app recordings):
        # FAVORITE1-7 slots (ProfileNumber 1-7; None = empty), the wake-up config, the door flag.
        self.favourites: dict[int, dict | None] = {n: None for n in range(1, 8)}
        self.favourite_colour: dict[int, int] = {}  # SET_COLOR (Mode 6) per slot, applied at save
        self.wakeup: dict | None = None  # {"Timestamp", "LightValue"} as last written (Mode 20)
        self.door_contact = 0  # DOOR_CONTACT (PN 8) flag
        self._roof_ctr: int | None = None  # last SafetyCounter seen (roof frames)
        self._roof_streak = 0  # counter increments observed (monotonic run)
        self._roof_ctr_advanced = 0.0  # self.now when the counter last incremented
        self._roof_withhold_until = 0.0  # motor withheld until then (see ROOF_WITHHOLD_S)
        self._roof_dir: str | None = None  # "up" / "down" while a move frame is held
        self._roof_last_frame = 0.0  # self.now when the last roof frame arrived
        self._roof_press_t: float | None = None  # an honoured press (not eaten by the checklist)
        self._roof_motor: str | None = None  # "up" / "down" while the motor runs
        self._roof_run = 0.0  # seconds the motor has run in this move
        pos = (self.state.get("roof") or {}).get("Position")
        self._roof_p = 1.0 if pos == 1 else 0.5 if pos == 2 else 0.0  # travel: 0 closed .. 1 open
        self._roof_checklist_t: float | None = None  # when InfoPopUp 2 was last raised
        self._roof_timers: list[tuple[float, dict]] = []  # (due, roof fields) delayed 1402 changes
        self.roof_checklist = True  # False = no InfoPopUp 2 before an open (tests that skip it)
        self.now = 0.0  # the unit's own clock, advanced by tick()
        self._acc_minute = 0.0  # sub-minute remainder for per-minute counters
        self._last_t15: int | None = None  # ignition edge detector (tick)
        self.writes: list[tuple[str, bytes]] = []  # (function, frame) audit trail
        # Per-function STALE-read overrides: a read returns these until the 1003 heartbeat arms
        # the session (self.armed), after which the true `state` is returned — models re-read chars
        # the heartbeat refreshes. NOT water: the old "1 L latched vs 11 L once the heartbeat runs"
        # story was correlation; water's real gate is `water_powered` (value-freshness.md).
        self.read_latch: dict[str, dict] = {}
        # Per-function subscribe-time NOTIFICATION overlay: what the unit pushes on the CCCD write,
        # when it differs from what a read returns. E.g. a stale water push (1 L) followed by a
        # correct read (17 L): the app reads after subscribing and the last frame wins, so calictl
        # must surface the read (tests/test_mock_integration.py). Not a "push-only" model.
        self.notify_push: dict[str, dict] = {}
        # Live notification subscriptions: state-char UUID -> [callback]. `MockBleakClient`
        # registers here on start_notify so the unit can PUSH after subscribe time, the way the
        # real unit does. Without this the offline harness only ever saw the one-shot push at
        # subscribe and `serve._confirm_lighting` / `device`'s on_push were unreachable in CI.
        self._subs: dict[str, list] = {}
        # One-off frame sink for hosts that are not in-process subscribers (the BLE fake peripheral
        # sets it): called as ``event_sink(function, frame)`` for every ``push(..., event=True)``.
        self.event_sink = None
        # LIGHTING, the unit's most treacherous behaviour (control-and-actuation.md): the state
        # char is a write-through ECHO — it reports what you WROTE, not what the lamps did (an
        # owner check in 2026-07 saw a "confirmed" readback while the lamps stayed dark). The only
        # truthful channel is the 1502 Mode-4 ramp notification carrying the REAL brightness.
        # So `state["lighting"]` is the echo, and these model the physical side:
        self.light_actual: dict[str, int] = {}  # what the lamps are really at (ramped)
        self.light_applies = True  # False = unit ACKs + echoes but lamps DON'T move
        self._light_ramp: dict[str, int] = {}  # zone -> target, stepped by tick()
        # WATER is measurement-gated on the van's own WATER SYSTEM being powered — NOT on the 1003
        # heartbeat (that was correlation; disproven at the van 2026-07-14, value-freshness.md).
        # Unpowered, the unit stops measuring and FREEZES BOTH tanks at their last reading, which is
        # what `freshness.implausible_water_drop` detects: a fresh-water drop to the 1 L latch while
        # grey is exactly frozen. Powered, it measures and pushes 1302 on an actual measured CHANGE.
        # The firmware ACKs some writes and then silently IGNORES them — the nastiest class for a
        # client, because nothing errors and only a readback reveals it. `driving` is an explicit
        # mock flag, NOT a derived predicate: the real "is the vehicle stationary" condition
        # (terminal-15 vs road speed vs parking brake) is still under decompile review, so deriving
        # it here would encode a guess. See `refusals` for the audit trail.
        self.driving = False
        self.refusals: list[tuple[str, str]] = []  # (function, why) for every ACK-and-ignore
        # Link liveness. The arm is a one-shot latch, but it does NOT last forever: the unit drops
        # a link that carries no 1003 beat for ~15 s (on-device 2026-07-09). Only a link that has
        # actually SEEN a beat can lapse — `_beat_t` stays None when a test arms the unit directly,
        # so hand-armed fixtures never expire underneath themselves.
        self._beat_t: float | None = None
        # ONE connection slot (the phone holding it is why buspi can't connect). OPT-IN: the tests
        # that model the single slot turn it on (e.g. the roof move inside the live session).
        self.one_slot = False
        self.holder: object | None = None  # the one connection slot (phone or daemon)
        self._wake_at: float | None = None  # scheduled re-wake (engine-crank drop)
        self._crank_drop = False  # drop the link at the end of this tick
        self.water_powered = True  # False = parked/locked: both tanks freeze
        self._water_latch: dict[str, int] = {}  # the frozen reading, captured when power drops
        self._water_pushed: dict[str, int] = {}  # last values notified, so we push only on change
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
            # Water: frozen at the last measured reading while the water system is unpowered. This
            # is the REAL water gate (see `water_powered`); the generic heartbeat latch below is a
            # different thing — it models the re-read chars the 1003 beat refreshes, and the
            # heartbeat notably does NOT refresh water.
            if fn == "water" and not self.water_powered and self._water_latch:
                vals.update(self._water_latch)
            # Generic freshness latch: a state char decays to a STALE latched value until the 1003
            # liveness heartbeat runs during the read (device.read_all/read do this to keep the link
            # up and refresh the re-read chars), after which the read returns the truth.
            if fn in self.read_latch and not self.armed:
                vals.update(self.read_latch[fn])
            return _pack_state(self.funcs[fn], vals)
        if uuid in (device.VERSION_CHAR, device.AUTH_CHAR):
            return b"\x04\x10\x02\x07"  # non-empty payload for the version/auth gate
        return bytes(6)  # unknown readable char: benign payload

    def decoded(self, function: str) -> dict:
        """Current decoded state as calictl would read it (for test assertions)."""
        return protocol.decode(self.funcs[function], self.read(self.funcs[function].state_char))

    # --- heartbeat ---------------------------------------------------------
    def beat(self, data: bytes) -> None:
        """A write to char 1003 — the liveness counter. Arms actuation."""
        ctr = int.from_bytes(bytes(data), "big")
        self.last_beat = ctr
        self.armed = True
        self._beat_t = self.now

    # --- notifications ---------------------------------------------------------
    def push(self, function: str, values: dict | None = None, event: bool = False) -> None:
        """Push a state-char notification to every subscribed client, as the real unit does.

        ``values`` overlays the stored state for this one frame (the lighting ramp uses it to send
        the REAL brightness while the stored state still holds the write-through echo). A no-op
        when nobody is subscribed or the unit is asleep. ``event=True`` marks a one-off ack/echo frame
        (never stored): it is also handed to :attr:`event_sink`.
        """
        f = self.funcs.get(function)
        if f is None or not f.state_char or not self.online:
            return
        frame = _pack_state(f, {**self.state.get(function, {}), **(values or {})})
        if event and self.event_sink:
            self.event_sink(function, frame)
        for cb in list(self._subs.get(f.state_char) or ()):
            cb(_Char(f.state_char, ["notify"]), frame)

    def _all_lights(self, func, st: dict, on: bool) -> dict:
        """Zone targets for the app's All-lights master (SET_PROFILE PN 12 LIGHTS_ON / PN 0 LIGHTS_OFF).

        MOCK-MODELLED, not van-verified (#230): ON lights every equipped zone that is off at
        ``control.LIGHT_ON_BRIGHTNESS`` (lit zones keep their level), OFF darkens every equipped
        zone. NOT_EQUIPPED (13) zones are untouched, and so is the pop-top reading light (L9) while
        the roof is down — it is unpowered then (the same fact as the L9 refusal in ``_refusal``).
        """
        roof_down = (self.state.get("roof") or {}).get("Position") in (0, 14)
        out = {}
        for sf in func.state_fields:
            cur = st.get(sf.name, 0)
            if not sf.name.startswith("BrightnessL") or cur == 13:
                continue
            if not on:
                out[sf.name] = 0
            elif cur == 0 and not (sf.name == "BrightnessLNine" and roof_down):
                out[sf.name] = control.LIGHT_ON_BRIGHTNESS
        return out

    def favourite_bits(self) -> int:
        """FavoriteProfileModifiedState as the REQUEST_CONFIG reply carries it (bit 0 = favourite 1)."""
        return sum(1 << (n - 1) for n, v in self.favourites.items() if v)

    def set_water_power(self, on: bool) -> None:
        """Power the van's water system on/off — the real gate on water measurement.

        Switching it OFF freezes both tanks at their current reading (the unit stops measuring, so
        every later read returns that latch no matter how the true level changes). Switching it ON
        resumes measurement, and the next :meth:`tick` notifies 1302 if a value actually moved.
        """
        on = bool(on)
        if not on and self.water_powered:
            w = self.state.get("water", {})
            self._water_latch = {k: w[k] for k in ("FreshWaterLevel", "WasteWaterLevel") if k in w}
        elif on:
            self._water_latch = {}
        self.water_powered = on

    def drop(self) -> None:
        """Van parks -> deep sleep: the link dies and the unit stops advertising."""
        self.online = False
        self.armed = False
        self.holder = None  # the link is gone, so the single slot is free again
        self._beat_t = None

    def wake(self) -> None:
        """Physical use (door/ignition) wakes the unit; it advertises again."""
        self.online = True

    # --- the clock ------------------------------------------------------------
    def tick(self, dt: float) -> set[str]:
        """Advance the unit's own clock by ``dt`` seconds and move everything that moves on it.

        Deterministic (no wall clock): tests call it directly, ``tools/applab/fake_unit_ble.py``
        calls it once a second. Returns the functions whose state changed, for notification.

        Modelled: the 1004 RTC (`CarTime*`), immediate-heating countdown (`RunningTimeinAction`
        per minute, `NormalOperation` off at 0), the cooler start timer (`TimerCounter*` counts
        down to `TimerHourSet:TimerMinSet`, then `State=1`, `TimerState=0`, `TimerElapsed=1`),
        starter-battery age (`AgeOneBattValuesMinutes` +1/min while the ignition is off, 0 while
        on, 255 cap), the ignition edge (terminal 15 rising sheds camping master and raises
        `campingmode.Enable`; falling clears `Enable` — the coupling seen live 2026-09-16), and
        roof travel (:meth:`_roof_tick`; no frames for ``ROOF_RELEASE_S`` = released).
        """
        import datetime as _dt

        self.now += dt
        changed: set[str] = set()
        v = self.state.get("vehicle")
        ign = bool(v.get("TerminalOneFive")) if v else False

        # RTC — the fields are 1900-based year, 0-BASED month (the app adds 1; verified in the app
        # lab: CarTimeMonth=9 displayed as October), 1-based day, then h/m/s.
        if v and all(
            k in v
            for k in (
                "CarTimeYear",
                "CarTimeMonth",
                "CarTimeDay",
                "CarTimeHour",
                "CarTimeMinute",
                "CarTimeSecond",
            )
        ):
            try:
                t = _dt.datetime(
                    1900 + int(v["CarTimeYear"]),
                    int(v["CarTimeMonth"]) + 1,
                    int(v["CarTimeDay"]),
                    int(v["CarTimeHour"]),
                    int(v["CarTimeMinute"]),
                    int(v["CarTimeSecond"]),
                )
                t += _dt.timedelta(seconds=dt)
                v.update(
                    CarTimeYear=t.year - 1900,
                    CarTimeMonth=t.month - 1,
                    CarTimeDay=t.day,
                    CarTimeHour=t.hour,
                    CarTimeMinute=t.minute,
                    CarTimeSecond=t.second,
                )
                changed.add("vehicle")
            except ValueError:
                pass  # garbage clock fields: leave them alone

        # ignition edge -> camping + starter-battery freshness
        if v is not None and self._last_t15 is not None and int(ign) != self._last_t15:
            cm = self.state.get("campingmode")
            if cm is not None:
                cm["Enable"] = int(ign)
                if ign:
                    cm["State"] = 0  # the unit sheds camping master when terminal 15 rises
                changed.add("campingmode")
            # The BLE link also drops for ~1 min at the engine crank (observed at the van) — the one
            # moment calictl both loses the link AND has state changes to reconcile. Deferred to the
            # END of this tick so the camping-shed notification still goes out first: the field data
            # (camping-watch) shows every ignition 0->1 paired with master_on 1->0, i.e. the daemon
            # did observe the shed, so the push cannot be swallowed by the drop.
            if ign and CRANK_DROP_S:
                self._crank_drop = True
        if v is not None:
            self._last_t15 = int(ign)
        e = self.state.get("energy")
        if e is not None and ign and e.get("AgeOneBattValuesMinutes", 0) != 0:
            e["AgeOneBattValuesMinutes"] = 0
            changed.add("energy")

        # per-minute counters
        self._acc_minute += dt
        while self._acc_minute >= 60:
            self._acc_minute -= 60
            a = self.state.get("airheater")
            if a is not None and a.get("NormalOperation"):
                left = max(0, int(a.get("RunningTimeinAction") or 0) - 1)
                a["RunningTimeinAction"] = left
                if left == 0:
                    a["NormalOperation"] = 0
                changed.add("airheater")
            if e is not None and not ign and "AgeOneBattValuesMinutes" in e:
                e["AgeOneBattValuesMinutes"] = min(255, int(e["AgeOneBattValuesMinutes"]) + 1)
                changed.add("energy")

        # cooler start timer
        c = self.state.get("cooler")
        if c is not None and c.get("TimerState") == 1 and v is not None and "CarTimeHour" in v:
            now_m = int(v["CarTimeHour"]) * 60 + int(v["CarTimeMinute"])
            start_m = int(c.get("TimerHourSet") or 0) * 60 + int(c.get("TimerMinSet") or 0)
            left = (start_m - now_m) % (24 * 60)
            if left == 0 or left > 24 * 60 - 2:  # reached (allow the tick to overshoot slightly)
                c.update(State=1, TimerState=0, TimerElapsed=1, TimerCounterHour=0, TimerCounterMin=0)
            else:
                c.update(TimerCounterHour=left // 60, TimerCounterMin=left % 60)
            changed.add("cooler")

        # heater departure timer: armed by the app's `3f3b017f1f3f` (OperationModeAirHeater=3,
        # observed 2026-09-16); at TimerHour:TimerMin the heater starts for RunningTime minutes.
        # The unit's own post-fire Mode value is UNVERIFIED — modelled as back to 0 (consumed).
        a = self.state.get("airheater")
        if a is not None and a.get("OperationModeAirHeater") == 3 and v is not None and "CarTimeHour" in v:
            now_m = int(v["CarTimeHour"]) * 60 + int(v["CarTimeMinute"])
            start_m = int(a.get("TimerHour") or 0) * 60 + int(a.get("TimerMin") or 0)
            left = (start_m - now_m) % (24 * 60)
            if left == 0 or left > 24 * 60 - 2:
                a.update(
                    NormalOperation=1, OperationModeAirHeater=0, RunningTimeinAction=a.get("RunningTime", 0)
                )
                changed.add("airheater")

        # roof: a counter that stopped arriving is no longer valid (the app reads a stale
        # SafetyCounterValid=1 as "another user is operating the roof" — observed 2026-09-16)
        r = self.state.get("roof")
        if (
            r is not None
            and r.get("SafetyCounterValid")
            and self.now - self._roof_last_frame > ROOF_COUNTER_STALE_S
        ):
            r["SafetyCounterValid"] = 0
            self._roof_streak = 0
            changed.add("roof")
        if r is not None and self._roof_tick(r, dt):
            changed.add("roof")

        # Lighting ramp: the lamps step toward the committed target (observed 01->03->04->05) and
        # the unit notifies 1502 Mode-4 frames carrying the REAL brightness. Each step pushes one
        # frame — that notification, NOT the echo readback, is what proves actuation.
        if self._light_ramp:
            done = []
            for zone, target in self._light_ramp.items():
                cur = self.light_actual.get(zone, self.state.get("lighting", {}).get(zone, 0))
                cur = cur + 1 if cur < target else (cur - 1 if cur > target else cur)
                self.light_actual[zone] = cur
                if cur == target:
                    done.append(zone)
            for zone in done:
                del self._light_ramp[zone]
            # Mode 4 = the SET_BRIGHTNESS/ramp notification the app confirms on.
            self.push("lighting", {**self.light_actual, "Mode": LIGHT_MODE_SET_BRIGHTNESS})

        # Water: only a POWERED system measures, and the unit notifies 1302 on a measured change
        # (value-freshness.md; `device._await_water_push` waits for exactly this). Parked, nothing
        # changes and nothing is pushed — which is why the buspi trace saw no water push at all.
        w = self.state.get("water")
        if w is not None and self.water_powered:
            cur = {k: w[k] for k in ("FreshWaterLevel", "WasteWaterLevel") if k in w}
            if cur and cur != self._water_pushed:
                self._water_pushed = cur
                changed.add("water")
                self.push("water")

        # The arm is a latch, not a subscription: a link that stops beating is dropped after
        # HEARTBEAT_TIMEOUT_S (on-device 2026-07-09) and the arm goes with it. Without this the mock
        # stayed armed forever after a single beat, so a daemon whose heartbeat task died mid-write
        # would still pass every offline test.
        if self.armed and self._beat_t is not None and self.now - self._beat_t > HEARTBEAT_TIMEOUT_S:
            self.armed = False
            self._beat_t = None
            self.drop()

        # Scheduled re-wake (engine crank): the unit comes back by itself.
        if self._wake_at is not None and self.now >= self._wake_at:
            self._wake_at = None
            self.wake()

        # Change-driven pushes, modelled ONLY for the chars the unit was CONFIRMED to push on real
        # hardware: campingmode (1202) and ignition/vehicle (1004) — control-and-actuation.md. The
        # heartbeat-traced buspi run of 2026-09-16 saw NO change-push on the other 12 subscribed
        # chars in 150 s, so pushing everything here would be fiction (protocol-crosscheck-applab).
        # The roof's 1402 pushes on every change too (CAPTURE 2026-10-10: `0302`, `030c`, `230c`, ...).
        for fn in changed & (CHANGE_PUSH_FNS | {"roof"}):
            self.push(fn)

        if getattr(self, "_crank_drop", False):  # engine crank: notify first, then lose the link
            self._crank_drop = False
            self.drop()
            self._wake_at = self.now + CRANK_DROP_S
        return changed

    # --- roof motion (CAPTURE 2026-10-10, see the ROOF_* constants) --------------
    def _roof_later(self, delay: float, **fields) -> None:
        """Schedule a delayed 1402 change (``0302`` -> ``0300`` 4 s later, ``2308`` -> ``1300`` ...)."""
        self._roof_timers.append((self.now + delay, fields))

    def _roof_halt(self, r: dict) -> None:
        """A release (or a STOP frame) while the motor runs: mid-travel the unit reports
        ``InfoPopUp`` 3 (`2303`), cleared to 0 after ``ROOF_INFO_CLEAR_S`` (`2300`)."""
        self._roof_press_t = None
        if self._roof_motor is None:
            return
        self._roof_motor = None
        if 0.0 < self._roof_p < 1.0:
            r["InfoPopUp"] = 3
            self._roof_later(ROOF_INFO_CLEAR_S, InfoPopUp=0)
        else:
            r["InfoPopUp"] = 0

    def _roof_edge(self, r: dict, new_dir: str | None) -> None:
        """A press, release or reversal (the frame's direction changed). An OPEN press first raises
        the pre-open checklist (`0302`, no motion, cleared after ``ROOF_INFO_CLEAR_S``) unless one
        was raised within ``ROOF_CHECKLIST_WINDOW_S``; a press after that is honoured. The capture
        only ever shows it before an open — a close press moved at once (`130c`)."""
        self._roof_halt(r)
        if new_dir is None:
            return
        if (new_dir, r.get("Position")) in (("up", 1), ("down", 0), ("down", 14)):
            return  # already at that limit: nothing to do
        recent = self._roof_checklist_t is not None and (
            self.now - self._roof_checklist_t <= ROOF_CHECKLIST_WINDOW_S
        )
        if new_dir == "up" and self.roof_checklist and not recent:
            self._roof_checklist_t = self.now
            r["InfoPopUp"] = 2
            self._roof_later(ROOF_INFO_CLEAR_S, InfoPopUp=0)
            return  # this press is spent: a FRESH press moves
        self._roof_press_t = self.now

    def _roof_tick(self, r: dict, dt: float) -> bool:
        """Advance the roof on the unit's clock. Returns True when 1402 changed."""
        before = dict(r)
        due = [t for t in self._roof_timers if t[0] <= self.now]
        self._roof_timers = [t for t in self._roof_timers if t[0] > self.now]
        for _t, fields in due:
            r.update(fields)
        if self._roof_dir is not None and self.now - self._roof_last_frame > ROOF_RELEASE_S:
            self._roof_dir = None  # frames stopped: released
            self._roof_halt(r)
        if self._roof_motor is not None and not r.get("SafetyCounterValid"):
            self._roof_halt(r)  # the counter lapsed under a running motor
        # Motor start: an honoured press, a valid counter, the press-to-start delay and the fresh-counter
        # withhold both over (a counter already streamed — the app's roof page — costs no withhold).
        start_at = max((self._roof_press_t or 0.0) + ROOF_START_S, self._roof_withhold_until)
        if (
            self._roof_motor is None
            and self._roof_press_t is not None
            and self._roof_dir is not None
            and r.get("SafetyCounterValid")
            and self.now >= start_at
        ):
            self._roof_motor, self._roof_run = self._roof_dir, 0.0
            self._roof_timers = []  # a pending 4 s clear must not overwrite the moving code
            r["InfoPopUp"] = 12  # moving (`030c` / `130c` at the old Position)
        elif self._roof_motor is not None:  # travel counts from the tick after the start
            self._roof_run += dt
            if self._roof_run >= ROOF_LEAVE_S:
                r["Position"] = 2  # between (`230c`)
            if self._roof_motor == "up":
                self._roof_p = min(1.0, self._roof_p + dt / ROOF_OPEN_S)
            else:
                self._roof_p = max(0.0, self._roof_p - dt / ROOF_CLOSE_S)
            if self._roof_p in (0.0, 1.0):  # end of travel: `2308`, then the final Position
                self._roof_motor = self._roof_press_t = None
                r.update(Position=2, InfoPopUp=8)
                self._roof_later(ROOF_SETTLE_S, Position=1 if self._roof_p else 0, InfoPopUp=0)
        return r != before

    # --- control writes ----------------------------------------------------
    def _refusal(self, fn: str, ctrl: dict) -> str | None:
        """Why the unit would ACK this write and then ignore it, or ``None`` to apply it.

        Only DEVICE-confirmed refusals live here. The unit's *reason* for each is its own; what
        matters for a client is that the write appears to succeed and the state never moves.
        """
        # Camping master ON is refused while the vehicle is being driven — live-verified
        # 2026-08-19: the write did not take, and the unit's own console showed "Diese Funktion ist
        # während der Fahrt nicht verfügbar". Gated on the explicit `driving` flag, since the real
        # predicate is still under decompile review.
        if fn == "campingmode" and self.driving and ctrl.get("State") == 1:
            return "camping master is refused while driving"
        # The pop-top reading light (L9) needs the roof raised — the lamp is unpowered when the
        # roof is down, so the write lands and nothing lights.
        if fn == "lighting" and ctrl.get("Mode") == LIGHT_MODE_SET_BRIGHTNESS:
            pos = (self.state.get("roof") or {}).get("Position")
            zone = ctrl.get("BrightnessLNine")
            if pos in (0, 14) and zone not in (None, LIGHT_ZONE_UNCHANGED) and zone > 0:
                return "the pop-top reading light needs the roof raised"
        # The cooling timer can only be set while the fridge is OFF. Only an actual CHANGE counts:
        # a full-packet write carries the timer fields on every unrelated command (power, level) —
        # the app sends them at their dictionary default (30/62, the leave-unchanged sentinel the
        # apply step below also skips), calictl sends the current set time — and the unit does not
        # refuse those.
        cs = self.state.get("cooler") or {}
        if fn == "cooler" and cs.get("State") == 1:
            sentinel = {cf.name: cf.default for cf in self.funcs["cooler"].control_fields}
            moves_timer = any(
                ctrl.get(c) is not None and ctrl.get(c) != sentinel.get(c) and ctrl.get(c) != cs.get(s)
                for c, s in (("TimerHour", "TimerHourSet"), ("TimerMin", "TimerMinSet"))
            )
            if ctrl.get("TimerStart") == 1 or moves_timer:
                return "the cooling timer can only be set while the fridge is off"
        # An empty favourite: NOT device-confirmed — decompile-backed (dg/h u0: the app only offers
        # activation on a stored tile). Modelled as ACKed and ignored (spec A2).
        if fn == "lighting" and ctrl.get("Mode") == LIGHT_MODE_SET_PROFILE:
            pn = ctrl.get("ProfileNumber")
            if pn in self.favourites and self.favourites[pn] is None:
                return "favourite %d is empty" % pn
        return None

    def write(self, uuid: str, data: bytes) -> None:
        if uuid == device.HEARTBEAT_CHAR:
            return self.beat(data)
        fn = self._control_char.get(uuid)
        if fn is None:
            return  # write to a non-control char: ignore
        func = self.funcs[fn]
        frame = bytes(data)
        self.writes.append((fn, frame))
        ctrl = control.decode_control(func, frame)

        # 1) firmware parse/validate layer — ALWAYS runs, armed or not. An out-of-range
        #    value is rejected and the unit drops the ATT link (observed 0x0E).
        for cf in func.control_fields:
            if cf.placed and cf.name in ctrl:
                # A 2-bit field at 3 is the leave-unchanged sentinel, not a value: the app's
                # post-write neutral frame carries State=3 on every 2-bit field (cooler
                # `ff771e3e1f1f`, heater `3f7b007f1f3f`) and the unit accepts it. Curated
                # `valid` sets (cooler State {0,1}) constrain calictl's OWN commands, not that.
                if cf.width == 2 and ctrl[cf.name] == LEAVE_UNCHANGED_2BIT:
                    continue
                try:
                    protocol.check_value(func, cf.name, cf.width, ctrl[cf.name], cf.valid)
                except ValueError as e:
                    raise MockDisconnect("out-of-range write to %s: %s" % (fn, e)) from e

        # 2) apply layer — gated on the 1003 heartbeat (issue #2). Without a live
        #    heartbeat the write is ACKed and ignored. Lighting is the exception: an AWAKE unit
        #    actuates a bare SET_BRIGHTNESS + commit with no heartbeat (photon-verified on-device
        #    2026-08-16; the app's E() writes direct). Its own gates — stage/commit, the frame's
        #    non-zero ProfileNumber, the L9 roof refusal, echo vs ramp — still apply below.
        if not self.armed and fn != "lighting":
            return

        # 2b) UNIT-SIDE REFUSALS: parsed fine, armed, ACKed — and then silently not applied. The
        #     client sees no error, so only a readback reveals it; that is exactly the trap this
        #     models. All three are DEVICE-confirmed (control-and-actuation.md); calictl mirrors
        #     them client-side in `control.command_precondition`, and with the mock permissive
        #     those mirrors could be deleted without any test noticing.
        refusal = self._refusal(fn, ctrl)
        if refusal:
            self.refusals.append((fn, refusal))
            return  # ACK, no state change — the silent kind
        st = self.state.setdefault(fn, {})

        # Lighting is COMMIT-GATED (HCI-verified 2026-07-13). A SET_PROFILE (Mode 16) or
        # SET_BRIGHTNESS (Mode 4) only STAGES the change; the unit APPLIES it when the commit
        # frame (Mode 0, control.LIGHT_COMMIT) lands right after. With no commit, the write is
        # ACKed but never applied. The apply gate for SET_BRIGHTNESS is the FRAME's ProfileNumber
        # (the app hardcodes 9), NOT a separately-active profile: a brightness frame carrying PN=0
        # is ignored, PN=9 applies (and makes profile 9 the active one).
        # NOTE: there is NO REQUEST_CONFIG-preamble apply-gate. A bare SET+commit actuates on an
        # awake unit — photon-verified on-device 2026-08-16 (the earlier "preamble required" gate
        # was a wake-state confound; the app's E() writes DIRECT and never sends the preamble).
        if fn == "lighting":
            mode = ctrl.get("Mode")
            pn = ctrl.get("ProfileNumber")
            lv = ctrl.get("LightValue", 0)
            # Configuration frames are ECHOED as one-off 1502 frames (never stored state) (the app reads
            # its wake-up page / door row / favourite tiles from these 1502 frames).
            if mode == control.LIGHT_MODE_WAKEUP_TIME:  # m0
                self.wakeup = {"Timestamp": ctrl.get("Timestamp", 0), "LightValue": lv}
                self.push("lighting", {"Mode": mode, **self.wakeup}, event=True)
                return
            if mode == LIGHT_MODE_SET_PROFILE and pn == control.LIGHT_PROFILE_DOOR_CONTACT:  # n4
                self.door_contact = 1 if lv == 1 else 0
                self.push(
                    "lighting",
                    {"Mode": mode, "ProfileNumber": pn, "LightValue": self.door_contact},
                    event=True,
                )
                return
            if mode == control.LIGHT_MODE_REQUEST_CONFIG:  # d0: reply = Mode 12 + favourite bits, then
                # the stored wake-up (Mode 20) and door-contact (Mode 16/PN 8) frames — the app awaits
                # 6 frames and fills its wake-up/door state (F0) from them. The exact 6-frame
                # composition of the real unit is not captured; these are the ones the app decodes.
                self.push("lighting", {"Mode": mode, "LightValue": self.favourite_bits()}, event=True)
                if self.wakeup is not None:
                    self.push("lighting", {"Mode": control.LIGHT_MODE_WAKEUP_TIME, **self.wakeup}, event=True)
                if self.door_contact:
                    self.push(
                        "lighting",
                        {
                            "Mode": LIGHT_MODE_SET_PROFILE,
                            "ProfileNumber": control.LIGHT_PROFILE_DOOR_CONTACT,
                            "LightValue": self.door_contact,
                        },
                        event=True,
                    )
                return
            if mode == control.LIGHT_MODE_SET_COLOR and pn in self.favourites:  # l3 step a
                self.favourite_colour[pn] = lv
                self.push("lighting", {"Mode": mode, "ProfileNumber": pn}, event=True)  # its own ack
                return
            if mode == LIGHT_MODE_SET_BRIGHTNESS and pn in self.favourites:  # l3 step b: SAVE, not live
                zones = {
                    cf.name: ctrl[cf.name]
                    for cf in func.control_fields
                    if cf.placed
                    and cf.name.startswith("BrightnessL")
                    and cf.name in ctrl
                    and ctrl[cf.name] != LIGHT_ZONE_UNCHANGED
                    and func.state_field(cf.name)
                }
                self._pending_light = ("save", pn, zones)
                return
            if mode == LIGHT_MODE_SET_PROFILE:
                self._pending_light = ("profile", ctrl.get("ProfileNumber", st.get("ProfileNumber", 0)))
                return
            if mode == LIGHT_MODE_SET_BRIGHTNESS:
                if not ctrl.get("ProfileNumber"):  # frame carries no working profile (PN=0) -> ignored
                    self._pending_light = None
                    return
                zones = {
                    cf.name: ctrl[cf.name]
                    for cf in func.control_fields
                    if cf.placed
                    and cf.name.startswith("BrightnessL")
                    and cf.name in ctrl
                    and ctrl[cf.name] != LIGHT_ZONE_UNCHANGED
                    and func.state_field(cf.name)
                }
                self._pending_light = ("zones", zones, ctrl.get("ProfileNumber"))
                return
            if mode == LIGHT_MODE_COMMIT:  # apply the staged change
                p = getattr(self, "_pending_light", None)
                if p and p[0] == "save":
                    _, n, zones = p
                    self.favourites[n] = {"zones": zones, "colour": self.favourite_colour.get(n, 1)}
                    self.push("lighting", {"Mode": LIGHT_MODE_SET_BRIGHTNESS, "ProfileNumber": n}, event=True)
                elif p and p[0] == "profile":
                    st["ProfileNumber"] = p[1]
                    fav = self.favourites.get(p[1])
                    if p[1] in (control.LIGHT_PROFILE_ALL_ON, control.LIGHT_PROFILE_ALL_OFF):
                        zones = self._all_lights(func, st, p[1] == control.LIGHT_PROFILE_ALL_ON)
                    else:
                        zones = fav["zones"] if fav else None
                    if zones is not None:  # u0 favourite / Q all-lights: apply the levels, ack on 1502
                        if self.light_applies:
                            for zone in zones:
                                self.light_actual.setdefault(zone, st.get(zone, 0))
                            self._light_ramp.update(zones)
                        st.update(zones)
                        self.push("lighting", {"Mode": LIGHT_MODE_SET_PROFILE}, event=True)
                elif p and p[0] == "zones":
                    # Snapshot the PHYSICAL baseline before the echo lands: `st` is about to be
                    # overwritten with the written value, so reading the ramp's starting point
                    # from it afterwards would start every ramp already at its target.
                    if self.light_applies:
                        for zone in p[1]:
                            self.light_actual.setdefault(zone, st.get(zone, 0))
                        self._light_ramp.update(p[1])
                    # The ECHO updates unconditionally — that is the trap: the state char reports
                    # the written value whether or not the lamps moved. The physical side only
                    # follows when the unit actually actuates, and then it RAMPS (tick()).
                    st.update(p[1])
                    st["ProfileNumber"] = p[2]  # a brightness set makes its profile the active one
                self._pending_light = None
            return

        # Roof (1401): the app streams `Up/Down/SafetyCounter` every ~500 ms while its roof page is
        # open (direction 0 = just the counter). The unit raises `SafetyCounterValid` (1402 bit 7)
        # once it sees the counter incrementing and only then honours a move; a restarted counter
        # drops validity again (~3 s withhold on the real unit). Motion is modelled coarsely: one
        # position step per valid move frame (closed 0 -> middle 2 -> open 1 and back).
        if fn == "roof":
            # SafetyCounter validity = the counter is MONOTONIC and still ADVANCING. The app's
            # counter is seed + elapsed_ms/500, so it only increments every ~500 ms while frames
            # arrive at ~500 ms idle and ~125 ms during a press (observed 2026-09-16) — several
            # frames may carry the SAME value; a decrease/restart invalidates, as does no
            # increment for ROOF_COUNTER_STALE_S. Two increments seen = validated.
            ctr = ctrl.get("SafetyCounter")
            if self._roof_ctr is None or ctr < self._roof_ctr:
                self._roof_streak = 0  # first frame / restarted counter
            elif ctr > self._roof_ctr:
                self._roof_streak += 1
                self._roof_ctr_advanced = self.now
            self._roof_ctr = ctr
            stale = self._roof_streak and (self.now - self._roof_ctr_advanced) > ROOF_COUNTER_STALE_S
            if stale:
                self._roof_streak = 0
            if self._roof_streak >= 2 and not st.get("SafetyCounterValid"):
                # Counter just validated: the unit WITHHOLDS the motor for ROOF_WITHHOLD_S while it
                # satisfies itself the stream is live. The withhold runs from VALIDATION, so a counter
                # the roof page already streams (the app, calictl's roof view) has paid it before the
                # press — the real app's press moved ~1.2 s later (CAPTURE 2026-10-10) — while a
                # counter started at the press pays it on top.
                self._roof_withhold_until = self.now + ROOF_WITHHOLD_S
            before = dict(st)
            st["SafetyCounterValid"] = 1 if self._roof_streak >= 2 else 0
            if not st["SafetyCounterValid"] and self._roof_motor is not None:
                self._roof_halt(st)  # an invalid counter never drives the motor
            up, down = ctrl.get("Up") == 1, ctrl.get("Down") == 1
            new_dir = "up" if up and not down else "down" if down and not up else None
            if new_dir != self._roof_dir:
                self._roof_edge(st, new_dir)  # press / release / reversal
            self._roof_dir = new_dir
            self._roof_last_frame = self.now
            if st != before:
                self.push("roof")  # 1402 notifies on change (CAPTURE 2026-10-10)
            return  # motion itself happens on the clock: tick()

        for cf in func.control_fields:
            if not (cf.placed and cf.name in ctrl):
                continue
            if cf.width == 2 and ctrl[cf.name] == LEAVE_UNCHANGED_2BIT:
                continue  # full-packet "leave unchanged" 2-bit sentinel
            # Wider fields: the app fills every UNTARGETED field with the model's default (its
            # `v()` value — heater HeatingLevel 11 / RunningTime 127 / TimerHour 31 / TimerMin 63,
            # cooler Level 7 / TimerHour 30 ...), which the unit treats as "leave unchanged". Seen
            # live 2026-09-16: the app's Dauerbetrieb-OFF frame is `0f7b007f1f3f`. Those defaults
            # are all outside each field's valid range, so nothing legitimate is lost.
            if cf.width > 2 and cf.default is not None and ctrl[cf.name] == cf.default:
                continue
            # `<X>Request` control bits drive the `<X>` state bit (NormalOperationRequest ->
            # NormalOperation, PermanentOperationRequest -> PermanentOperation).
            target = cf.name
            if func.state_field(target) is None and target.endswith("Request"):
                target = target[: -len("Request")]
            # Cooler start time: control TimerHour/TimerMin land in state TimerHourSet/TimerMinSet
            # (the app's picker writes them alone — ff7704021f1f = 04:02 — and re-reads the *Set*
            # fields to display "cooling starts at"). Heater keeps the same names on both sides.
            if (
                fn == "cooler"
                and cf.name in ("TimerHour", "TimerMin")
                and func.state_field(cf.name + "Set") is not None
            ):
                st[cf.name + "Set"] = ctrl[cf.name]
                continue
            # Cooler timer ACTIONS: TimerStart=1 arms the timer (TimerState=1), TimerCancel=1
            # clears it — the app's frames carry only the action bit (f7771e3e1f1f, observed).
            if (
                fn == "cooler"
                and cf.name in ("TimerStart", "TimerCancel")
                and ctrl[cf.name] == 1
                and func.state_field("TimerState") is not None
            ):
                st["TimerState"] = 1 if cf.name == "TimerStart" else 0
                continue
            if func.state_field(target) is None:
                continue  # not a name-aligned control→state field
                # (offset-remapped timers are NOT faked)
            st[target] = ctrl[cf.name]
        # Immediate heating starts the run-time countdown: RunningTimeinAction (1702) mirrors the
        # configured RunningTime while NormalOperation is on and reads 0 otherwise (the app's status
        # bar shows "Active • N min remaining" from it — with 0 it says "0 min remaining").
        if fn == "airheater" and func.state_field("RunningTimeinAction") is not None:
            st["RunningTimeinAction"] = st.get("RunningTime", 0) if st.get("NormalOperation") else 0


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

    unit: MockCamperUnit | None = None  # set by the harness / fixture

    def __init__(self, addr, timeout=None, adapter=None):
        self.addr = addr
        self.is_connected = False
        self._notifying: list[str] = []  # chars this client subscribed (for clean removal)

    @classmethod
    def bind(cls, unit: MockCamperUnit) -> type[MockBleakClient]:
        cls.unit = unit
        return cls

    async def connect(self):
        if self.unit is not None and not self.unit.online:
            raise MockDisconnect("van asleep (not advertising)")
        # ONE connection slot: while the phone app (or another client) holds it the unit stops
        # advertising and a second connect fails. calictl's own taxonomy distinguishes this from a
        # sleeping van (value-freshness.md) — with a mock that let everyone in, "phone holds the
        # slot" and "van asleep" were the same behaviour and a mis-classification was invisible.
        if self.unit is not None and self.unit.one_slot and self.unit.holder not in (None, self):
            raise MockDisconnect("connection slot busy (another client is connected)")
        if self.unit is not None:
            self.unit.holder = self
        self.is_connected = True

    async def disconnect(self):
        self.is_connected = False
        if self.unit is not None and self.unit.holder is self:
            self.unit.holder = None  # release the single slot
        self._unsubscribe_all()  # a dropped link takes its subscriptions with it

    def _unsubscribe_all(self):
        for uuid in self._notifying:
            subs = self.unit._subs.get(uuid) if self.unit else None
            if subs:
                del subs[:1]  # drop one registration for this client
        self._notifying.clear()

    @property
    def services(self):
        unit = self.unit
        chars = [
            _Char(device.VERSION_CHAR, ["read"]),
            _Char(device.AUTH_CHAR, ["read"]),
            _Char(device.HEARTBEAT_CHAR, ["write"]),
        ]
        for _fn, f in unit.funcs.items():
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
            self.is_connected = False  # mirror the unit dropping the link
            raise

    async def start_notify(self, uuid, cb):
        # The unit pushes a char's CURRENT value once as soon as a client enables notifications
        # (buspi trace 2026-09-16: one notify per subscribed char right after its CCCD write, then
        # only on change — it does not stream). A pending `notify_push` overlay replaces the
        # subscribe-time value; a function with a stale read-latch armed
        # pushes nothing (its fresh value only arrives once the heartbeat has run).
        fn = self.unit._state_char.get(str(uuid))
        if fn is None:
            return None
        # Keep the callback so the unit can push AFTER subscribe time too (unit.push / tick()).
        self.unit._subs.setdefault(str(uuid), []).append(cb)
        self._notifying.append(str(uuid))
        if fn in self.unit.notify_push:
            frame = _pack_state(
                self.unit.funcs[fn], {**self.unit.state.get(fn, {}), **self.unit.notify_push[fn]}
            )
            cb(_Char(str(uuid), ["notify"]), frame)
        elif fn not in self.unit.read_latch:
            cb(_Char(str(uuid), ["notify"]), self.unit.read(str(uuid)))
        return None

    async def stop_notify(self, uuid):
        subs = self.unit._subs.get(str(uuid)) if self.unit else None
        if subs:
            del subs[:1]
        if str(uuid) in self._notifying:
            self._notifying.remove(str(uuid))
        return None


class FakePairingTransport:
    """Deterministic stand-in for `calictl.pairing_bluez.BluezTransport`, for the guided-pairing
    web-wizard e2e (no dbus/BlueZ in CI/dev sandboxes). Same async transport contract
    (`calictl.pairing_bluez.PairingRunner`'s docstring): scripts the happy path (scan -> connect
    -> pair -> waiting_passkey -> passkey ``RIGHT_PASSKEY`` -> bonded at ``FOUND_ADDR``) and the
    wrong-passkey path (-> ``EV_PAIR_FAIL``, which the real SM retries up to
    ``pairing.MAX_ATTEMPTS`` times before giving up -> ``error``/``pairing_failed``). Timings are
    short but non-zero so a 1 s poll observes every state transition. Installed in place of the
    real module by `tools.run_against_mock.install_fake_pairing_transport`.
    """

    FOUND_ADDR = "C0:FF:EE:00:00:01"
    RIGHT_PASSKEY = 123456

    def __init__(self, on_event=None):
        self.on_event = on_event
        # e2e knob (Task 11): CALICTL_FAKE_PAIRING selects a scripted failure mode on top of the
        # happy path above -- "connect_failed" (every connect() raises) or "radio_busy" (stop_scan
        # reports another BlueZ client still scanning), read once at construction like the real
        # transport reads its environment at import time.
        self.mode = os.environ.get("CALICTL_FAKE_PAIRING", "")
        self.radio_busy = False

    async def _emit(self, ev, arg=0):
        if self.on_event is not None:
            await self.on_event(ev, arg)

    async def start_scan(self):
        asyncio.ensure_future(self._device_found_soon())

    async def _device_found_soon(self):
        await asyncio.sleep(0.5)
        await self._emit(EV_DEVICE_FOUND)

    async def stop_scan(self):
        self.radio_busy = self.mode == "radio_busy"

    async def connect(self):
        if self.mode == "connect_failed":
            raise ConnectionError("fake: link refused")
        await asyncio.sleep(0.1)
        await self._emit(EV_CONNECTED)

    async def pair(self):
        await asyncio.sleep(0.1)
        await self._emit(EV_PASSKEY_REQUESTED)

    async def send_passkey(self, pk):
        await asyncio.sleep(0.2)
        await self._emit(EV_PAIR_OK if pk == self.RIGHT_PASSKEY else EV_PAIR_FAIL)

    async def verify(self):
        await asyncio.sleep(0.1)
        return 5  # any int = "verified"; the real transport counts readable state chars

    async def persist_bond(self):
        return self.FOUND_ADDR

    async def disconnect(self):
        pass

    async def remove_bond(self):
        pass
