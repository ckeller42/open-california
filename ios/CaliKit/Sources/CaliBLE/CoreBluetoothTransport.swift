@preconcurrency import CoreBluetooth
import CaliCore
import Foundation

/// The link to the camper unit over CoreBluetooth.
///
/// Concurrency: every stored var below is touched only on `queue`, the serial queue CoreBluetooth
/// calls the delegate methods on. The async API hops onto `queue` before it touches state. That
/// confinement is why the class is `@unchecked Sendable`.
///
/// Bring-up: connect, discover the services of the char family, then read `1004`, the
/// authenticated read. Only its success is `.encrypted`, on every link; without `1004` or with a
/// failed read the transport ends the link (fail closed). No own timers: the session's deadline
/// calls `cancel()`.
///
/// Which unit (`PeerPolicy`): only the adopted one, by its saved identifier. Without one,
/// `connect()` reports `.pairingRequired`. The first pairing is the user's: `pairingCandidates()`
/// lists the units in range (service `1000`, name exactly `VWCAMPER`), the user picks one,
/// `beginFirstPairing(with:)` arms it for ONE connect, and it is adopted when its authenticated
/// `1004` read succeeds on that link (lenient, AD_IOS_SHARED_BOND): after a seen pairing (the first
/// read fails with an authentication error, iOS shows the passkey dialog, a later read succeeds),
/// or at once with the bond the iPhone already has (iOS keeps one bond per accessory, shared with
/// the vendor app). The real unit refuses Just Works pairing (evidence ledger, CAPTURE), so a
/// genuine new pairing always shows the iOS passkey dialog, and its code must match the camper
/// screen. A pairing prompt without a code is not the camper: the user cancels it.
///
/// Residual risk: CoreBluetooth exposes no encryption state, so a look-alike that serves `1004`
/// without encryption is adopted once the user picks it. The guard is the user's pick from the
/// candidate list (strongest first) and the exact name `VWCAMPER`.
///
/// Writes: a write that is issued and then gets no answer (the caller cancelled the link, or the
/// char does not accept the write and CoreBluetooth never answers) ends as `.linkLost` when the
/// link goes: the runner reports it as unconfirmed, the honest side.
///
/// - Requirement: R_IOS_BLE_DIRECT, R_IOS_PAIRING, R_IOS_BACKGROUND_LINK, R_IOS_POWER_SAVE, R_IOS_HONEST
public final class CoreBluetoothTransport: NSObject, CamperTransport, @unchecked Sendable {
    public let events: AsyncStream<TransportEvent>
    private let sink: AsyncStream<TransportEvent>.Continuation
    private let queue = DispatchQueue(label: "de.opencalifornia.camper.ble")
    private var central: CBCentralManager!
    private var peripheral: CBPeripheral?
    private var chars: [UInt16: CBCharacteristic] = [:]
    private var ops = PendingOps()
    private var phase = Phase.idle
    private var servicesLeft = 0
    /// +1 per link: a delayed re-read of an earlier link sends nothing.
    private var linkGen = 0
    /// State restoration handed over a connected peripheral; its bring-up waits for `poweredOn`.
    private var restoredConnected = false
    private var candidates: (list: CandidateList, k: CheckedContinuation<[PairingCandidate], any Error>)?
    private var policy = PeerPolicy(saved: UserDefaults.standard.string(forKey: peripheralKey).flatMap(UUID.init(uuidString:)))

    private enum Phase: Int, Comparable {
        case idle, waitingForPower, connecting, discovering, probing, up
        static func < (a: Phase, b: Phase) -> Bool { a.rawValue < b.rawValue }
    }

    static let peripheralKey = "de.opencalifornia.camper.peripheral"
    /// The general service (`1000`): the unit advertises it, and it holds `1001`–`1004`.
    static let generalService = CBUUID(string: CamperGATT.uuid(CamperGATT.auth & 0xFF00))
    /// One service per function: char `0xXXYY` lives in service `0xXX00`.
    static let services: [CBUUID] = {
        let all = Array(CamperGATT.stateChars.values) + Array(CamperGATT.controlChars.values) + [CamperGATT.heartbeat, CamperGATT.auth]
        return Set(all.map { $0 & 0xFF00 }).sorted().map { CBUUID(string: CamperGATT.uuid($0)) }
    }()

    public init(restoreIdentifier: String = "de.opencalifornia.camper.central") {
        (events, sink) = AsyncStream.makeStream()
        super.init()
        var options: [String: Any] = [:]
        #if os(iOS)
        options[CBCentralManagerOptionRestoreIdentifierKey] = restoreIdentifier
        #endif
        // On the queue: no delegate callback can run before `central` is set.
        queue.sync { central = CBCentralManager(delegate: self, queue: queue, options: options) }
    }

    // MARK: pure mapping (tested without a radio)

    /// Why a link ended. HCI 0x13 (the unit's power-save hang-up) arrives as
    /// `CBError.peripheralDisconnected`; no error means our own `cancel()`.
    public static func mapDisconnect(_ error: Error?) -> DisconnectReason {
        guard let error else { return .localCancel }
        return (error as? CBError)?.code == .peripheralDisconnected ? .remoteHangUp : .linkLost
    }

    /// An error that means "no usable bond": iOS shows its own passkey dialog on the next access,
    /// or the bond is stale (the unit dropped it: *Forget This Device* in Settings).
    static func needsPairing(_ error: Error?) -> Bool {
        if let e = error as? CBATTError { return e.code == .insufficientAuthentication || e.code == .insufficientEncryption }
        if let e = error as? CBError { return e.code == .peerRemovedPairingInformation || e.code == .encryptionTimedOut }
        return false
    }

    /// A failed write: an ATT error is the unit's answer (`.writeRejected`); any other error means the
    /// write went out and no answer came back, so the unit may have acted (`.linkLost`).
    static func mapWrite(_ error: Error) -> TransportError {
        error is CBATTError ? .writeRejected : .linkLost
    }

    /// nil: Bluetooth is usable, or its state is not known yet (wait for the next update).
    static func availability(_ state: CBManagerState) -> BluetoothAvailability? {
        switch state {
        case .poweredOff: .poweredOff
        case .unauthorized: .unauthorized
        case .unsupported: .unsupported
        default: nil
        }
    }

    /// The short id of a char or service UUID of the unit's family; nil for any other UUID.
    static func short(_ uuid: CBUUID) -> UInt16? {
        let s = uuid.uuidString.lowercased()
        guard s.count == 36, s.hasPrefix("0000"),
              s.hasSuffix(CamperGATT.uuid(0).dropFirst(8)) else { return nil }
        return UInt16(s.dropFirst(4).prefix(4), radix: 16)
    }

    /// Scans for `window` and returns the units in range, strongest first, for the user to pick
    /// (also when there is only one). Empty means no unit in range.
    /// - Throws: `PairingScanError.bluetooth` when Bluetooth is not on, `.busy` when a scan already runs.
    public func pairingCandidates(window: Duration = .seconds(3)) async throws -> [PairingCandidate] {
        try await withCheckedThrowingContinuation { k in
            queue.async {
                guard self.central.state == .poweredOn else {
                    return k.resume(throwing: PairingScanError.bluetooth(Self.availability(self.central.state)))
                }
                guard self.candidates == nil else { return k.resume(throwing: PairingScanError.busy) }
                self.candidates = (CandidateList(), k)
                self.central.scanForPeripherals(withServices: [Self.generalService])
                let ms = Int(window.components.seconds * 1000 + window.components.attoseconds / 1_000_000_000_000_000)
                self.queue.asyncAfter(deadline: .now() + .milliseconds(ms)) {
                    self.central.stopScan()
                    guard let c = self.candidates else { return }
                    self.candidates = nil
                    c.k.resume(returning: c.list.sorted)
                }
            }
        }
    }

    /// The user picked `id` and confirmed it: the next `connect()` goes to that unit, once. It is
    /// adopted (and replaces the saved unit) when its `1004` read succeeds (see the class comment);
    /// any other end of that link, or `cancel()`, ends the first pairing.
    public func beginFirstPairing(with id: UUID) async {
        queue.async { self.policy.chosen = id }
    }

    // MARK: CamperTransport

    public func connect() async {
        queue.async { self.startConnect() }
    }

    public func cancel() async {
        queue.async { self.stop() }
    }

    public func read(_ char: UInt16) async throws -> [UInt8] {
        try await withCheckedThrowingContinuation { k in
            queue.async {
                guard self.phase == .up, let p = self.peripheral else { return k.resume(throwing: TransportError.notConnected) }
                guard let c = self.chars[char] else { return k.resume(throwing: TransportError.noSuchChar(char)) }
                if self.ops.addRead(char, k) { p.readValue(for: c) }
            }
        }
    }

    public func write(_ char: UInt16, _ bytes: [UInt8]) async throws {
        try await withCheckedThrowingContinuation { (k: CheckedContinuation<Void, any Error>) in
            queue.async {
                guard self.phase == .up, let p = self.peripheral else { return k.resume(throwing: TransportError.notConnected) }
                guard let c = self.chars[char] else { return k.resume(throwing: TransportError.noSuchChar(char)) }
                self.ops.addWrite(char, k)
                p.writeValue(Data(bytes), for: c, type: .withResponse)
            }
        }
    }

    /// Enables notifications on each char that supports them; a char without notify is skipped.
    /// The unit sends nothing on subscribe: the caller reads each char.
    public func subscribe(_ list: [UInt16]) async throws {
        try await withCheckedThrowingContinuation { (k: CheckedContinuation<Void, any Error>) in
            queue.async {
                guard self.phase == .up, let p = self.peripheral else { return k.resume(throwing: TransportError.notConnected) }
                let notifying = list.compactMap { self.chars[$0] }.filter { !$0.properties.isDisjoint(with: [.notify, .indicate]) }
                self.ops.addSubscribe(Set(notifying.compactMap { Self.short($0.uuid) }), k)
                for c in notifying { p.setNotifyValue(true, for: c) }
            }
        }
    }

    // MARK: link (on `queue`)

    private func startConnect() {
        if phase == .up { sink.yield(.encrypted); return }
        guard phase == .idle else { return }
        if let a = Self.availability(central.state) { sink.yield(.bluetoothUnavailable(a)); return }
        guard central.state == .poweredOn else { phase = .waitingForPower; return }
        switch policy.route() {
        case .chosen(let id), .saved(let id):
            // iOS resolves the unit's rotating address with the bond. Never another peripheral.
            guard let p = central.retrievePeripherals(withIdentifiers: [id]).first else {
                policy.linkEnded()
                sink.yield(.disconnected(reason: .failedToConnect))
                return
            }
            open(p)
        case .refuse:
            sink.yield(.pairingRequired)
        }
    }

    private func open(_ p: CBPeripheral) {
        peripheral = p
        p.delegate = self
        phase = .connecting
        central.connect(p)  // no timeout in CoreBluetooth; no auto-reconnect (the unit cuts a redial within 6 s)
    }

    private func linkOpened(_ p: CBPeripheral) {
        phase = .discovering
        linkGen += 1
        sink.yield(.connected)
        p.discoverServices(Self.services)
    }

    private func stop() {
        switch phase {
        case .idle:
            policy.linkEnded()
        case .waitingForPower, .connecting:
            if phase == .connecting, let p = peripheral { central.cancelPeripheralConnection(p) }
            tearDown()
            sink.yield(.disconnected(reason: .localCancel))
        case .discovering, .probing, .up:
            // didDisconnectPeripheral (error nil -> .localCancel) ends it.
            if let p = peripheral { central.cancelPeripheralConnection(p) }
        }
    }

    /// The link is gone: every pending call resumes (an issued write with `.linkLost`).
    private func tearDown() {
        phase = .idle
        policy.linkEnded()  // a first pairing is one-shot
        chars = [:]
        servicesLeft = 0
        restoredConnected = false
        ops.drain()
    }

    private func fail(_ p: CBPeripheral, _ error: Error?) {
        if Self.needsPairing(error) { sink.yield(.pairingRequired) }
        central.cancelPeripheralConnection(p)
    }
}

// MARK: - CBCentralManagerDelegate (on `queue`)

extension CoreBluetoothTransport: CBCentralManagerDelegate {
    public func centralManagerDidUpdateState(_ central: CBCentralManager) {
        if central.state == .poweredOn {
            if phase == .waitingForPower { phase = .idle; startConnect() }
            if restoredConnected, let p = peripheral, p.state == .connected { restoredConnected = false; linkOpened(p) }
            return
        }
        // Not powered on: CoreBluetooth sends no disconnect for the links it dropped.
        let was = phase
        if was > .waitingForPower { tearDown() }
        if was >= .discovering { sink.yield(.disconnected(reason: .linkLost)) }
        if let a = Self.availability(central.state) {
            if was == .waitingForPower { tearDown() }
            sink.yield(.bluetoothUnavailable(a))
        } else if was > .idle {
            phase = .waitingForPower  // resetting: connect again once it is back
        }
    }

    public func centralManager(_ central: CBCentralManager, didDiscover p: CBPeripheral,
                               advertisementData: [String: Any], rssi: NSNumber) {
        // A cheap first screen only: the user's pick and the passkey are the check.
        let name = advertisementData[CBAdvertisementDataLocalNameKey] as? String ?? p.name ?? ""
        candidates?.list.saw(id: p.identifier, name: name, rssi: rssi.intValue)
    }

    public func centralManager(_ central: CBCentralManager, didConnect p: CBPeripheral) {
        guard p === peripheral, phase == .connecting else { return }
        linkOpened(p)
    }

    public func centralManager(_ central: CBCentralManager, didFailToConnect p: CBPeripheral, error: Error?) {
        guard p === peripheral, phase != .idle else { return }
        if Self.needsPairing(error) { sink.yield(.pairingRequired) }
        tearDown()
        sink.yield(.disconnected(reason: .failedToConnect))
    }

    public func centralManager(_ central: CBCentralManager, didDisconnectPeripheral p: CBPeripheral, error: Error?) {
        guard p === peripheral, phase != .idle else { return }
        if Self.needsPairing(error) { sink.yield(.pairingRequired) }
        tearDown()
        sink.yield(.disconnected(reason: Self.mapDisconnect(error)))
    }

    #if os(iOS)
    /// iOS relaunched the app for the held link (it was terminated by the system, not swiped away).
    /// Called before `centralManagerDidUpdateState`; the bring-up resumes once Bluetooth is on.
    public func centralManager(_ central: CBCentralManager, willRestoreState dict: [String: Any]) {
        let restored = dict[CBCentralManagerRestoredStatePeripheralsKey] as? [CBPeripheral] ?? []
        for other in restored where other.identifier != policy.saved { central.cancelPeripheralConnection(other) }
        guard let p = restored.first(where: { $0.identifier == policy.saved }) else { return }
        peripheral = p
        p.delegate = self
        switch p.state {
        case .connected: phase = .connecting; restoredConnected = true
        case .connecting: phase = .connecting  // the pending connect lives on; didConnect follows
        default: peripheral = nil
        }
    }
    #endif
}

// MARK: - CBPeripheralDelegate (on `queue`)

extension CoreBluetoothTransport: CBPeripheralDelegate {
    public func peripheral(_ p: CBPeripheral, didDiscoverServices error: Error?) {
        guard p === peripheral, phase == .discovering else { return }
        let found = p.services ?? []
        guard error == nil, !found.isEmpty else { return fail(p, error) }
        servicesLeft = found.count
        for s in found { p.discoverCharacteristics(nil, for: s) }
    }

    public func peripheral(_ p: CBPeripheral, didDiscoverCharacteristicsFor service: CBService, error: Error?) {
        guard p === peripheral, phase == .discovering else { return }
        for c in service.characteristics ?? [] { if let s = Self.short(c.uuid) { chars[s] = c } }
        servicesLeft -= 1
        guard servicesLeft == 0 else { return }
        // `.encrypted` only once the char map is complete: the session starts the heartbeat on it.
        // No `1004`, no proof of encryption: not the unit, or not usable.
        guard let auth = chars[CamperGATT.auth] else { return fail(p, nil) }
        phase = .probing
        p.readValue(for: auth)
    }

    public func peripheral(_ p: CBPeripheral, didUpdateValueFor c: CBCharacteristic, error: Error?) {
        guard p === peripheral, let s = Self.short(c.uuid) else { return }
        if phase == .probing, s == CamperGATT.auth {
            // The probe's value is discarded: the session reads every char after `.encrypted`.
            if error == nil {
                switch policy.authenticated(p.identifier) {
                case .up: return linkUp(p)
                case .reject: return fail(p, nil)
                }
            }
            if Self.needsPairing(error), policy.pairingError() {
                // iOS pairs: it shows its passkey dialog. Read again after a pause until it
                // succeeds, the caller cancels, or `PeerPolicy.maxPairingReads` is reached.
                sink.yield(.pairing)
                let gen = linkGen
                queue.asyncAfter(deadline: .now() + 1) {
                    if self.phase == .probing, self.peripheral === p, self.linkGen == gen { p.readValue(for: c) }
                }
                return
            }
            return fail(p, error)
        }
        let bytes = [UInt8](c.value ?? Data())
        let result: Result<[UInt8], any Error> = error.map { e in
            if Self.needsPairing(e) { sink.yield(.pairingRequired) }
            return .failure(TransportError.noSuchChar(s))
        } ?? .success(bytes)
        // CoreBluetooth reports a read answer and a notification alike: while a read of this char is
        // pending, the value goes to that read and is never reported as a push (a push confirms a
        // command; a read can be the unit's write-through echo).
        switch ops.finishRead(s, result) {
        case true?: p.readValue(for: c)
        case false?: break
        // Nothing reaches the session before the authenticated read verified the link.
        case nil: if error == nil, phase == .up { sink.yield(.notified(char: s, bytes: bytes)) }
        }
    }

    public func peripheral(_ p: CBPeripheral, didWriteValueFor c: CBCharacteristic, error: Error?) {
        guard p === peripheral, let s = Self.short(c.uuid) else { return }
        if Self.needsPairing(error) { sink.yield(.pairingRequired) }
        sink.yield(.written(char: s, ok: error == nil))
        ops.finishWrite(s, error.map { .failure(Self.mapWrite($0)) } ?? .success(()))
    }

    public func peripheral(_ p: CBPeripheral, didUpdateNotificationStateFor c: CBCharacteristic, error: Error?) {
        guard p === peripheral, let s = Self.short(c.uuid) else { return }
        if Self.needsPairing(error) { sink.yield(.pairingRequired) }
        ops.finishSubscribe(s, error.map { _ in TransportError.writeRejected })
    }

    private func linkUp(_ p: CBPeripheral) {
        phase = .up
        UserDefaults.standard.set(p.identifier.uuidString, forKey: Self.peripheralKey)  // the adopted unit
        sink.yield(.encrypted)
    }
}

/// Which peripheral the transport may talk to. The saved identifier is the only one it connects
/// to, and it never switches by itself. A new one is adopted only when the user picked it
/// (`chosen`, one connect only) and its authenticated `1004` read succeeded, after a pairing or at
/// once with the iPhone's existing bond (for example the vendor app's): lenient, AD_IOS_SHARED_BOND.
/// Nothing is adopted in the background. A failed read or a missing `1004` never reaches
/// `authenticated`; the link then ends, and with it the first pairing.
///
/// - Requirement: R_IOS_PAIRING, R_IOS_BLE_DIRECT
struct PeerPolicy {
    private(set) var saved: UUID?
    /// The unit the user picked for a first pairing; nil outside one.
    var chosen: UUID?
    private var pairingReads = 0
    /// The re-reads of `1004` in one first pairing (one per second): a hard cap of its own, so a
    /// caller that gave up without `cancel()` leaves no endless reads.
    static let maxPairingReads = 90

    init(saved: UUID?) { self.saved = saved }

    enum Route: Equatable { case chosen(UUID), saved(UUID), refuse }
    enum Verdict: Equatable {
        /// The link is verified: report it encrypted (a saved unit, or a unit adopted just now).
        case up
        case reject
    }

    func route() -> Route {
        if let chosen { return .chosen(chosen) }
        return saved.map(Route.saved) ?? .refuse
    }

    /// The `1004` read failed with a pairing error. True: in a first pairing, iOS pairs and the read
    /// goes again; false: end the link (a saved unit's bond is stale).
    mutating func pairingError() -> Bool {
        guard chosen != nil, pairingReads < Self.maxPairingReads else { return false }
        pairingReads += 1
        return true
    }

    /// The authenticated `1004` read succeeded on `id`. Ends a first pairing (adopted or not).
    mutating func authenticated(_ id: UUID) -> Verdict {
        guard let c = chosen else { return id == saved ? .up : .reject }
        linkEnded()
        guard id == c else { return .reject }
        saved = id
        return .up
    }

    /// The link ended, a connect failed, or the caller cancelled: the first pairing is over.
    mutating func linkEnded() {
        chosen = nil
        pairingReads = 0
    }
}

/// Why `pairingCandidates` could not scan.
public enum PairingScanError: Error, Equatable, Sendable {
    /// Bluetooth is not on; nil: its state is not known yet.
    case bluetooth(BluetoothAvailability?)
    /// Another candidate scan runs.
    case busy
}

/// A unit in range during a first pairing.
public struct PairingCandidate: Sendable, Equatable {
    public let id: UUID
    public let name: String
    public let rssi: Int
}

/// The units seen in a pairing scan: the exact advertised name only, one entry per identifier.
///
/// - Requirement: R_IOS_PAIRING
struct CandidateList {
    private var seen: [UUID: PairingCandidate] = [:]

    mutating func saw(id: UUID, name: String, rssi: Int) {
        guard name == CamperGATT.deviceName else { return }
        seen[id] = PairingCandidate(id: id, name: name, rssi: rssi)
    }

    /// Strongest signal first.
    var sorted: [PairingCandidate] { seen.values.sorted { $0.rssi > $1.rssi } }
}

/// The continuations of the calls in flight, per char, in issue order; each resumes exactly once.
/// Not thread-safe: the transport confines it to its queue.
///
/// - Requirement: R_IOS_BLE_DIRECT, R_IOS_HONEST
struct PendingOps {
    typealias Read = CheckedContinuation<[UInt8], any Error>
    typealias Done = CheckedContinuation<Void, any Error>
    private var reads: [UInt16: [Read]] = [:]
    private var writes: [UInt16: [Done]] = [:]
    private var subscribes: [(left: Set<UInt16>, k: Done)] = []

    /// Queues a read. True: it is the only one for the char, so issue `readValue` now (one read
    /// per char on the air: CoreBluetooth answers do not say which request they belong to).
    mutating func addRead(_ c: UInt16, _ k: Read) -> Bool {
        reads[c, default: []].append(k)
        return reads[c]!.count == 1
    }

    /// A value arrived. Resumes the oldest read: true when another one waits (issue it), false when
    /// none waits, nil when no read was pending (the value is a notification).
    mutating func finishRead(_ c: UInt16, _ r: Result<[UInt8], any Error>) -> Bool? {
        guard var q = reads[c], !q.isEmpty else { return nil }
        q.removeFirst().resume(with: r)
        reads[c] = q.isEmpty ? nil : q
        return !q.isEmpty
    }

    /// Queues an issued write; the ATT answers come in issue order.
    mutating func addWrite(_ c: UInt16, _ k: Done) { writes[c, default: []].append(k) }

    mutating func finishWrite(_ c: UInt16, _ r: Result<Void, any Error>) {
        guard var q = writes[c], !q.isEmpty else { return }
        q.removeFirst().resume(with: r)
        writes[c] = q.isEmpty ? nil : q
    }

    /// Waits for the notification state of every char in `chars`; an empty set resumes at once.
    mutating func addSubscribe(_ chars: Set<UInt16>, _ k: Done) {
        if chars.isEmpty { k.resume() } else { subscribes.append((chars, k)) }
    }

    mutating func finishSubscribe(_ c: UInt16, _ error: (any Error)?) {
        subscribes = subscribes.compactMap { s in
            guard s.left.contains(c) else { return s }
            if let error { s.k.resume(throwing: error); return nil }
            let left = s.left.subtracting([c])
            if left.isEmpty { s.k.resume(); return nil }
            return (left, s.k)
        }
    }

    /// The link went: a write was issued, so the unit may have acted (`.linkLost`); a read or a
    /// subscribe has no effect on the unit (`.notConnected`).
    mutating func drain() {
        let (r, w, s) = (reads.values.joined(), writes.values.joined(), subscribes.map(\.k))
        reads = [:]; writes = [:]; subscribes = []
        for k in r { k.resume(throwing: TransportError.notConnected) }
        for k in w { k.resume(throwing: TransportError.linkLost) }
        for k in s { k.resume(throwing: TransportError.notConnected) }
    }
}
