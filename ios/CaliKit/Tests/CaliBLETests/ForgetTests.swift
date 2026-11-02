import Foundation
import Testing

@testable import CaliBLE
@testable import CaliCore

private let general = CamperGATT.stateChars["general"]!
private let cooler = CamperGATT.stateChars["cooler"]!
private let coolerOff: [UInt8] = [0x08, 0x43, 0, 0, 0, 0, 0, 0]

private struct ForgetRig {
    let clock = TestClock()
    let fake: FakeCamperTransport
    let s: CamperSession

    init() {
        fake = FakeCamperTransport(clock: clock, frames: [general: [0, 0, 0, 0, 0, 0, 0, 0, 2], cooler: coolerOff])
        s = CamperSession(transport: fake, clock: clock)
    }
}

/// Polls `cond` in real time (the session works on its own tasks) for up to 2 s.
private func until(_ what: String, _ cond: () async -> Bool) async throws {
    for _ in 0..<2000 {
        if await cond() { return }
        try await Task.sleep(for: .milliseconds(1))
    }
    Issue.record("timed out waiting for \(what)")
}

/// "Forget this camper unit": the app's own data goes, the iOS bond stays.
struct ForgetTests {
    /// A forget ends the link and the saved unit: no redial, no write, and a connect says "not paired".
    /// - Test: T_IOS_FORGET_ENDS_LINK
    /// - Verifies: R_IOS_PAIRING, R_IOS_HONEST
    @Test func forgetEndsTheLinkAndTheSavedUnit() async throws {
        let r = ForgetRig()
        try await r.s.ensureConnected(timeout: .seconds(10))
        try await until("first beat") { await r.s.sinceFirstBeat != nil }
        #expect(await r.s.state.asOf != nil)

        await r.s.forgetUnit()
        try await until("the link ended") { await r.fake.cancels >= 1 }
        #expect(await !r.s.heartbeatBeating)
        let writes = await r.fake.writes.count
        #expect(await r.s.link == .unreachable(CamperSession.notPaired))
        #expect(await r.fake.forgets == 1)
        let state = await r.s.state
        #expect(state.fields.isEmpty && state.asOf == nil)
        #expect(await r.s.pendingReconnect == nil)

        await #expect(throws: SessionError.notPaired) { try await r.s.ensureConnected(timeout: .seconds(10)) }
        #expect(await r.fake.writes.count == writes)
        #expect(await r.s.link == .unreachable(CamperSession.notPaired))
    }

    /// A command that waits for the arm delay when the unit is forgotten writes nothing.
    /// - Test: T_IOS_FORGET_DURING_COMMAND
    /// - Verifies: R_IOS_PAIRING, R_IOS_HONEST
    @Test func forgetDuringACommandWritesNothingAfter() async throws {
        let r = ForgetRig()
        let cmd = Command(function: "cooler", what: "power", value: "on")
        let run = Task { await r.s.run(cmd) }
        try await until("the arm wait") { r.clock.deadlines.contains(CamperGATT.armDelay) }
        let before = await r.fake.writes.count

        await r.s.forgetUnit()
        r.clock.advance(by: CamperGATT.armDelay)
        let result = await run.value
        #expect(result == .failed(.linkEnded))
        let after = await r.fake.writes
        #expect(after.count == before)
        #expect(after.allSatisfy { $0.char == CamperGATT.heartbeat })
        #expect(await r.s.link == .unreachable(CamperSession.notPaired))
    }

    /// A first pairing after a forget works as on a fresh install: the pick connects and the session is ready.
    /// - Test: T_IOS_FORGET_THEN_PAIR
    /// - Verifies: R_IOS_PAIRING
    @Test func pairingAgainAfterAForgetEndsAsAlreadyPaired() async throws {
        let r = ForgetRig()
        try await r.s.ensureConnected(timeout: .seconds(10))
        await r.s.forgetUnit()
        let unit = UUID()
        let outcome = try await r.s.firstPairing(with: unit)
        #expect(outcome == .alreadyPaired)
        #expect(await r.fake.picks == [unit])
        #expect(await r.s.isReady)
    }
}
