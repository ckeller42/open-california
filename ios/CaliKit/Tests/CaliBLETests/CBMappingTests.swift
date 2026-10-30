import CoreBluetooth
import Testing
@testable import CaliBLE
import CaliCore

/// The pure parts of the CoreBluetooth transport. The simulator and `swift test` have no radio:
/// the link itself is checked with `docs/ios-app-radio-checklist.md`.
struct CBMappingTests {
    /// - Test: T_IOS_CB_DISCONNECT_MAP
    /// - Verifies: R_IOS_POWER_SAVE, R_IOS_BLE_DIRECT
    @Test func disconnectReasons() {
        // HCI 0x13 (remote user terminated) arrives as CBError.peripheralDisconnected.
        #expect(CoreBluetoothTransport.mapDisconnect(CBError(.peripheralDisconnected)) == .remoteHangUp)
        #expect(CoreBluetoothTransport.mapDisconnect(CBError(.connectionTimeout)) == .linkLost)
        #expect(CoreBluetoothTransport.mapDisconnect(CBError(.unknown)) == .linkLost)
        #expect(CoreBluetoothTransport.mapDisconnect(nil) == .localCancel)
    }

    /// - Test: T_IOS_CB_PAIRING_MAP
    /// - Verifies: R_IOS_PAIRING
    @Test func pairingErrors() {
        #expect(CoreBluetoothTransport.needsPairing(CBATTError(.insufficientAuthentication)))
        #expect(CoreBluetoothTransport.needsPairing(CBATTError(.insufficientEncryption)))
        #expect(CoreBluetoothTransport.needsPairing(CBError(.peerRemovedPairingInformation)))
        #expect(!CoreBluetoothTransport.needsPairing(CBATTError(.writeNotPermitted)))
        #expect(!CoreBluetoothTransport.needsPairing(CBError(.peripheralDisconnected)))
        #expect(!CoreBluetoothTransport.needsPairing(nil))
    }

    /// A write error is "rejected" only when the unit answered; otherwise the result is unconfirmed.
    ///
    /// - Test: T_IOS_CB_WRITE_MAP
    /// - Verifies: R_IOS_HONEST
    @Test func writeErrors() {
        #expect(CoreBluetoothTransport.mapWrite(CBATTError(.writeNotPermitted)) == .writeRejected)
        #expect(CoreBluetoothTransport.mapWrite(CBError(.connectionTimeout)) == .linkLost)
    }

    /// - Test: T_IOS_CB_AVAILABILITY_MAP
    /// - Verifies: R_IOS_BLE_DIRECT
    @Test func centralStates() {
        #expect(CoreBluetoothTransport.availability(.poweredOff) == .poweredOff)
        #expect(CoreBluetoothTransport.availability(.unauthorized) == .unauthorized)
        #expect(CoreBluetoothTransport.availability(.unsupported) == .unsupported)
        #expect(CoreBluetoothTransport.availability(.poweredOn) == nil)
        #expect(CoreBluetoothTransport.availability(.unknown) == nil)
        #expect(CoreBluetoothTransport.availability(.resetting) == nil)
    }

    /// - Test: T_IOS_CB_UUID_FAMILY
    /// - Verifies: R_IOS_BLE_DIRECT
    @Test func uuidFamily() {
        #expect(CoreBluetoothTransport.short(CBUUID(string: CamperGATT.uuid(0x1102))) == 0x1102)
        #expect(CoreBluetoothTransport.short(CBUUID(string: "0000F001-6C77-4B7D-BBF6-A5E587701F3D")) == 0xF001)
        #expect(CoreBluetoothTransport.short(CBUUID(string: "00001102-0000-1000-8000-00805F9B34FB")) == nil)
        #expect(CoreBluetoothTransport.short(CBUUID(string: "2A00")) == nil)
        let services = Set(CoreBluetoothTransport.services.compactMap(CoreBluetoothTransport.short))
        // Each char lives in its function's service (feature-availability.md: 0x1000, 0x1100, 0x1700).
        #expect(services.isSuperset(of: [0x1000, 0x1100, 0x1200, 0x1500, 0x1700, 0xF000]))
        #expect(services.allSatisfy { $0 & 0x00FF == 0 })
    }

    /// Only the adopted unit, verified by an authenticated read alone; without one, nothing until
    /// the user picks a unit.
    ///
    /// - Test: T_IOS_CB_PEER_SAVED_ONLY
    /// - Verifies: R_IOS_PAIRING, R_IOS_BLE_DIRECT
    @Test func connectsOnlyToTheAdoptedUnit() {
        let unit = UUID(), other = UUID()
        var p = PeerPolicy(saved: unit)
        #expect(p.route() == .saved(unit))
        let r1 = p.authenticated(unit); #expect(r1 == .up)  // no pairing error needed
        let r2 = p.authenticated(other); #expect(r2 == .reject)
        let r3 = p.pairingError(); #expect(!r3)  // a stale bond of the saved unit ends the link
        #expect(PeerPolicy(saved: nil).route() == .refuse)
        var none = PeerPolicy(saved: nil)
        let r4 = none.authenticated(other); #expect(r4 == .reject)  // no user action: never adopt
        #expect(none.saved == nil)
    }

    /// Strict adoption: the pairing must be seen (an authentication error, then a success).
    ///
    /// - Test: T_IOS_CB_PEER_ADOPT
    /// - Verifies: R_IOS_PAIRING
    @Test func adoptsOnlyAfterASeenPairing() {
        let old = UUID(), new = UUID()
        var p = PeerPolicy(saved: old)
        p.chosen = new
        #expect(p.route() == .chosen(new))
        let e = p.pairingError(); #expect(e)  // iOS shows the passkey dialog; read again
        let r1 = p.authenticated(new); #expect(r1 == .up)
        #expect(p.saved == new)
        #expect(p.route() == .saved(new))
        let r2 = p.authenticated(old); #expect(r2 == .reject)  // the old unit is not taken back silently
    }

    /// A first read that succeeds without a seen pairing is an existing bond: not adopted.
    ///
    /// - Test: T_IOS_CB_PEER_ALREADY_PAIRED
    /// - Verifies: R_IOS_PAIRING
    @Test func existingBondIsNotAdopted() {
        let old = UUID(), new = UUID()
        var p = PeerPolicy(saved: old)
        p.chosen = new
        let r = p.authenticated(new); #expect(r == .alreadyPaired)
        #expect(p.saved == old)
        #expect(p.route() == .saved(old))  // one-shot: the pick is over
    }

    /// The first pairing is one-shot: a link that ends without an adoption (a failed read, a missing
    /// `1004`, a cancel or a timeout) falls back to the saved unit, or to nothing; a seen pairing
    /// does not carry over to the next pick.
    ///
    /// - Test: T_IOS_CB_PEER_ONE_SHOT
    /// - Verifies: R_IOS_PAIRING
    @Test func firstPairingIsOneShot() {
        let old = UUID(), new = UUID(), other = UUID()
        var p = PeerPolicy(saved: old)
        p.chosen = new
        _ = p.pairingError()
        p.linkEnded()
        #expect(p.route() == .saved(old))
        p.chosen = new
        let r0 = p.authenticated(new); #expect(r0 == .alreadyPaired)

        var q = PeerPolicy(saved: nil)
        q.chosen = new
        _ = q.pairingError()
        let r1 = q.authenticated(other); #expect(r1 == .reject)  // not the picked unit
        #expect(q.route() == .refuse)
        #expect(q.saved == nil)
    }

    /// Only the exact advertised name, one entry per unit, strongest first.
    ///
    /// - Test: T_IOS_CB_CANDIDATES
    /// - Verifies: R_IOS_PAIRING
    @Test func pairingCandidates() {
        let a = UUID(), b = UUID()
        var l = CandidateList()
        l.saw(id: a, name: "VWCAMPER", rssi: -80)
        l.saw(id: b, name: "VWCAMPER", rssi: -60)
        l.saw(id: a, name: "VWCAMPER", rssi: -70)
        l.saw(id: UUID(), name: "VWCAMPER-X", rssi: -40)
        l.saw(id: UUID(), name: "", rssi: -40)
        #expect(l.sorted.map(\.id) == [b, a])
        #expect(l.sorted.map(\.rssi) == [-60, -70])
    }

    /// - Test: T_IOS_CB_PENDING_OPS
    /// - Verifies: R_IOS_HONEST, R_IOS_BLE_DIRECT
    @Test func readsSerializePerCharAndResumeOnce() async throws {
        let ops = Box()
        let first = Task { try await withCheckedThrowingContinuation { k in ops.issued.append(ops.v.addRead(0x1202, k)) } }
        try await ops.until { $0.issued.count == 1 }
        let second = Task { try await withCheckedThrowingContinuation { k in ops.issued.append(ops.v.addRead(0x1202, k)) } }
        try await ops.until { $0.issued.count == 2 }
        #expect(ops.issued == [true, false])  // one readValue on the air per char
        #expect(ops.v.finishRead(0x1202, .success([1])) == true)  // another waits: issue it
        #expect(try await first.value == [1])
        #expect(ops.v.finishRead(0x1202, .success([2])) == false)
        #expect(try await second.value == [2])
        // No read pending: the value is a notification.
        #expect(ops.v.finishRead(0x1202, .success([3])) == nil)
    }

    /// The link went: an issued write is `.linkLost` (the unit may have acted), a read `.notConnected`.
    ///
    /// - Test: T_IOS_CB_DRAIN
    /// - Verifies: R_IOS_HONEST, R_IOS_POWER_SAVE
    @Test func disconnectDrainsEveryContinuation() async throws {
        let ops = Box()
        let write = Task { try await withCheckedThrowingContinuation { (k: CheckedContinuation<Void, any Error>) in ops.v.addWrite(0x1203, k); ops.issued.append(true) } }
        let read = Task { try await withCheckedThrowingContinuation { k in ops.issued.append(ops.v.addRead(0x1202, k)) } }
        let sub = Task { try await withCheckedThrowingContinuation { (k: CheckedContinuation<Void, any Error>) in ops.v.addSubscribe([0x1202], k); ops.issued.append(true) } }
        try await ops.until { $0.issued.count == 3 }
        ops.v.drain()
        await #expect(throws: TransportError.linkLost) { try await write.value }
        await #expect(throws: TransportError.notConnected) { try await read.value }
        await #expect(throws: TransportError.notConnected) { try await sub.value }
        ops.v.drain()  // nothing left: a second drain resumes nothing twice
    }

    /// - Test: T_IOS_CB_WRITES_FIFO
    /// - Verifies: R_IOS_BLE_DIRECT
    @Test func writesAnswerInOrderAndSubscribeWaitsForAll() async throws {
        let ops = Box()
        let a = Task { try await withCheckedThrowingContinuation { (k: CheckedContinuation<Void, any Error>) in ops.v.addWrite(0x1003, k); ops.issued.append(true) } }
        try await ops.until { $0.issued.count == 1 }
        let b = Task { try await withCheckedThrowingContinuation { (k: CheckedContinuation<Void, any Error>) in ops.v.addWrite(0x1003, k); ops.issued.append(true) } }
        try await ops.until { $0.issued.count == 2 }
        ops.v.finishWrite(0x1003, .success(()))
        ops.v.finishWrite(0x1003, .failure(TransportError.writeRejected))
        try await a.value
        await #expect(throws: TransportError.writeRejected) { try await b.value }

        let sub = Task { try await withCheckedThrowingContinuation { (k: CheckedContinuation<Void, any Error>) in ops.v.addSubscribe([0x1202, 0x1502], k); ops.issued.append(true) } }
        try await ops.until { $0.issued.count == 3 }
        ops.v.finishSubscribe(0x1202, nil)
        ops.v.finishSubscribe(0x1502, nil)
        try await sub.value
        // An empty subscribe (no notifying char) returns at once.
        try await withCheckedThrowingContinuation { (k: CheckedContinuation<Void, any Error>) in ops.v.addSubscribe([], k) }
    }
}

/// Holds a `PendingOps` for the tests (the transport confines it to its queue; here a lock does).
private final class Box: @unchecked Sendable {
    private let lock = NSLock()
    private var ops = PendingOps()
    private var _issued: [Bool] = []
    var v: PendingOps {
        get { lock.withLock { ops } }
        _modify { lock.lock(); defer { lock.unlock() }; yield &ops }
    }
    var issued: [Bool] {
        get { lock.withLock { _issued } }
        _modify { lock.lock(); defer { lock.unlock() }; yield &_issued }
    }

    /// Waits until the tasks registered their continuations.
    func until(_ ok: (Box) -> Bool) async throws {
        while !ok(self) { try await Task.sleep(for: .milliseconds(1)) }
    }
}
