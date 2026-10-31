import Foundation

/// A deterministic stand-in for the camper unit. Tests drive it (hang-up, foreign pushes,
/// rejected writes, Bluetooth off, failed connect) and read back every write in order.
///
/// Time: only `hangUp(after:)` waits, and it waits on the injected clock, so a test clock
/// makes it instant. Seeds are raw frames; there is no state-field encoder to build one
/// from a field dictionary (the C codec only encodes control frames).
///
/// - Requirement: R_IOS_BLE_DIRECT, R_IOS_POWER_SAVE
public actor FakeCamperTransport: CamperTransport {
    public struct Write: Sendable, Equatable {
        public let char: UInt16
        public let bytes: [UInt8]
    }

    public nonisolated let events: AsyncStream<TransportEvent>
    private let sink: AsyncStream<TransportEvent>.Continuation
    private let clock: any Clock<Duration>
    private var frames: [UInt16: [UInt8]]
    private var subscribed: Set<UInt16> = []
    private var connected = false
    private var rejectNext = false
    private var dropOnWriteTo: UInt16?
    private var connectWillFail = false
    private var unavailable: BluetoothAvailability?
    private var hangUpTask: Task<Void, Never>?
    private var pairOnConnect = false
    private var pairingOpen = false
    private var pairingRequiredOnConnect = false
    private var notPairedOnConnect = false
    private var candidates: [PairingCandidate] = []

    /// Every write attempt, in order, including a rejected one.
    public private(set) var writes: [Write] = []

    public init(clock: any Clock<Duration> = ContinuousClock(), frames: [UInt16: [UInt8]] = [:],
                candidates: [PairingCandidate] = []) {
        (events, sink) = AsyncStream.makeStream()
        self.clock = clock
        self.frames = frames
        self.candidates = candidates
    }

    // MARK: test controls

    /// Sets the frame a read returns; a subscribed char also gets a `.notified` push.
    public func seed(_ char: UInt16, _ bytes: [UInt8]) {
        frames[char] = bytes
        if connected, subscribed.contains(char) { sink.yield(.notified(char: char, bytes: bytes)) }
    }

    /// The unit ends the link after `after` on the injected clock.
    public func hangUp(after: Duration) {
        hangUpTask?.cancel()
        hangUpTask = Task { [clock] in
            do { try await clock.sleep(for: after) } catch { return }
            self.finishHangUp()
        }
    }

    /// Another central changed the unit: the frame is stored, and pushed only if we subscribed to
    /// the char while connected (the real unit does not notify an unsubscribed char).
    public func pushForeign(char: UInt16, bytes: [UInt8]) {
        frames[char] = bytes
        if connected, subscribed.contains(char) { sink.yield(.notified(char: char, bytes: bytes)) }
    }

    /// The unit's write-through echo: a later read returns `bytes`, and nothing is pushed.
    public func echo(_ char: UInt16, _ bytes: [UInt8]) { frames[char] = bytes }

    public func rejectNextWrite() { rejectNext = true }
    /// The next write to `char` is issued (recorded), then the link drops before its ACK.
    public func dropLinkDuringNextWrite(to char: UInt16) { dropOnWriteTo = char }
    /// A later read of `char` fails (the unit does not offer it).
    public func remove(_ char: UInt16) { frames[char] = nil }
    public func failNextConnect() { connectWillFail = true }
    /// `connect()` reports `.bluetoothUnavailable` until called with `nil`.
    public func setBluetooth(unavailable: BluetoothAvailability?) { self.unavailable = unavailable }
    /// The link drops without the unit's hang-up (out of range).
    public func dropLink() { end(.linkLost) }
    public func demandPairing() { sink.yield(.pairingRequired) }
    /// The next `connect()` is a first pairing: `.connected` and `.pairing`, then nothing until
    /// `completePairing()` (the user typed the passkey).
    public func pairNextConnect() { pairOnConnect = true }
    public func completePairing() {
        guard pairingOpen else { return }
        pairingOpen = false
        connected = true
        sink.yield(.encrypted)
    }
    /// The next `connect()` finds a stale bond: `.pairingRequired`, no link.
    public func requirePairingOnNextConnect() { pairingRequiredOnConnect = true }
    /// The next `connect()` finds no adopted unit: `.notPaired`, no link.
    public func notPairedOnNextConnect() { notPairedOnConnect = true }
    /// The units a pairing scan finds (none: the scan throws `.noUnitFound`).
    public func setCandidates(_ list: [PairingCandidate]) { candidates = list }

    /// Every pick, in order.
    public private(set) var picks: [UUID] = []

    // MARK: CamperTransport

    /// Counts every dial.
    public private(set) var connects = 0

    public func connect() {
        connects += 1
        if let unavailable { sink.yield(.bluetoothUnavailable(unavailable)); return }
        if connectWillFail { connectWillFail = false; sink.yield(.disconnected(reason: .failedToConnect)); return }
        if pairingRequiredOnConnect { pairingRequiredOnConnect = false; sink.yield(.pairingRequired); return }
        if notPairedOnConnect { notPairedOnConnect = false; sink.yield(.notPaired); return }
        if pairOnConnect {
            pairOnConnect = false
            pairingOpen = true
            sink.yield(.connected)
            sink.yield(.pairing)
            return
        }
        connected = true
        sink.yield(.connected)
        sink.yield(.encrypted)
    }

    /// Counts every call, also one without a link.
    public private(set) var cancels = 0
    public func cancel() {
        cancels += 1
        pairingOpen = false
        end(.localCancel)
    }

    public func read(_ char: UInt16) throws -> [UInt8] {
        guard connected else { throw TransportError.notConnected }
        guard let f = frames[char] else { throw TransportError.noSuchChar(char) }
        return f
    }

    public func write(_ char: UInt16, _ bytes: [UInt8]) throws {
        guard connected else { throw TransportError.notConnected }
        writes.append(Write(char: char, bytes: bytes))
        if dropOnWriteTo == char {
            dropOnWriteTo = nil
            end(.linkLost)
            throw TransportError.linkLost
        }
        if rejectNext {
            rejectNext = false
            sink.yield(.written(char: char, ok: false))
            throw TransportError.writeRejected
        }
        sink.yield(.written(char: char, ok: true))
    }

    /// Like the real transport: Bluetooth off throws, an empty scan throws `.noUnitFound`; strongest first.
    public func pairingCandidates(window: Duration) throws -> [PairingCandidate] {
        if let unavailable { throw PairingScanError.bluetooth(unavailable) }
        guard !candidates.isEmpty else { throw PairingScanError.noUnitFound }
        return candidates.sorted { $0.rssi > $1.rssi }
    }

    /// Like the real transport: the current link ends (`.localCancel`), then one connect at once.
    public func beginFirstPairing(with id: UUID) {
        picks.append(id)
        end(.localCancel)
        connect()
    }

    /// Like the real unit, sends nothing on subscribe: the caller must `read` each char.
    public func subscribe(_ chars: [UInt16]) throws {
        guard connected else { throw TransportError.notConnected }
        subscribed.formUnion(chars)
    }

    private func finishHangUp() { end(.remoteHangUp) }

    private func end(_ reason: DisconnectReason) {
        hangUpTask?.cancel()
        hangUpTask = nil
        guard connected else { return }
        connected = false
        subscribed = []
        sink.yield(.disconnected(reason: reason))
    }
}

#if DEBUG
/// A manual clock for tests: `sleep` suspends until `advance(by:)` passes its deadline.
public final class TestClock: Clock, @unchecked Sendable {
    public struct Instant: InstantProtocol {
        public var offset: Duration
        public func advanced(by d: Duration) -> Instant { Instant(offset: offset + d) }
        public func duration(to other: Instant) -> Duration { other.offset - offset }
        public static func < (a: Instant, b: Instant) -> Bool { a.offset < b.offset }
    }

    private struct Sleeper { let deadline: Instant; let cont: CheckedContinuation<Void, any Error>; let id: Int }
    private let lock = NSLock()
    private var current = Instant(offset: .zero)
    private var sleepers: [Sleeper] = []
    private var nextID = 0

    public init() {}
    public var minimumResolution: Duration { .zero }
    public var now: Instant { lock.withLock { current } }
    /// Sleepers waiting now; tests poll it before `advance` so the sleep is registered first.
    public var sleeperCount: Int { lock.withLock { sleepers.count } }
    /// The deadlines (time since the clock's start) of the sleepers waiting now: a test can wait
    /// for one particular sleep to be registered.
    public var deadlines: [Duration] { lock.withLock { sleepers.map(\.deadline.offset) } }

    public func sleep(until deadline: Instant, tolerance: Duration?) async throws {
        let id = lock.withLock { defer { nextID += 1 }; return nextID }
        try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { cont in
                // A task cancelled before this point already ran onCancel (it found nothing):
                // checking under the lock keeps a dead sleeper out of `sleepers`.
                let ready = lock.withLock { () -> Bool? in
                    if Task.isCancelled { return nil }
                    if deadline <= current { return true }
                    sleepers.append(Sleeper(deadline: deadline, cont: cont, id: id))
                    return false
                }
                switch ready {
                case nil: cont.resume(throwing: CancellationError())
                case true?: cont.resume()
                case false?: break
                }
            }
        } onCancel: {
            let s = lock.withLock { () -> Sleeper? in
                guard let i = sleepers.firstIndex(where: { $0.id == id }) else { return nil }
                return sleepers.remove(at: i)
            }
            s?.cont.resume(throwing: CancellationError())
        }
    }

    public func advance(by d: Duration) {
        let due = lock.withLock { () -> [Sleeper] in
            current = current.advanced(by: d)
            let due = sleepers.filter { $0.deadline <= current }
            sleepers.removeAll { $0.deadline <= current }
            return due
        }
        for s in due { s.cont.resume() }
    }
}
#endif
