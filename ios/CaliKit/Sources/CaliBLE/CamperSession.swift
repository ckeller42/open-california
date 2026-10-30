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

    let transport: CamperTransport
    let clock: any Clock<Duration>
    private let semantics: Semantics
    private let charToFunction: [UInt16: String]
    /// Interpretations before `applySwCorrections` (it adds to its input, so it runs on these each time).
    private var uncorrected: [String: [String: JSONValue]] = [:]
    private var waterGuard = WaterGuard()
    /// Monotonic time for the water ramp debounce.
    private let elapsed: @Sendable () -> Duration
    private var subscribers: [Int: AsyncStream<(CamperState, LinkState)>.Continuation] = [:]
    private var pushSubscribers: [Int: AsyncStream<Push>.Continuation] = [:]
    private var waiters: [Int: CheckedContinuation<Void, any Error>] = [:]
    private var nextID = 0
    /// +1 per encrypted link: a task of an earlier link can never mark the current one.
    private var linkGen = 0
    /// Subscribe and the read pass finished on the current link.
    private var ready = false
    /// The one bring-up (cooldown, connect, subscribe, read) in progress; every caller awaits it.
    private var flight: Task<Void, any Error>?
    /// Why the session cancelled its own link (the next `.localCancel` is then a link loss).
    private var dropReason: String?
    private(set) var commands = 0
    /// Functions with a frame (read or push) from the current link; cleared at each link up.
    public private(set) var liveFunctions: Set<String> = []
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
        elapsed = clock.stopwatch()
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

    /// The link is up, subscribed, and its read pass finished: `state` holds this link's frames.
    public var isReady: Bool { link == .up && ready }

    /// Camping master on (`1202` `State` 1): only then the link is held in the background.
    public var holdsInBackground: Bool { state.fields["campingmode"]?["State"] == 1 }

    /// The `1003` heartbeat is running on the current link.
    public var heartbeatBeating: Bool { heartbeat != nil }

    /// Time since the current link's first successful heartbeat write; nil while none. A command
    /// waits until this reaches `CODEC_ARM_DELAY_MS` before its first frame.
    public var sinceFirstBeat: Duration? { firstBeat?() }

    /// A control command is running from now until `endCommand()`. While one runs, the unit's
    /// hang-up does not start the power-save rest (session.c `!cali_ctl_busy()`): it reconnects.
    public func beginCommand() { commands += 1 }
    public func endCommand() { commands = max(0, commands - 1) }

    /// Every change of `state` or `link`, starting with the current values.
    public func updates() -> AsyncStream<(CamperState, LinkState)> {
        let (stream, cont) = AsyncStream<(CamperState, LinkState)>.makeStream(bufferingPolicy: .bufferingNewest(16))
        let id = takeID()
        subscribers[id] = cont
        cont.yield((state, link))
        cont.onTermination = { [weak self] _ in Task { await self?.unsubscribe(id) } }
        return stream
    }

    /// Brings the link up, subscribes and reads every state function; returns at once when the link
    /// is already ready. `timeout` is the total budget of the whole call. After a lost link it waits
    /// until the unit's 7 s cooldown has passed; when that does not fit the budget it throws
    /// `inRest(retryAfter:)` at once. It ends the power-save rest and replaces a scheduled reconnect.
    /// Concurrent callers share one bring-up.
    public func ensureConnected(timeout: Duration) async throws {
        let remaining = clock.countdown(timeout)
        if isReady { return }
        if flight == nil {
            if let left = cooldownLeft(), left > timeout { throw SessionError.inRest(retryAfter: left) }
            cancelReconnect()
            startFlight(budget: timeout)
        }
        guard let f = flight else { throw SessionError.unreachable }
        try await race(clock, remaining()) { try await f.value }
        guard isReady else { throw SessionError.unreachable }
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
            } catch is CancellationError {
                throw SessionError.unreachable
            } catch {
                continue
            }
        }
    }

    /// What the unit pushed (a notification), or that the link went down. Reads are not in it:
    /// a read of a state char can be the unit's write-through echo of our own write.
    enum Push: Sendable {
        case frame(function: String, state: CamperState)
        case linkDown
    }

    /// Every push from now on (see `Push`); the command runner's only source of confirmation.
    /// The caller ends it with `unsubscribePushes(id)`.
    func pushes() -> (id: Int, stream: AsyncStream<Push>) {
        let (stream, cont) = AsyncStream<Push>.makeStream(bufferingPolicy: .bufferingNewest(64))
        let id = takeID()
        pushSubscribers[id] = cont
        return (id, stream)
    }

    func unsubscribePushes(_ id: Int) { pushSubscribers.removeValue(forKey: id)?.finish() }

    var pushSubscriberCount: Int { pushSubscribers.count }

    // MARK: link

    private func startFlight(budget: Duration) {
        flight = Task {
            defer { self.flightDone() }
            do {
                try await race(clock, budget) { try await self.bringUp() }
            } catch {
                await self.dropLink("The camper unit did not answer.")
                throw error is SessionError ? error : SessionError.unreachable
            }
        }
    }

    private func flightDone() { flight = nil }

    /// Cooldown, connect (unless up), subscribe, read; `ready` only when all of it worked on one link.
    private func bringUp() async throws {
        if let left = cooldownLeft() { try await clock.sleep(for: left) }
        if link != .up {
            setLink(.connecting)
            try await waitForLink()
        }
        let gen = linkGen
        do {
            try await transport.subscribe(Array(CamperGATT.stateChars.values))
        } catch {
            throw SessionError.unreachable
        }
        try await readAll()
        guard gen == linkGen, link == .up, !Task.isCancelled else { throw SessionError.unreachable }
        ready = true
        publish()
    }

    /// Waits for `.encrypted`; a failed connect, Bluetooth off or cancellation (the deadline) fails it.
    private func waitForLink() async throws {
        let id = takeID()
        try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { (cont: CheckedContinuation<Void, any Error>) in
                waiters[id] = cont
                // Connect only once the waiter is registered: the fake answers inside connect().
                Task { await transport.connect() }
            }
        } onCancel: {
            Task { await self.failWaiter(id) }
        }
    }

    private func failWaiter(_ id: Int) {
        waiters.removeValue(forKey: id)?.resume(throwing: SessionError.unreachable)
    }

    private func resumeWaiters(_ result: Result<Void, SessionError>) {
        let all = waiters.values
        waiters = [:]
        for w in all { w.resume(with: result.mapError { $0 as any Error }) }
    }

    /// The session ends its own link after a failure on it (session.c `link_lost`): a heartbeat that
    /// cannot be written, a failed subscribe or read pass, a missed deadline.
    private func dropLink(_ why: String) async {
        ready = false
        stopHeartbeat()
        switch link {
        case .connecting:
            setLink(.unreachable(why))
            resumeWaiters(.failure(.unreachable))
            await transport.cancel()
        case .up:
            dropReason = why
            await transport.cancel()
        default:
            break
        }
    }

    private func handle(_ e: TransportEvent) {
        switch e {
        case .connected, .written:
            break
        case .encrypted:
            linkGen += 1
            ready = false
            liveFunctions = []
            state.lightConfig = [:]
            setLink(.up)
            startHeartbeat(gen: linkGen)
            // A link nobody here asked for (iOS reconnect, state restoration) still subscribes and reads.
            if flight == nil { startFlight(budget: Self.redialTimeout) }
            resumeWaiters(.success(()))
        case .notified(let char, let bytes):
            store(char, bytes)
            if let fn = charToFunction[char] {
                for c in pushSubscribers.values { c.yield(.frame(function: fn, state: state)) }
            }
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

    private func lost(_ raw: DisconnectReason) {
        let wasUp = link == .up
        let held = sinceFirstBeat.map { $0 >= Self.stable } ?? false
        let dropped = raw == .localCancel ? dropReason : nil
        let reason = dropped == nil ? raw : .linkLost
        dropReason = nil
        ready = false
        stopHeartbeat()
        if !waiters.isEmpty {  // a connect in progress failed: its caller decides what follows
            if link == .connecting { setLink(.unreachable("The camper unit did not answer.")) }
            resumeWaiters(.failure(.unreachable))
            return
        }
        guard wasUp else { return }
        for c in pushSubscribers.values { c.yield(.linkDown) }
        sinceLost = clock.stopwatch()
        if held { backoffStep = 0 }
        if reason == .localCancel {
            setLink(.off)
        } else if reason == .remoteHangUp, restConditions {
            // S_SEQ_POWER_SAVE_HANGUP: the unit saves power; no redial until the user or Siri asks.
            setLink(.rest(until: Date().addingTimeInterval(Self.seconds(Self.cooldown))))
        } else if holdsInBackground || reason == .remoteHangUp || commands > 0 {
            scheduleReconnect()
        } else {
            setLink(dropped.map { .unreachable($0) } ?? .off)
        }
    }

    /// session.c `rest_conditions`: camping master off and shore power neither active nor standby,
    /// both known, and no command in flight. A missing frame or field means "reconnect as usual".
    private var restConditions: Bool {
        guard commands == 0, state.fields["campingmode"]?["State"] == 0,
              case .string(let shore)? = state.interpreted["energy"]?["shore_state"]
        else { return false }
        return shore != "active" && shore != "standby"
    }

    private func scheduleReconnect() {
        guard reconnect == nil else { return }
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
            if !isReady { scheduleReconnect() }
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
    /// `CODEC_HEARTBEAT_PERIOD_MS`, first beat at link up, until the link goes down. A beat that
    /// cannot be written ends the link (session.c `link_lost("heartbeat")`).
    private func startHeartbeat(gen: Int) {
        stopHeartbeat()
        heartbeat = Task { [transport, clock] in
            var counter = CamperGATT.heartbeatStart
            while !Task.isCancelled {
                do {
                    try await transport.write(CamperGATT.heartbeat, withUnsafeBytes(of: counter.bigEndian, Array.init))
                } catch TransportError.notConnected {
                    return  // the disconnect event follows and stops the beat
                } catch {
                    await self.heartbeatFailed(gen)
                    return
                }
                if counter == CamperGATT.heartbeatStart { self.firstBeatWritten(gen) }
                counter &+= 1
                do { try await clock.sleep(for: CamperGATT.heartbeatPeriod) } catch { return }
            }
        }
    }

    private func firstBeatWritten(_ gen: Int) {
        if gen == linkGen, heartbeat != nil { firstBeat = clock.stopwatch() }
    }

    private func heartbeatFailed(_ gen: Int) async {
        guard gen == linkGen else { return }
        await dropLink("The heartbeat could not be written.")
    }

    private func stopHeartbeat() {
        heartbeat?.cancel()
        heartbeat = nil
        firstBeat = nil
    }

    // MARK: state

    private func store(_ char: UInt16, _ bytes: [UInt8]) {
        guard let fn = charToFunction[char], let fields = try? CaliCodec.decode(function: fn, bytes: bytes) else { return }
        if fn == "water" {
            // The stale-latch guard (ports.c): hold the last plausible reading, or store nothing on a cold start.
            let t = elapsed().components
            let ms = UInt64(max(0, t.seconds)) * 1000 + UInt64(max(0, t.attoseconds) / 1_000_000_000_000_000)
            if waterGuard.settle(WaterLevels(fields: fields), nowMs: ms) {
                state.waterHeld = false
                state.waterGoodAt = Date()
            } else {
                state.waterHeld = true
                guard state.fields["water"] != nil else { return }
                liveFunctions.insert(fn)
                state.asOf = Date()
                publish()
                return
            }
        }
        liveFunctions.insert(fn)
        if fn == "lighting" {
            // A read or a push of the unit's own frame: never one of our writes (R4). A config or ack frame
            // only feeds the latch and keeps the stored lamps (#284, session.c `keep`).
            state.lightConfig = CaliControl.lightConfig(previous: state.lightConfig, frame: fields)
            if !CaliControl.lightReportsState(fields), let kept = state.fields[fn], CaliControl.lightReportsState(kept) {
                state.asOf = Date()
                publish()
                return
            }
        }
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

extension Clock {
    /// The time left of `budget` counted from this call.
    func countdown(_ budget: Duration) -> @Sendable () -> Duration {
        let end = now.advanced(by: budget)
        return { self.now.duration(to: end) }
    }
}

/// Resumes its continuation once: with the work's result or with the timer's timeout.
private final class Once: @unchecked Sendable {  // @unchecked: all state behind `lock`
    private let lock = NSLock()
    private var cont: CheckedContinuation<Void, any Error>?
    private var timer: Task<Void, Never>?

    init(_ cont: CheckedContinuation<Void, any Error>) { self.cont = cont }

    func setTimer(_ t: Task<Void, Never>) {
        let done = lock.withLock { () -> Bool in
            if cont == nil { return true }
            timer = t
            return false
        }
        if done { t.cancel() }
    }

    func finish(_ r: Result<Void, any Error>) {
        let (c, t) = lock.withLock { () -> (CheckedContinuation<Void, any Error>?, Task<Void, Never>?) in
            defer { cont = nil; timer = nil }
            return (cont, timer)
        }
        t?.cancel()
        c?.resume(with: r)
    }
}

/// Runs `op`, but returns by `budget` on `clock` whether or not `op` honours cancellation
/// (a BLE read can hang). On the deadline `op` is cancelled and `SessionError.unreachable` thrown.
func race(_ clock: any Clock<Duration>, _ budget: Duration,
                  _ op: @escaping @Sendable () async throws -> Void) async throws {
    guard budget > .zero else { throw SessionError.unreachable }
    try await withCheckedThrowingContinuation { (cont: CheckedContinuation<Void, any Error>) in
        let once = Once(cont)
        let work = Task {
            do { try await op(); once.finish(.success(())) } catch { once.finish(.failure(error)) }
        }
        once.setTimer(Task {
            do { try await clock.sleep(for: budget) } catch { return }
            work.cancel()
            once.finish(.failure(SessionError.unreachable))
        })
    }
}
