import CaliCore
import Foundation

/// The link to the camper unit: connect, subscribe and read every state function, keep the link
/// alive with the `1003` heartbeat, and respect the unit's power-save hang-up
/// (`S_SEQ_POWER_SAVE_HANGUP`). The rules mirror the satellite's session
/// (`firmware/components/cali_core/session.c`, `cali_session.h`).
///
/// One task consumes `transport.events` for the session's lifetime; every other task (heartbeat,
/// reconnect, timeouts) calls back into the actor.
///
/// - Requirement: R_IOS_BLE_DIRECT, R_IOS_POWER_SAVE, R_IOS_BACKGROUND_LINK, R_IOS_HONEST
public actor CamperSession {
    public private(set) var state = CamperState()
    public private(set) var link: LinkState = .off

    /// The unit cuts a link that starts less than 6 s after it ended the previous one
    /// (`CALI_SESSION_UNIT_COOLDOWN_MS`): every reconnect waits this long after a lost link.
    static let cooldown = Duration.seconds(7)
    /// Reconnect delays after a lost link while camping is on; the last one repeats.
    static let backoff: [Duration] = [.seconds(5), .seconds(10), .seconds(30), .seconds(60)]
    /// A link that held this long resets the backoff (`CALI_SESSION_STABLE_MS`).
    static let stable = Duration.seconds(60)
    static let redialTimeout = Duration.seconds(20)

    private let transport: CamperTransport
    private let clock: any Clock<Duration>
    private let semantics: Semantics
    private let charToFunction: [UInt16: String]
    /// Interpretations before `applySwCorrections` (it adds to its input, so it runs on these each time).
    private var uncorrected: [String: [String: JSONValue]] = [:]
    private var subscribers: [Int: AsyncStream<(CamperState, LinkState)>.Continuation] = [:]
    private var waiters: [Int: CheckedContinuation<Void, any Error>] = [:]
    private var nextID = 0
    private var heartbeat: Task<Void, Never>?
    private var firstBeat: (@Sendable () -> Duration)?
    private var sinceLost: (@Sendable () -> Duration)?
    private var reconnect: Task<Void, Never>?
    private var backoffStep = 0
    /// The delay of the scheduled automatic reconnect; nil when none is scheduled.
    private(set) var pendingReconnect: Duration?

    public init(transport: CamperTransport, clock: any Clock<Duration>) {
        self.init(transport: transport, clock: clock, semantics: .shared)
    }

    init(transport: CamperTransport, clock: any Clock<Duration>, semantics: Semantics) {
        self.transport = transport
        self.clock = clock
        self.semantics = semantics
        charToFunction = Dictionary(uniqueKeysWithValues: CamperGATT.stateChars.map { ($1, $0) })
        let events = transport.events
        Task { [weak self] in
            for await e in events {
                guard let self else { return }
                await self.handle(e)
            }
        }
    }

    // MARK: public API

    /// Camping master on (`1202` `State` 1): only then the link is held in the background.
    public var holdsInBackground: Bool { state.fields["campingmode"]?["State"] == 1 }

    /// The `1003` heartbeat is running on the current link.
    public var heartbeatBeating: Bool { heartbeat != nil }

    /// Time since the current link's first heartbeat; nil while it does not beat. A command on a link
    /// that is younger than `CODEC_ARM_DELAY_MS` waits out the rest before its first frame.
    public var sinceFirstBeat: Duration? { firstBeat?() }

    /// Every change of `state` or `link`, starting with the current values.
    public func updates() -> AsyncStream<(CamperState, LinkState)> {
        let (stream, cont) = AsyncStream<(CamperState, LinkState)>.makeStream(bufferingPolicy: .bufferingNewest(16))
        let id = takeID()
        subscribers[id] = cont
        cont.yield((state, link))
        cont.onTermination = { [weak self] _ in Task { await self?.unsubscribe(id) } }
        return stream
    }

    /// Brings the link up (if it is not) and reads every state function. After a lost link it waits
    /// until the unit's 7 s cooldown has passed; when that is longer than `timeout` it throws
    /// `inRest(retryAfter:)` at once. It also ends the power-save rest and replaces a scheduled
    /// automatic reconnect.
    public func ensureConnected(timeout: Duration) async throws {
        if link == .up { return }
        if link == .connecting { return try await waitForLink(timeout: timeout, connect: false) }
        if let left = cooldownLeft() {
            if left > timeout { throw SessionError.inRest(retryAfter: left) }
            cancelReconnect()
            try await clock.sleep(for: left)
            if link == .up { return }
            if link == .connecting { return try await waitForLink(timeout: timeout, connect: false) }
        }
        cancelReconnect()
        setLink(.connecting)
        try await waitForLink(timeout: timeout, connect: true)
        try await subscribeAndRead()
    }

    /// Reads every state function once; each frame replaces that function's last one.
    /// A function whose read fails (not offered by the unit) is skipped.
    public func readAll() async throws {
        guard link == .up else { throw SessionError.unreachable }
        // general first: it carries the protocol gate.
        let order = CamperGATT.stateChars.sorted { $0.key == "general" || ($1.key != "general" && $0.key < $1.key) }
        for (_, char) in order {
            do {
                store(char, try await transport.read(char))
            } catch TransportError.notConnected {
                throw SessionError.unreachable
            } catch {
                continue
            }
        }
    }

    // MARK: link

    private func subscribeAndRead() async throws {
        do {
            try await transport.subscribe(Array(CamperGATT.stateChars.values))
        } catch {
            throw SessionError.unreachable
        }
        try await readAll()
    }

    /// Waits for `.encrypted`; fails on a failed connect, Bluetooth off or `timeout` (then cancels the connect).
    private func waitForLink(timeout: Duration, connect: Bool) async throws {
        let id = takeID()
        let timer = Task { [clock] in
            do { try await clock.sleep(for: timeout) } catch { return }
            await self.timedOut(id)
        }
        defer { timer.cancel() }
        try await withCheckedThrowingContinuation { (cont: CheckedContinuation<Void, any Error>) in
            waiters[id] = cont
            // Connect only once the waiter is registered: the fake answers inside connect().
            if connect { Task { await transport.connect() } }
        }
    }

    private func timedOut(_ id: Int) async {
        guard let w = waiters.removeValue(forKey: id) else { return }
        w.resume(throwing: SessionError.unreachable)
        if waiters.isEmpty, link == .connecting {
            setLink(.unreachable("The camper unit did not answer."))
            await transport.cancel()
        }
    }

    private func resumeWaiters(_ result: Result<Void, SessionError>) {
        let all = waiters.values
        waiters = [:]
        for w in all { w.resume(with: result.mapError { $0 as any Error }) }
    }

    private func handle(_ e: TransportEvent) {
        switch e {
        case .connected, .written:
            break
        case .encrypted:
            setLink(.up)
            startHeartbeat()
            // A link nobody here asked for (iOS reconnect, state restoration) still subscribes and reads.
            if waiters.isEmpty { Task { try? await self.subscribeAndRead() } }
            resumeWaiters(.success(()))
        case .notified(let char, let bytes):
            store(char, bytes)
        case .bluetoothUnavailable(let a):
            setLink(.unreachable("Bluetooth is not available."))
            resumeWaiters(.failure(.bluetooth(a)))
        case .pairingRequired:
            setLink(.unreachable("The camper unit asks for pairing."))
            resumeWaiters(.failure(.unreachable))
        case .disconnected(let reason):
            lost(reason)
        }
    }

    private func lost(_ reason: DisconnectReason) {
        let wasUp = link == .up
        let held = sinceFirstBeat.map { $0 >= Self.stable } ?? false
        stopHeartbeat()
        if !waiters.isEmpty {  // a connect in progress failed: its caller decides what follows
            if link == .connecting { setLink(.unreachable("The camper unit did not answer.")) }
            resumeWaiters(.failure(.unreachable))
            return
        }
        guard wasUp else { return }
        sinceLost = clock.stopwatch()
        if held { backoffStep = 0 }
        if reason == .localCancel {
            setLink(.off)
        } else if reason == .remoteHangUp, restConditions {
            // S_SEQ_POWER_SAVE_HANGUP: the unit saves power; no redial until the user or Siri asks.
            setLink(.rest(until: Date().addingTimeInterval(Self.seconds(Self.cooldown))))
        } else if holdsInBackground || reason == .remoteHangUp {
            scheduleReconnect()
        } else {
            setLink(.off)
        }
    }

    /// session.c `rest_conditions`: camping master off and shore power neither active nor standby,
    /// both known. A missing frame or field means "reconnect as usual".
    private var restConditions: Bool {
        guard state.fields["campingmode"]?["State"] == 0,
              case .string(let shore)? = state.interpreted["energy"]?["shore_state"]
        else { return false }
        return shore != "active" && shore != "standby"
    }

    private func scheduleReconnect() {
        let delay = max(Self.backoff[min(backoffStep, Self.backoff.count - 1)], cooldownLeft() ?? .zero)
        backoffStep += 1
        pendingReconnect = delay
        setLink(.unreachable("Reconnecting…"))
        reconnect = Task { [clock] in
            do { try await clock.sleep(for: delay) } catch { return }
            await self.redial()
        }
    }

    private func redial() async {
        reconnect = nil
        pendingReconnect = nil
        do {
            try await ensureConnected(timeout: Self.redialTimeout)
        } catch {
            if link != .up, reconnect == nil { scheduleReconnect() }
        }
    }

    private func cancelReconnect() {
        reconnect?.cancel()
        reconnect = nil
        pendingReconnect = nil
    }

    private func cooldownLeft() -> Duration? {
        guard let sinceLost else { return nil }
        let left = Self.cooldown - sinceLost()
        return left > .zero ? left : nil
    }

    // MARK: heartbeat

    /// The `1003` heartbeat: 4-byte big-endian counter from `CODEC_HEARTBEAT_START`, +1 every
    /// `CODEC_HEARTBEAT_PERIOD_MS`, first beat at link up, until the link goes down.
    private func startHeartbeat() {
        stopHeartbeat()
        heartbeat = Task { [transport, clock] in
            var counter = CamperGATT.heartbeatStart
            while !Task.isCancelled {
                do {
                    try await transport.write(CamperGATT.heartbeat, withUnsafeBytes(of: counter.bigEndian, Array.init))
                } catch TransportError.notConnected {
                    return  // the disconnect event follows and stops the beat
                } catch {
                    // ponytail: a rejected beat is skipped; the C session drops the link instead.
                }
                if counter == CamperGATT.heartbeatStart { self.firstBeatWritten() }
                counter &+= 1
                do { try await clock.sleep(for: CamperGATT.heartbeatPeriod) } catch { return }
            }
        }
    }

    private func firstBeatWritten() {
        if heartbeat != nil { firstBeat = clock.stopwatch() }
    }

    private func stopHeartbeat() {
        heartbeat?.cancel()
        heartbeat = nil
        firstBeat = nil
    }

    // MARK: state

    private func store(_ char: UInt16, _ bytes: [UInt8]) {
        guard let fn = charToFunction[char], let fields = try? CaliCodec.decode(function: fn, bytes: bytes) else { return }
        state.fields[fn] = fields
        do {
            uncorrected[fn] = try semantics.interpret(function: fn, fields: fields)
            state.semanticsErrors[fn] = nil
        } catch {
            uncorrected[fn] = nil
            state.semanticsErrors[fn] = (error as? SemanticsError)?.message ?? "\(error)"
        }
        do {
            state.interpreted = try semantics.applySwCorrections(uncorrected)
            state.semanticsErrors["applySwCorrections"] = nil
        } catch {
            state.interpreted = uncorrected
            state.semanticsErrors["applySwCorrections"] = (error as? SemanticsError)?.message ?? "\(error)"
        }
        state.gate = ProtocolGate(general: state.fields["general"])
        state.asOf = Date()
        publish()
    }

    private func setLink(_ l: LinkState) {
        guard l != link else { return }
        link = l
        publish()
    }

    private func publish() {
        for c in subscribers.values { c.yield((state, link)) }
    }

    private func unsubscribe(_ id: Int) { subscribers[id] = nil }

    private func takeID() -> Int {
        defer { nextID += 1 }
        return nextID
    }

    private static func seconds(_ d: Duration) -> TimeInterval {
        Double(d.components.seconds) + Double(d.components.attoseconds) / 1e18
    }
}

extension Clock {
    /// A closure that gives the time since this call (opens an `any Clock` once).
    fileprivate func stopwatch() -> @Sendable () -> Duration {
        let start = now
        return { start.duration(to: self.now) }
    }
}
