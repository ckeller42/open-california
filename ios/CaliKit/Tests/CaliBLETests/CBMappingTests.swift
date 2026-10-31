import CaliCore
import CoreBluetooth
import Testing

@testable import CaliBLE

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
        let unit = UUID()
        let other = UUID()
        var p = PeerPolicy(saved: unit)
        #expect(p.route() == .saved(unit))
        let r1 = p.authenticated(unit)
        #expect(r1 == .up)  // no pairing error needed
        let r2 = p.authenticated(other)
        #expect(r2 == .reject)
        let r3 = p.pairingError()
        #expect(!r3)  // a stale bond of the saved unit ends the link
        #expect(PeerPolicy(saved: nil).route() == .refuse)
        var none = PeerPolicy(saved: nil)
        let r4 = none.authenticated(other)
        #expect(r4 == .reject)  // no user action: never adopt
        #expect(none.saved == nil)
    }

    /// Lenient adoption, a seen pairing: an authentication error, then a success.
    ///
    /// - Test: T_IOS_CB_PEER_ADOPT
    /// - Verifies: R_IOS_PAIRING
    @Test func adoptsAfterASeenPairing() {
        let old = UUID()
        let new = UUID()
        var p = PeerPolicy(saved: old)
        p.begin(new)
        #expect(p.route() == .chosen(new))
        let e = p.pairingError()
        #expect(e)  // iOS shows the passkey dialog; read again
        let r1 = p.authenticated(new)
        #expect(r1 == .up)
        #expect(p.saved == new)
        #expect(p.route() == .saved(new))
        let r2 = p.authenticated(old)
        #expect(r2 == .reject)  // the old unit is not taken back silently
    }

    /// Lenient adoption, a shared bond: the iPhone is already paired with the unit (for example
    /// through the vendor app), so the first read succeeds at once. The picked unit is adopted.
    ///
    /// - Test: T_IOS_CB_PEER_SHARED_BOND
    /// - Verifies: R_IOS_PAIRING, AD_IOS_SHARED_BOND
    @Test func existingBondIsAdoptedDuringAPick() {
        let old = UUID()
        let new = UUID()
        var p = PeerPolicy(saved: old)
        p.begin(new)
        let r = p.authenticated(new)
        #expect(r == .up)
        #expect(p.saved == new)
        #expect(p.route() == .saved(new))  // one-shot: the pick is over
    }

    /// Outside a pick, an authenticated read never adopts: not in the background without a saved
    /// unit, and not another unit next to the saved one.
    ///
    /// - Test: T_IOS_CB_PEER_NO_BACKGROUND_ADOPT
    /// - Verifies: R_IOS_PAIRING
    @Test func noAdoptionOutsideAPick() {
        let unit = UUID()
        let other = UUID()
        var none = PeerPolicy(saved: nil)
        let r1 = none.authenticated(unit)
        #expect(r1 == .reject)
        #expect(none.saved == nil)
        #expect(none.route() == .refuse)
        var p = PeerPolicy(saved: unit)
        let r2 = p.authenticated(other)
        #expect(r2 == .reject)
        #expect(p.saved == unit)
    }

    /// The first pairing is one-shot: a link that ends without an adoption (a failed read, a missing
    /// `1004`, a cancel or a timeout) falls back to the saved unit, or to nothing. An adoption also
    /// uses the pick up: a second unit needs a second pick. A reconnect to the saved unit verifies
    /// the authenticated `1004` read again.
    ///
    /// - Test: T_IOS_CB_PEER_ONE_SHOT
    /// - Verifies: R_IOS_PAIRING
    @Test func firstPairingIsOneShot() {
        let old = UUID()
        let new = UUID()
        let other = UUID()
        var p = PeerPolicy(saved: old)
        p.begin(new)
        _ = p.pairingError()
        p.linkEnded()
        #expect(p.route() == .saved(old))
        let r0 = p.authenticated(new)
        #expect(r0 == .reject)  // the pick ended with the link
        #expect(p.saved == old)

        var q = PeerPolicy(saved: nil)
        q.begin(new)
        _ = q.pairingError()
        let r1 = q.authenticated(other)
        #expect(r1 == .reject)  // not the picked unit
        #expect(q.route() == .refuse)
        #expect(q.saved == nil)

        var a = PeerPolicy(saved: nil)
        a.begin(new)
        let r2 = a.authenticated(new)
        #expect(r2 == .up)
        #expect(a.route() == .saved(new))
        let r3 = a.authenticated(other)
        #expect(r3 == .reject)  // the pick is used up
        #expect(a.saved == new)
        a.begin(other)  // a second pick
        let r4 = a.authenticated(other)
        #expect(r4 == .up)
        #expect(a.saved == other)

        // Reconnect: the saved unit is up only on its authenticated read; a failed one ends the link.
        #expect(a.route() == .saved(other))
        let stale = a.pairingError()
        #expect(!stale)
        let r5 = a.authenticated(other)
        #expect(r5 == .up)
    }

    /// A pick that ends without a successful pairing leaves nothing behind: a later connect (an
    /// automatic redial) goes to the saved unit, verifies it as before and never adopts the pick.
    /// A cancel ends a pick that still waits for its connect.
    ///
    /// - Test: T_IOS_CB_PICK_EXPIRES
    /// - Verifies: R_IOS_PAIRING
    @Test func pickWithoutPairingDoesNotAdoptLater() {
        let old = UUID()
        let new = UUID()
        var p = PeerPolicy(saved: old)
        p.begin(new)
        let e = p.pairingError()
        #expect(e)
        p.linkEnded()  // the dialog was cancelled, the link timed out or the budget ran out
        #expect(p.route() == .saved(old))
        let r1 = p.authenticated(new)
        #expect(r1 == .reject)
        let r2 = p.authenticated(old)
        #expect(r2 == .up)
        #expect(p.saved == old)

        var c = PeerPolicy(saved: old)
        c.pick(new)
        c.cancelPick()
        c.linkEnded()
        #expect(c.startPick() == nil)
        #expect(c.route() == .saved(old))
    }

    /// A pick while the saved unit's link is up waits until that link has ended; until then no link
    /// is verified (the saved unit is not reported as the new one), and the pick does not route.
    /// Once the old link has ended the pick connects, and only the picked unit is adopted.
    ///
    /// - Test: T_IOS_CB_PICK_REPLACES_LINK
    /// - Verifies: R_IOS_PAIRING
    @Test func pickWhileSavedLinkIsUpWaitsForItsOwnLink() {
        let old = UUID()
        let new = UUID()
        var p = PeerPolicy(saved: old)
        p.pick(new)
        #expect(p.route() == .saved(old))  // nothing dials for the pick before the old link ended
        let late = p.authenticated(old)
        #expect(late == .reject)
        #expect(p.saved == old)
        p.linkEnded()  // the old link's end keeps the pick
        #expect(p.startPick() == new)
        #expect(p.route() == .chosen(new))
        let r = p.authenticated(new)
        #expect(r == .up)
        #expect(p.saved == new)
        #expect(p.route() == .saved(new))
    }

    /// A pick ends with the pairing budget, counted from the pick: it can no longer start a connect,
    /// and `expire` ends a running one. Before the deadline nothing expires.
    ///
    /// - Test: T_IOS_CB_PICK_DEADLINE
    /// - Verifies: R_IOS_PAIRING
    @Test func pickExpiresWithItsBudget() {
        let old = UUID()
        let new = UUID()
        let t0 = ContinuousClock.now
        var p = PeerPolicy(saved: old)
        p.pick(new, now: t0)
        let early = p.expire(now: t0.advanced(by: .seconds(30)))
        #expect(!early)
        let late = p.startPick(now: t0.advanced(by: PeerPolicy.pairingBudget))
        #expect(late == nil)  // too late for its connect
        #expect(!p.inPick)
        #expect(p.route() == .saved(old))

        var q = PeerPolicy(saved: old)
        q.pick(new, now: t0)
        let started = q.startPick(now: t0.advanced(by: .seconds(1)))
        #expect(started == new)
        let ended = q.expire(now: t0.advanced(by: PeerPolicy.pairingBudget))
        #expect(ended)
        #expect(!q.inPick)
        #expect(q.route() == .saved(old))
        let r = q.authenticated(new)
        #expect(r == .reject)
    }

    /// Bluetooth goes away while a pick waits for the old link to end: the transport cancels the
    /// pick after the teardown, so the next "powered on" connects to the saved unit, not the pick.
    ///
    /// - Test: T_IOS_CB_PICK_POWER_LOSS
    /// - Verifies: R_IOS_PAIRING
    @Test func powerLossEndsAPendingPick() {
        let old = UUID()
        let new = UUID()
        var p = PeerPolicy(saved: old)
        p.pick(new)
        p.linkEnded()  // tearDown of the old link
        p.cancelPick()  // the not-powered-on path
        let none = p.startPick()
        #expect(none == nil)
        #expect(p.route() == .saved(old))
    }

    /// The re-reads of a first pairing stop at a cap of their own.
    ///
    /// - Test: T_IOS_CB_PAIRING_READ_CAP
    /// - Verifies: R_IOS_PAIRING
    @Test func pairingReadsAreCapped() {
        var p = PeerPolicy(saved: nil)
        p.begin(UUID())
        for _ in 0..<PeerPolicy.maxPairingReads {
            let ok = p.pairingError()
            #expect(ok)
        }
        let more = p.pairingError()
        #expect(!more)
        p.linkEnded()
        p.begin(UUID())
        let again = p.pairingError()
        #expect(again)  // a new pick starts a new count
    }

    /// Only the exact advertised name, one entry per unit, strongest first.
    ///
    /// - Test: T_IOS_CB_CANDIDATES
    /// - Verifies: R_IOS_PAIRING
    @Test func pairingCandidates() {
        let a = UUID()
        let b = UUID()
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
        let write = Task {
            try await withCheckedThrowingContinuation { (k: CheckedContinuation<Void, any Error>) in
                ops.v.addWrite(0x1203, k)
                ops.issued.append(true)
            }
        }
        let read = Task { try await withCheckedThrowingContinuation { k in ops.issued.append(ops.v.addRead(0x1202, k)) } }
        let sub = Task {
            try await withCheckedThrowingContinuation { (k: CheckedContinuation<Void, any Error>) in
                ops.v.addSubscribe([0x1202], k)
                ops.issued.append(true)
            }
        }
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
        let a = Task {
            try await withCheckedThrowingContinuation { (k: CheckedContinuation<Void, any Error>) in
                ops.v.addWrite(0x1003, k)
                ops.issued.append(true)
            }
        }
        try await ops.until { $0.issued.count == 1 }
        let b = Task {
            try await withCheckedThrowingContinuation { (k: CheckedContinuation<Void, any Error>) in
                ops.v.addWrite(0x1003, k)
                ops.issued.append(true)
            }
        }
        try await ops.until { $0.issued.count == 2 }
        ops.v.finishWrite(0x1003, .success(()))
        ops.v.finishWrite(0x1003, .failure(TransportError.writeRejected))
        try await a.value
        await #expect(throws: TransportError.writeRejected) { try await b.value }

        let sub = Task {
            try await withCheckedThrowingContinuation { (k: CheckedContinuation<Void, any Error>) in
                ops.v.addSubscribe([0x1202, 0x1502], k)
                ops.issued.append(true)
            }
        }
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
        _modify {
            lock.lock()
            defer { lock.unlock() }
            yield &ops
        }
    }
    var issued: [Bool] {
        get { lock.withLock { _issued } }
        _modify {
            lock.lock()
            defer { lock.unlock() }
            yield &_issued
        }
    }

    /// Waits until the tasks registered their continuations.
    func until(_ ok: (Box) -> Bool) async throws {
        while !ok(self) { try await Task.sleep(for: .milliseconds(1)) }
    }
}

extension PeerPolicy {
    /// A pick with no link up: its connect starts at once.
    mutating func begin(_ id: UUID) {
        pick(id)
        _ = startPick()
    }
}
