import Foundation
import Testing

@testable import CaliBLE
@testable import CaliCore

/// The van's 1004 frame (real-unit baseline 0410): CarVariant 0 = California T7.
private let vehicleT7: [UInt8] = [0x04, 0x7e, 0x07, 0x1c, 0x12, 0x26, 0x33, 0x00, 0x00, 0x00, 0x00]

/// general (CommunicationVersion 2), camping mode and energy, as in SessionTests; no 1004 unless added.
private func frames(campingOn: Bool = false) -> [UInt16: [UInt8]] {
    [
        CamperGATT.stateChars["general"]!: [0, 0, 0, 0, 0, 0, 0, 0, 2],
        CamperGATT.stateChars["campingmode"]!: [campingOn ? 0x21 : 0x20],
        CamperGATT.stateChars["energy"]!: [0, 0, 0, 0, 0x00],
    ]
}

private struct VariantRig {
    let clock = TestClock()
    let fake: FakeCamperTransport
    let s: CamperSession

    init(_ frames: [UInt16: [UInt8]]) {
        fake = FakeCamperTransport(clock: clock, frames: frames)
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

/// The per-variant subscribe gate on a link (the app's and the satellite's `cali_variant_has`).
struct SessionVariantTests {
    /// The session reads 1004 first and then subscribes and reads only the functions of the unit's
    /// variant (a T7 here: the van's own 1004 frame starts with CarVariant 0), in the one fixed order.
    /// - Test: T_IOS_SESSION_VARIANT_GATE
    /// - Verifies: R_IOS_TESTED, R_IOS_BLE_DIRECT
    @Test func aT7SessionSubscribesOnlyItsFunctionsInOrder() async throws {
        var frames = frames()
        frames[CamperGATT.auth] = vehicleT7
        let r = VariantRig(frames)
        try await r.s.ensureConnected(timeout: .seconds(10))
        let t7 = CamperGATT.stateChars(forVariant: 0).map(\.char)
        #expect(await r.fake.reads.first == CamperGATT.auth)
        #expect(await r.fake.subscribeCalls.last == t7)
        for gc in ["satelliteantenna", "stairs", "livingroomheater", "roofaircondition"] {
            #expect(await !r.fake.reads.contains(CamperGATT.stateChars[gc]!), "\(gc)")
        }
        #expect(await r.s.state.fields["general"]?["CommunicationVersion"] == 2)
    }

    /// Two links subscribe in the same order, so two traces compare line by line.
    /// - Test: T_IOS_SESSION_VARIANT_ORDER
    /// - Verifies: R_IOS_TESTED, R_IOS_BLE_DIRECT
    @Test func twoConnectsSubscribeInTheSameOrder() async throws {
        var frames = frames(campingOn: true)  // camping on: a lost link is redialled
        frames[CamperGATT.auth] = vehicleT7
        let r = VariantRig(frames)
        try await r.s.ensureConnected(timeout: .seconds(10))
        await r.fake.dropLink()
        try await until("redial in 7 s") { await r.s.pendingReconnect == .seconds(7) }
        try await until("one sleeper") { r.clock.sleeperCount == 1 }
        r.clock.advance(by: .seconds(7))
        try await until("the second link subscribed") { await r.fake.subscribeCalls.count >= 2 }
        let calls = await r.fake.subscribeCalls
        #expect(calls.first == calls.last)
    }

    /// No 1004 frame (its read fails): the variant is unknown, so every function, never none.
    /// - Test: T_IOS_SESSION_VARIANT_UNKNOWN
    /// - Verifies: R_IOS_TESTED, R_IOS_BLE_DIRECT
    @Test func aFailed1004ReadSubscribesEverything() async throws {
        let r = VariantRig(frames())
        try await r.s.ensureConnected(timeout: .seconds(10))
        #expect(await r.fake.subscribeCalls.last == CamperGATT.stateChars(forVariant: nil).map(\.char))
    }
}
