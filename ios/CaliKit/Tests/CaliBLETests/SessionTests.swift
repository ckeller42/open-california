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
    static let campingOff: [UInt8] = [0x20]
    static let campingOn: [UInt8] = [0x21]
    static let noShore: [UInt8] = [0, 0, 0, 0, 0x00]
    static let shore: [UInt8] = [0, 0, 0, 0, 0x01]

    static func frames(camping: [UInt8] = campingOff, energy e: [UInt8] = noShore) -> [UInt16: [UInt8]] {
        [general: generalV2, self.camping: camping, energy: e]
    }
}

/// Counts `connect()` calls; everything else goes to the fake.
private actor Counting: CamperTransport {
    let inner: FakeCamperTransport
    nonisolated let events: AsyncStream<TransportEvent>
    var connects = 0
    init(_ inner: FakeCamperTransport) { self.inner = inner; events = inner.events }
    func connect() async { connects += 1; await inner.connect() }
    func cancel() async { await inner.cancel() }
    func read(_ char: UInt16) async throws -> [UInt8] { try await inner.read(char) }
    func write(_ char: UInt16, _ bytes: [UInt8]) async throws { try await inner.write(char, bytes) }
    func subscribe(_ chars: [UInt16]) async throws { try await inner.subscribe(chars) }
}

/// Polls `cond` in real time (the session works on its own tasks) for up to 2 s.
private func until(_ what: String, _ cond: () async -> Bool) async throws {
    for _ in 0..<2000 {
        if await cond() { return }
        try await Task.sleep(for: .milliseconds(1))
    }
    Issue.record("timed out waiting for \(what)")
}

/// Gives the session's tasks time to act on an advance that must change nothing.
private func settle() async throws { try await Task.sleep(for: .milliseconds(30)) }

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
        for _ in 0..<60 { r.clock.advance(by: .seconds(1)); await Task.yield() }
        try await settle()
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
        try await until("cooldown sleeper") { r.clock.sleeperCount == 1 }
        r.clock.advance(by: .milliseconds(6900))
        try await settle()
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
            try await settle()
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
        for _ in 0..<5 { r.clock.advance(by: .milliseconds(600)); await Task.yield() }
        try await settle()
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
}
