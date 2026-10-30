import Testing
@testable import CaliBLE
import CaliCore

struct FakeTransportTests {
    static let general: UInt16 = CamperGATT.stateChars["general"]!

    /// A frame from the codec vectors would also do; the fake only stores bytes.
    static func connected(clock: any Clock<Duration> = ContinuousClock()) async -> FakeCamperTransport {
        let t = FakeCamperTransport(clock: clock, frames: [general: [0xC0, 0x01]])
        await t.connect()
        return t
    }

    static func next(_ it: inout AsyncStream<TransportEvent>.Iterator) async -> TransportEvent? { await it.next() }

    /// - Test: T_IOS_FAKE_READ
    /// - Verifies: R_IOS_TESTED, R_IOS_BLE_DIRECT
    @Test func readReturnsSeededFrame() async throws {
        let t = await Self.connected()
        #expect(try await t.read(Self.general) == [0xC0, 0x01])
        await #expect(throws: TransportError.noSuchChar(0x9999)) { try await t.read(0x9999) }
    }

    /// - Test: T_IOS_FAKE_WRITES
    /// - Verifies: R_IOS_TESTED, R_IOS_BLE_DIRECT
    @Test func writeIsRecordedInOrder() async throws {
        let t = await Self.connected()
        try await t.write(0x1003, [0, 0x10, 0, 0])
        try await t.write(0x1202, [1])
        await t.rejectNextWrite()
        await #expect(throws: TransportError.writeRejected) { try await t.write(0x1202, [2]) }
        try await t.write(0x1202, [3])
        #expect(await t.writes.map(\.bytes) == [[0, 0x10, 0, 0], [1], [2], [3]])
        var it = t.events.makeAsyncIterator()
        var acks: [Bool] = []
        while acks.count < 4, let e = await it.next() { if case .written(_, let ok) = e { acks.append(ok) } }
        #expect(acks == [true, true, false, true])
    }

    /// - Test: T_IOS_FAKE_HANGUP
    /// - Verifies: R_IOS_TESTED, R_IOS_POWER_SAVE
    @Test func hangUpEmitsRemoteHangUp() async throws {
        let clock = TestClock()
        let t = await Self.connected(clock: clock)
        await t.hangUp(after: .seconds(30))
        while clock.sleeperCount == 0 { await Task.yield() }
        clock.advance(by: .seconds(29))
        await Task.yield()
        await #expect(throws: Never.self) { try await t.write(0x1202, [1]) }  // still up
        clock.advance(by: .seconds(1))
        var it = t.events.makeAsyncIterator()
        var last: TransportEvent?
        while let e = await it.next() { last = e; if case .disconnected = e { break } }
        guard case .disconnected(let r) = last else { Issue.record("no disconnect"); return }
        #expect(r == .remoteHangUp)
        await #expect(throws: TransportError.notConnected) { try await t.write(0x1202, [1]) }
    }

    /// - Test: T_IOS_FAKE_FOREIGN
    /// - Verifies: R_IOS_TESTED, R_IOS_BLE_DIRECT
    @Test func foreignPushArrivesAsNotified() async throws {
        let t = await Self.connected()
        await t.pushForeign(char: Self.general, bytes: [7])
        var it = t.events.makeAsyncIterator()
        var got: [UInt8]?
        while let e = await it.next() { if case .notified(Self.general, let b) = e { got = b; break } }
        #expect(got == [7])
        #expect(try await t.read(Self.general) == [7])
    }

    /// - Test: T_IOS_FAKE_UNAVAILABLE
    /// - Verifies: R_IOS_TESTED, R_IOS_BLE_DIRECT
    @Test func bluetoothOffAndConnectFailureAreReported() async {
        let t = FakeCamperTransport()
        var it = t.events.makeAsyncIterator()
        await t.setBluetooth(unavailable: .poweredOff)
        await t.connect()
        guard case .bluetoothUnavailable(.poweredOff)? = await it.next() else { Issue.record("expected poweredOff"); return }
        await t.setBluetooth(unavailable: nil)
        await t.failNextConnect()
        await t.connect()
        guard case .disconnected(.failedToConnect)? = await it.next() else { Issue.record("expected failedToConnect"); return }
    }
}
