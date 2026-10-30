import Foundation
import Testing
@testable import CaliBLE
@testable import CaliCore

/// Real frames. campingmode and energy are `tests/vectors/camper_codec.json` decode cases
/// (`campingmode/state/Installed/onehot` = 0x20 State 0, with State set = 0x21; the
/// `energy/state/StateLandAfs/onehot` layout with StateLandAfs 0 = inactive or 1 = active).
/// No vector has CommunicationVersion 2: the `general/state/CommunicationVersion/onehot` case shows
/// the version is the whole 9th byte, so the frame below sets it to 2.
private enum Fx {
    static let general = CamperGATT.stateChars["general"]!
    static let camping = CamperGATT.stateChars["campingmode"]!
    static let energy = CamperGATT.stateChars["energy"]!
    static let generalV2: [UInt8] = [0, 0, 0, 0, 0, 0, 0, 0, 2]
    static let water = CamperGATT.stateChars["water"]!
    static let lighting = CamperGATT.stateChars["lighting"]!
    /// `water` layout: FreshWaterLevel is byte 1, WasteWaterLevel byte 4 (liters, camper_codec.json).
    static func waterFrame(fresh: UInt8, waste: UInt8 = 0) -> [UInt8] { [0, fresh, 0, 0, waste, 0] }
    static func lights(pn: UInt8, mode: UInt8) -> [UInt8] {
        [pn, mode, 0, 0, 0, 0, 0, 0, 0x55, 0x55, 0x55, 0x55, 0xd5, 0x5d, 0xdd, 0xdd]
    }
    static let wakeup: [UInt8] = [0, 20, 0x6a, 0xc5, 0xed, 0xf0, 0x01, 0x01, 0xee, 0xee, 0xee, 0xee, 0xee, 0xee, 0xee, 0xee]
    static let campingOff: [UInt8] = [0x20]
    static let campingOn: [UInt8] = [0x21]
    static let noShore: [UInt8] = [0, 0, 0, 0, 0x00]
    static let shore: [UInt8] = [0, 0, 0, 0, 0x01]

    static func frames(camping: [UInt8] = campingOff, energy e: [UInt8] = noShore) -> [UInt16: [UInt8]] {
        [general: generalV2, self.camping: camping, energy: e]
    }
}

/// Counts `connect()` calls and can make subscribe fail or reads wait; everything else goes to the fake.
private actor Counting: CamperTransport {
    let inner: FakeCamperTransport
    nonisolated let events: AsyncStream<TransportEvent>
    var connects = 0
    var cancels = 0
    var failSubscribe = false
    /// While set, every read waits until `releaseReads()` (`hang`: forever, ignoring cancellation).
    var holdReads = false
    var held: [CheckedContinuation<Void, Never>] = []
    init(_ inner: FakeCamperTransport) { self.inner = inner; events = inner.events }
    func connect() async { connects += 1; await inner.connect() }
    func cancel() async { cancels += 1; await inner.cancel() }
    func read(_ char: UInt16) async throws -> [UInt8] {
        if holdReads { await withCheckedContinuation { held.append($0) } }
        return try await inner.read(char)
    }
    func write(_ char: UInt16, _ bytes: [UInt8]) async throws { try await inner.write(char, bytes) }
    func subscribe(_ chars: [UInt16]) async throws {
        if failSubscribe { failSubscribe = false; throw TransportError.writeRejected }
        try await inner.subscribe(chars)
    }
    func set(failSubscribe f: Bool) { failSubscribe = f }
    func set(holdReads h: Bool) { holdReads = h }
    func releaseReads() { holdReads = false; held.forEach { $0.resume() }; held = [] }
}

/// Polls `cond` in real time (the session works on its own tasks) for up to 2 s.
private func until(_ what: String, _ cond: () async -> Bool) async throws {
    for _ in 0..<2000 {
        if await cond() { return }
        try await Task.sleep(for: .milliseconds(1))
    }
    Issue.record("timed out waiting for \(what)")
}

private struct Rig {
    let clock = TestClock()
    let fake: FakeCamperTransport
    let t: Counting
    let s: CamperSession

    init(_ frames: [UInt16: [UInt8]] = Fx.frames(), semantics: Semantics = .shared) {
        fake = FakeCamperTransport(clock: clock, frames: frames)
        t = Counting(fake)
        s = CamperSession(transport: t, clock: clock, semantics: semantics)
    }

    func heartbeats() async -> [[UInt8]] { await fake.writes.filter { $0.char == CamperGATT.heartbeat }.map(\.bytes) }
}

struct SessionTests {
    /// - Test: T_IOS_SESSION_READ_ALL
    /// - Verifies: R_IOS_TESTED, R_IOS_BLE_DIRECT, R_IOS_V2_ONLY
    @Test func readAllFillsStateAndGate() async throws {
        let r = Rig()
        try await r.s.ensureConnected(timeout: .seconds(10))
        let st = await r.s.state
        #expect(await r.s.link == .up)
        #expect(st.fields["general"]?["CommunicationVersion"] == 2)
        #expect(st.gate == .supported)
        #expect(st.fields["campingmode"]?["State"] == 0)
        #expect(st.interpreted["campingmode"]?["master_on"] == .bool(false))
        #expect(st.interpreted["energy"]?["shore_state"] == .string("inactive"))
        #expect(st.asOf != nil)
        #expect(st.semanticsErrors.isEmpty)
        #expect(st.fields["water"] == nil)  // not seeded: its read fails, the pass goes on

        // The last frame wins: a later read replaces the earlier one.
        await r.fake.seed(Fx.camping, Fx.campingOn)
        try await r.s.readAll()
        #expect(await r.s.state.fields["campingmode"]?["State"] == 1)
    }

    /// - Test: T_IOS_SESSION_REST
    /// - Verifies: R_IOS_TESTED, R_IOS_POWER_SAVE
    @Test func hangUpWithCampingOffEntersRestAndDoesNotRedial() async throws {
        let r = Rig()
        try await r.s.ensureConnected(timeout: .seconds(10))
        await r.fake.hangUp(after: .seconds(30))
        try await until("heartbeat + hang-up sleepers") { r.clock.sleeperCount >= 2 }
        r.clock.advance(by: .seconds(30))
        try await until("rest") { if case .rest = await r.s.link { true } else { false } }
        #expect(await r.s.heartbeatBeating == false)
        try await until("no sleeper left") { r.clock.sleeperCount == 0 }  // no reconnect is waiting
        for _ in 0..<60 { r.clock.advance(by: .seconds(1)); await Task.yield() }
        #expect(r.clock.sleeperCount == 0)
        #expect(await r.t.connects == 1)
        #expect(await r.s.pendingReconnect == nil)
    }

    /// Shore power active keeps the reconnects going (the app's rule), so no rest.
    /// - Test: T_IOS_SESSION_SHORE_NO_REST
    /// - Verifies: R_IOS_TESTED, R_IOS_POWER_SAVE
    @Test func hangUpWithShorePowerDoesNotRest() async throws {
        let r = Rig(Fx.frames(energy: Fx.shore))
        try await r.s.ensureConnected(timeout: .seconds(10))
        await r.fake.hangUp(after: .seconds(30))
        try await until("heartbeat + hang-up sleepers") { r.clock.sleeperCount >= 2 }
        r.clock.advance(by: .seconds(30))
        try await until("reconnect scheduled") { await r.s.pendingReconnect != nil }
        if case .rest = await r.s.link { Issue.record("rest with shore power") }
    }

    /// - Test: T_IOS_SESSION_REST_COOLDOWN
    /// - Verifies: R_IOS_TESTED, R_IOS_POWER_SAVE
    @Test func ensureConnectedAfterRestWaitsSevenSeconds() async throws {
        let r = Rig()
        try await r.s.ensureConnected(timeout: .seconds(10))
        await r.fake.hangUp(after: .seconds(30))
        try await until("heartbeat + hang-up sleepers") { r.clock.sleeperCount >= 2 }
        r.clock.advance(by: .seconds(30))
        try await until("rest") { if case .rest = await r.s.link { true } else { false } }

        // The cooldown does not fit a 1 s budget: the caller learns how long to wait.
        await #expect(throws: SessionError.inRest(retryAfter: .seconds(7))) {
            try await r.s.ensureConnected(timeout: .seconds(1))
        }
        let call = Task { try await r.s.ensureConnected(timeout: .seconds(10)) }
        // the cooldown + the caller's deadline + the bring-up's deadline
        try await until("cooldown sleeper") { r.clock.sleeperCount == 3 }
        r.clock.advance(by: .milliseconds(6900))
        #expect(r.clock.sleeperCount == 3)  // the cooldown sleep did not end
        #expect(await r.t.connects == 1)
        r.clock.advance(by: .milliseconds(100))
        try await call.value
        #expect(await r.t.connects == 2)
        #expect(await r.s.link == .up)
    }

    /// - Test: T_IOS_SESSION_BACKOFF
    /// - Verifies: R_IOS_TESTED, R_IOS_BACKGROUND_LINK, R_IOS_POWER_SAVE
    @Test func linkLostWithCampingOnBacksOff() async throws {
        let r = Rig(Fx.frames(camping: Fx.campingOn))
        try await r.s.ensureConnected(timeout: .seconds(10))
        await r.fake.dropLink()
        // 5 s is raised to the unit's 7 s cooldown after a lost link; then 10, 30, 60, 60.
        for (n, delay) in [7, 10, 30, 60, 60].enumerated() {
            await r.fake.failNextConnect()
            try await until("reconnect in \(delay) s") { await r.s.pendingReconnect == .seconds(delay) }
            try await until("one sleeper") { r.clock.sleeperCount == 1 }
            r.clock.advance(by: .seconds(delay) - .milliseconds(100))
            #expect(r.clock.sleeperCount == 1, "early redial before \(delay) s")
            #expect(await r.t.connects == 1 + n, "early redial before \(delay) s")
            r.clock.advance(by: .milliseconds(100))
            try await until("redial \(n + 1)") { await r.t.connects == 2 + n }
        }
        // The next attempt succeeds and the link holds again.
        try await until("reconnect in 60 s") { await r.s.pendingReconnect == .seconds(60) }
        try await until("one sleeper") { r.clock.sleeperCount == 1 }
        r.clock.advance(by: .seconds(60))
        try await until("up") { await r.s.link == .up }
        #expect(await r.s.pendingReconnect == nil)
    }

    /// A link the transport brings up by itself (iOS reconnect, state restoration) is read too.
    /// - Test: T_IOS_SESSION_UNASKED_LINK
    /// - Verifies: R_IOS_TESTED, R_IOS_BACKGROUND_LINK
    @Test func linkUpWithoutEnsureConnectedSubscribesAndReads() async throws {
        let r = Rig()
        await r.fake.connect()
        try await until("read after an unasked link-up") { await r.s.state.fields["general"]?["CommunicationVersion"] == 2 }
        #expect(await r.s.link == .up)
        await r.fake.pushForeign(char: Fx.camping, bytes: Fx.campingOn)
        try await until("subscribed: push arrives") { await r.s.holdsInBackground }
    }

    /// A new link publishes `.up` with no live function: the previous link's frames stay in `fields`
    /// for display, but they are not this link's until the read pass has run.
    /// - Test: T_IOS_SESSION_LIVE_PER_LINK
    /// - Verifies: R_IOS_TESTED, R_IOS_HONEST
    @Test func liveFunctionsStartEmptyOnEachLink() async throws {
        let r = Rig()
        try await r.s.ensureConnected(timeout: .seconds(10))
        #expect(await r.s.state.liveFunctions.contains("general"))
        await r.fake.dropLink()
        try await until("down") { await r.s.link != .up }
        var updates = await r.s.updates().makeAsyncIterator()
        await r.fake.connect()  // the transport brings the link back by itself
        var upUpdate: CamperState?
        while upUpdate == nil, let (st, link) = await updates.next() {
            if link == .up { upUpdate = st }
        }
        #expect(upUpdate?.liveFunctions.isEmpty == true)
        #expect(upUpdate?.fields["general"] != nil)  // the old frames stay for display
        #expect(await r.s.liveFunctions.isEmpty)
        // The read pass of the new link runs after the unit's 7 s cooldown.
        try await until("cooldown sleeper") { r.clock.deadlines.contains { $0 >= .seconds(6) } }
        r.clock.advance(by: .seconds(7))
        try await until("read pass") { await r.s.state.liveFunctions.contains("general") }
    }

    /// Another central (the VW app, the satellite) changes the unit: its push updates our state.
    /// - Test: T_IOS_SESSION_FOREIGN_PUSH
    /// - Verifies: R_IOS_TESTED, R_IOS_HONEST
    @Test func foreignPushUpdatesState() async throws {
        let r = Rig()
        try await r.s.ensureConnected(timeout: .seconds(10))
        var updates = await r.s.updates().makeAsyncIterator()
        await r.fake.pushForeign(char: Fx.camping, bytes: Fx.campingOn)
        try await until("pushed State 1") { await r.s.state.fields["campingmode"]?["State"] == 1 }
        #expect(await r.s.state.interpreted["campingmode"]?["master_on"] == .bool(true))
        var sawPush = false
        while let (st, _) = await updates.next() {
            if st.fields["campingmode"]?["State"] == 1 { sawPush = true; break }
        }
        #expect(sawPush)
    }

    /// - Test: T_IOS_SESSION_BACKGROUND_HOLD
    /// - Verifies: R_IOS_TESTED, R_IOS_BACKGROUND_LINK
    @Test func holdsInBackgroundOnlyWhileCamping() async throws {
        let r = Rig()
        #expect(await r.s.holdsInBackground == false)  // no frame yet
        try await r.s.ensureConnected(timeout: .seconds(10))
        #expect(await r.s.holdsInBackground == false)
        await r.fake.pushForeign(char: Fx.camping, bytes: Fx.campingOn)
        try await until("camping on") { await r.s.holdsInBackground }
        await r.fake.pushForeign(char: Fx.camping, bytes: Fx.campingOff)
        try await until("camping off") { await !r.s.holdsInBackground }
    }

    /// - Test: T_IOS_SESSION_HEARTBEAT
    /// - Verifies: R_IOS_TESTED, R_IOS_BLE_DIRECT
    @Test func heartbeatRunsWhileUpAndStopsOnDisconnect() async throws {
        let r = Rig()
        #expect(await r.s.heartbeatBeating == false)
        #expect(await r.s.sinceFirstBeat == nil)
        try await r.s.ensureConnected(timeout: .seconds(10))
        try await until("first beat") { await r.heartbeats().count == 1 }
        #expect(await r.heartbeats() == [[0x00, 0x10, 0x00, 0x00]])
        #expect(await r.s.heartbeatBeating)
        try await until("heartbeat sleeper") { r.clock.sleeperCount == 1 }
        r.clock.advance(by: .milliseconds(600))
        try await until("second beat") { await r.heartbeats().count == 2 }
        #expect(await r.heartbeats().last == [0x00, 0x10, 0x00, 0x01])
        #expect(await r.s.sinceFirstBeat == .milliseconds(600))

        await r.fake.dropLink()  // camping off: no reconnect
        try await until("beat stopped") { await !r.s.heartbeatBeating }
        #expect(await r.s.sinceFirstBeat == nil)
        try await until("heartbeat sleeper gone") { r.clock.sleeperCount == 0 }
        for _ in 0..<5 { r.clock.advance(by: .milliseconds(600)); await Task.yield() }
        #expect(r.clock.sleeperCount == 0)
        #expect(await r.heartbeats().count == 2)
        #expect(await r.s.link == .off)
    }

    /// A semantics.js failure keeps the raw fields, leaves the interpretation out and records why.
    /// - Test: T_IOS_SESSION_SEMANTICS_ERROR
    /// - Verifies: R_IOS_TESTED, R_IOS_HONEST
    @Test func semanticsErrorIsSurfacedNotEmpty() async throws {
        let broken = Semantics(scripts: [("""
            function interpret(f, d) { if (f === "general") throw new Error("boom"); return {installed: true}; }
            function applySwCorrections(s) { return s; }
            """, nil)])
        let r = Rig(semantics: broken)
        try await r.s.ensureConnected(timeout: .seconds(10))
        let st = await r.s.state
        #expect(st.fields["general"]?["CommunicationVersion"] == 2)
        #expect(st.gate == .supported)
        #expect(st.interpreted["general"] == nil)
        #expect(st.interpreted["campingmode"] == ["installed": .bool(true)])
        #expect(st.semanticsErrors["general"]?.contains("boom") == true)
    }

    /// A first pairing keeps the caller waiting within its own (long) budget while the passkey
    /// dialog is open; the typed passkey then brings the link up.
    ///
    /// - Test: T_IOS_SESSION_PAIRING_WAITS
    /// - Verifies: R_IOS_PAIRING
    @Test func firstPairingKeepsTheConnectWaiting() async throws {
        let r = Rig()
        await r.fake.pairNextConnect()
        let call = Task { try await r.s.ensureConnected(timeout: .seconds(90)) }
        try await until("pairing") { await r.s.link == .pairing }
        r.clock.advance(by: .seconds(30))  // the user types; well past the 20 s redial budget
        try await Task.sleep(for: .milliseconds(20))
        #expect(await r.s.link == .pairing)
        await r.fake.completePairing()
        try await call.value
        #expect(await r.s.isReady)
    }

    /// The caller's deadline ends a pairing that does not finish: the transport is cancelled.
    ///
    /// - Test: T_IOS_SESSION_PAIRING_DEADLINE
    /// - Verifies: R_IOS_PAIRING
    @Test func pairingDeadlineCancelsTheTransport() async throws {
        let r = Rig()
        await r.fake.pairNextConnect()
        let call = Task { try await r.s.ensureConnected(timeout: .seconds(90)) }
        try await until("pairing") { await r.s.link == .pairing }
        try await until("deadline timer") { r.clock.deadlines.contains(.seconds(90)) }
        r.clock.advance(by: .seconds(90))
        await #expect(throws: SessionError.unreachable) { try await call.value }
        try await until("cancelled") { await r.fake.cancels >= 1 }
        #expect(await r.s.link != .pairing)
        await r.fake.completePairing()  // too late: the fake dropped the pairing on cancel
        #expect(await r.s.isReady == false)
    }

    /// A saved unit with a stale bond: `.pairingRequired` fails the connect at once, as before.
    ///
    /// - Test: T_IOS_SESSION_STALE_BOND
    /// - Verifies: R_IOS_PAIRING
    @Test func staleBondFailsTheConnect() async throws {
        let r = Rig()
        await r.fake.requirePairingOnNextConnect()
        await #expect(throws: SessionError.unreachable) { try await r.s.ensureConnected(timeout: .seconds(90)) }
        #expect(await r.s.link == .unreachable("The camper unit asks for pairing."))
        try await until("transport cancelled") { await r.fake.cancels >= 1 }
    }

    /// - Test: T_IOS_SESSION_HEARTBEAT_REJECTED
    /// - Verifies: R_IOS_TESTED, R_IOS_HONEST
    @Test func rejectedHeartbeatDropsTheLinkAndNeverArms() async throws {
        let r = Rig()
        await r.fake.rejectNextWrite()  // the first write is the first beat
        await #expect(throws: SessionError.unreachable) { try await r.s.ensureConnected(timeout: .seconds(10)) }
        try await until("link dropped") { await r.s.link != .up }
        #expect(await r.s.sinceFirstBeat == nil)
        #expect(await r.s.heartbeatBeating == false)
        #expect(await r.s.isReady == false)
        #expect(await r.t.cancels == 1)
        #expect(await r.heartbeats().count == 1)
    }

    /// - Test: T_IOS_SESSION_SUBSCRIBE_FAILS
    /// - Verifies: R_IOS_TESTED, R_IOS_HONEST
    @Test func subscribeFailureDropsTheLinkAndTheNextCallReads() async throws {
        let r = Rig()
        await r.t.set(failSubscribe: true)
        await #expect(throws: SessionError.unreachable) { try await r.s.ensureConnected(timeout: .seconds(10)) }
        #expect(await r.s.isReady == false)
        #expect(await r.t.cancels == 1)
        try await until("link down") { await r.s.link != .up }
        #expect(await r.s.state.fields["general"] == nil)

        let call = Task { try await r.s.ensureConnected(timeout: .seconds(10)) }
        try await until("cooldown + deadline sleepers") { r.clock.sleeperCount == 3 }
        r.clock.advance(by: .seconds(7))
        try await call.value
        #expect(await r.t.connects == 2)
        #expect(await r.s.isReady)
        #expect(await r.s.state.fields["general"]?["CommunicationVersion"] == 2)
    }

    /// - Test: T_IOS_SESSION_SINGLE_FLIGHT
    /// - Verifies: R_IOS_TESTED, R_IOS_HONEST
    @Test func concurrentCallersShareOneConnectAndWaitForTheRead() async throws {
        let r = Rig()
        await r.t.set(holdReads: true)
        let a = Task { try await r.s.ensureConnected(timeout: .seconds(10)); return await r.s.state.fields["general"] }
        let b = Task { try await r.s.ensureConnected(timeout: .seconds(10)); return await r.s.state.fields["general"] }
        try await until("a read waits") { await !r.t.held.isEmpty }
        #expect(await r.s.isReady == false)
        await r.t.releaseReads()
        #expect(try await a.value?["CommunicationVersion"] == 2)
        #expect(try await b.value?["CommunicationVersion"] == 2)
        #expect(await r.t.connects == 1)
    }

    /// - Test: T_IOS_SESSION_DEADLINE
    /// - Verifies: R_IOS_TESTED, R_IOS_HONEST
    @Test func hangingReadEndsAtTheTotalDeadline() async throws {
        let r = Rig()
        await r.t.set(holdReads: true)  // never released: the read hangs and ignores cancellation
        let call = Task { try await r.s.ensureConnected(timeout: .seconds(5)) }
        try await until("a read waits") { await !r.t.held.isEmpty }
        // heartbeat + the caller's deadline + the bring-up's deadline
        try await until("sleepers") { r.clock.sleeperCount == 3 }
        r.clock.advance(by: .milliseconds(4900))
        #expect(await r.s.isReady == false)
        r.clock.advance(by: .milliseconds(100))
        await #expect(throws: SessionError.unreachable) { try await call.value }
        try await until("link dropped") { await r.s.link != .up }
        #expect(await r.t.cancels == 1)
        await r.t.releaseReads()
    }

    /// A hang-up while a command runs is not the power-save rest (session.c `!cali_ctl_busy()`).
    /// - Test: T_IOS_SESSION_COMMAND_NO_REST
    /// - Verifies: R_IOS_TESTED, R_IOS_POWER_SAVE
    @Test func hangUpDuringCommandDoesNotRest() async throws {
        let r = Rig()
        try await r.s.ensureConnected(timeout: .seconds(10))
        await r.s.beginCommand()
        await r.fake.hangUp(after: .seconds(30))
        try await until("heartbeat + hang-up sleepers") { r.clock.sleeperCount >= 2 }
        r.clock.advance(by: .seconds(30))
        try await until("reconnect scheduled") { await r.s.pendingReconnect != nil }
        if case .rest = await r.s.link { Issue.record("rest during a command") }
        await r.s.endCommand()
    }
}

/// The store path: the water stale-latch guard and the lamp-frame merge.
struct StoreGuardTests {
    /// A parked unit serves the stale 1 L latch: it never replaces a real level (ports.c, #274).
    /// - Test: T_IOS_WATER_LATCH_HELD
    /// - Verifies: R_IOS_TESTED, R_IOS_HONEST, R_IOS_FUNCTIONS
    @Test func staleLatchDoesNotReplaceARealLevel() async throws {
        let r = Rig(Fx.frames().merging([Fx.water: Fx.waterFrame(fresh: 17)]) { $1 })
        try await r.s.ensureConnected(timeout: .seconds(10))
        // Cold start: a first reading waits for 5 s of the same value before it is shown.
        #expect(await r.s.state.fields["water"] == nil)
        r.clock.advance(by: .seconds(6))
        try await r.s.readAll()
        var st = await r.s.state
        #expect(st.fields["water"]?["FreshWaterLevel"] == 17)
        #expect(st.waterHeld == false && st.waterGoodAt != nil)

        // The latch, from a read and from a push: the 17 L stays and is marked held.
        await r.fake.seed(Fx.water, Fx.waterFrame(fresh: 1))
        try await r.s.readAll()
        try await until("held push") { await r.s.state.waterHeld }
        st = await r.s.state
        #expect(st.fields["water"]?["FreshWaterLevel"] == 17)
        #expect(st.waterHeld)
        #expect(st.interpreted["water"] != nil)

        // The latch alone never turns into the level, however long it lasts.
        r.clock.advance(by: .seconds(60))
        try await r.s.readAll()
        #expect(await r.s.state.fields["water"]?["FreshWaterLevel"] == 17)

        // A real measurement: the same new level for 5 s is adopted.
        await r.fake.seed(Fx.water, Fx.waterFrame(fresh: 15))
        try await r.s.readAll()
        #expect(await r.s.state.fields["water"]?["FreshWaterLevel"] == 17)  // first sighting: held
        r.clock.advance(by: .seconds(6))
        try await r.s.readAll()
        st = await r.s.state
        #expect(st.fields["water"]?["FreshWaterLevel"] == 15)
        #expect(st.waterHeld == false)
    }

    /// With no baseline the latch is stored as nothing, not as a fake 1 L.
    /// - Test: T_IOS_WATER_COLD_LATCH
    /// - Verifies: R_IOS_TESTED, R_IOS_HONEST
    @Test func coldStartLatchStoresNothing() async throws {
        let r = Rig(Fx.frames().merging([Fx.water: Fx.waterFrame(fresh: 1)]) { $1 })
        try await r.s.ensureConnected(timeout: .seconds(10))
        let st = await r.s.state
        #expect(st.fields["water"] == nil && st.interpreted["water"] == nil)
        #expect(st.waterHeld)
    }

    /// A config or ack 1502 frame feeds the latch but keeps the stored lamps (#284, session.c `keep`).
    /// - Test: T_IOS_LAMP_MERGE
    /// - Verifies: R_IOS_TESTED, R_IOS_HONEST, R_IOS_FUNCTIONS
    @Test func configFrameKeepsTheStoredLamps() async throws {
        let lamps = Fx.lights(pn: 9, mode: 4)
        let r = Rig(Fx.frames().merging([Fx.lighting: lamps]) { $1 })
        try await r.s.ensureConnected(timeout: .seconds(10))
        let before = await r.s.state
        #expect(before.fields["lighting"]?["Mode"] == 4)

        await r.fake.pushForeign(char: Fx.lighting, bytes: Fx.wakeup)  // Mode 20
        try await until("latched") { await r.s.state.lightConfig["WakeupTimestamp"] != nil }
        await r.fake.pushForeign(char: Fx.lighting, bytes: Fx.lights(pn: 0, mode: 12))  // REQUEST_CONFIG reply
        try await Task.sleep(for: .milliseconds(50))
        let after = await r.s.state
        #expect(after.fields["lighting"] == before.fields["lighting"])
        #expect(after.interpreted["lighting"] == before.interpreted["lighting"])
        #expect(after.lightConfig["WakeupLightValue"] == 0x0101)

        // A later lamp-state frame replaces them.
        await r.fake.pushForeign(char: Fx.lighting, bytes: Fx.lights(pn: 9, mode: 4).enumerated().map { $0 == 8 ? 0x33 : $1 })
        try await until("new lamps") { await r.s.state.fields["lighting"]?["BrightnessLOne"] == 3 }
    }

    /// With no lamp-state frame stored, a config frame is stored as it is.
    /// - Test: T_IOS_LAMP_MERGE_EMPTY
    /// - Verifies: R_IOS_TESTED, R_IOS_HONEST
    @Test func configFrameIsStoredWhenNoLampsAreKnown() async throws {
        let r = Rig(Fx.frames().merging([Fx.lighting: Fx.wakeup]) { $1 })
        try await r.s.ensureConnected(timeout: .seconds(10))
        #expect(await r.s.state.fields["lighting"]?["Mode"] == 20)
    }
}
