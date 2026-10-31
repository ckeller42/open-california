import CaliBLE
import CaliCore
import Foundation
import Observation

/// What the screens show: the session's latest state and link, and whether a control may be used.
///
/// A state restored at launch (or a fixture's) stays until the session has a frame of its own, so a
/// relaunch shows the old values with their age. Controls need a link that is up, a frame younger
/// than 90 s and a supported unit; a command itself plans only over the session's own frames.
///
/// - Requirement: R_IOS_HONEST, R_IOS_V2_ONLY
@MainActor @Observable
final class CamperStore {
    /// The web UI's `SAT_OFFLINE_S` (`semantics.js`): data older than this is stale.
    static let staleAfter: TimeInterval = 90

    var state: CamperState
    var link: LinkState
    @ObservationIgnored let session: CamperSession
    @ObservationIgnored var now: () -> Date = Date.init
    @ObservationIgnored private var follow: Task<Void, Never>?
    /// Where each new state is saved for a relaunch (nil: not saved, as for fixtures).
    @ObservationIgnored let saveURL: URL?

    init(session: CamperSession, state: CamperState = CamperState(), link: LinkState = .off, saveURL: URL? = nil) {
        self.session = session
        self.state = state
        self.link = link
        self.saveURL = saveURL
    }

    /// The app's saved state: Application Support/last_state.json (the unit's decoded frames only).
    static var defaultSaveURL: URL { URL.applicationSupportDirectory.appending(path: "last_state.json") }

    /// The state saved at `url` with its `asOf`, as a relaunch shows it: no function is live (the
    /// controls wait for this link's read pass) and the link's lighting latch is empty.
    ///
    /// - Requirement: R_IOS_BACKGROUND_LINK, R_IOS_HONEST
    static func restore(from url: URL) -> CamperState? {
        guard let data = try? Data(contentsOf: url), var st = try? JSONDecoder().decode(CamperState.self, from: data) else {
            return nil
        }
        st.liveFunctions = []
        st.lightConfig = [:]
        return st
    }

    private func save() {
        guard let saveURL, let data = try? JSONEncoder().encode(state) else { return }
        try? FileManager.default.createDirectory(at: saveURL.deletingLastPathComponent(), withIntermediateDirectories: true)
        // ponytail: one small file per update; a lost write only costs the relaunch view its newest frame.
        try? data.write(to: saveURL, options: .atomic)
    }

    /// No frame at all, or the last frame is older than 90 s.
    var isStale: Bool { isStale(at: now()) }

    /// `isStale` at `date` (the screens pass their `TimelineView` tick).
    func isStale(at date: Date) -> Bool {
        guard let asOf = state.asOf else { return true }
        return date.timeIntervalSince(asOf) > Self.staleAfter
    }

    /// The age of the shown state at `date` ("5 h ago"); nil without a frame.
    func age(at date: Date) -> LocalizedStringResource? { state.asOf.map { ageText(date.timeIntervalSince($0)) } }
    var age: LocalizedStringResource? { age(at: now()) }

    /// The interpreted value `key` of `function`, nil when absent.
    func value(_ function: String, _ key: String) -> JSONValue? { state.interpreted[function]?[key] }

    /// The unit reports `function` as installed (`R_IOS_INSTALLED`: no tile or row otherwise).
    ///
    /// - Requirement: R_IOS_INSTALLED
    func installed(_ function: String) -> Bool { value(function, "installed")?.bool == true }

    /// The link is up, this link has read `general` (the runner plans only over frames of the current
    /// link), the data is fresh and the unit is supported.
    var controlsEnabled: Bool {
        link == .up && state.liveFunctions.contains("general") && !isStale && state.gate == .supported
    }

    /// `controlsEnabled`, and this link has a frame of `function` (else the runner answers `.notReady`).
    func controlEnabled(_ function: String) -> Bool {
        controlsEnabled && state.liveFunctions.contains(function)
    }

    /// Why the controls of `function` are locked, in the words of the link state; nil when usable.
    /// In the power-save rest a tap connects again (`R_IOS_POWER_SAVE`: the next user action redials).
    ///
    /// - Requirement: R_IOS_HONEST, R_IOS_V2_ONLY, R_IOS_POWER_SAVE
    func lockReason(_ function: String) -> LocalizedStringResource? {
        if controlEnabled(function) { return nil }
        if case .unsupported = state.gate { return "This camper unit is not supported yet" }
        // The pill's words for the link state.
        switch link {
        case .rest: return "Disconnected to save energy — tap to connect"
        case .connecting: return "Connecting…"
        case .pairing: return "Not available while pairing"
        case .unreachable: return "Not available while the van is unreachable"
        case .up where !isStale: return "Waiting for the camper unit's data"
        case .up, .off: return "Not available while the van is offline"
        }
    }

    /// What the user sees of the last action, pinned under the pill: a command or a tap-to-connect.
    var feedback: Feedback?
    @ObservationIgnored private var feedbackGen = 0

    /// A command is on its way (the controls wait for it).
    var isSending: Bool { if case .sending = feedback { true } else { false } }

    /// Sends `cmd` with the feedback: "Sending…", then the result, which clears after 8 s.
    ///
    /// - Requirement: R_IOS_HONEST
    @discardableResult
    func run(_ cmd: Command) async -> CommandResult {
        show(.sending(function: cmd.function), clearAfter: nil)
        let r = await send(cmd)
        show(.result(r, function: cmd.function), clearAfter: 8)
        return r
    }

    /// The app is in the foreground (`RootView` follows the scene phase).
    var inForeground = true

    /// The user's tap in the power-save rest: "Connecting in N s…" while the unit's reconnect cooldown
    /// runs, then one dial ("Connecting…") and its outcome; nothing once connected. A tap that the app
    /// left for the background before the cooldown ended does not dial (`R_IOS_POWER_SAVE`: only a
    /// user action dials, once).
    ///
    /// - Requirement: R_IOS_POWER_SAVE, R_IOS_HONEST
    func tapToConnect() async {
        var until: Date?
        if case .rest(let u) = link, u > now() { until = u }
        show(.connecting(until: until), clearAfter: nil)
        if let until {
            try? await Task.sleep(for: .seconds(until.timeIntervalSince(now())))
            guard inForeground else { return show(nil, clearAfter: nil) }
        }
        let outcome = await connect()
        switch outcome {
        case .connected: show(nil, clearAfter: nil)
        case .resting(let until): show(.connect(outcome), clearAfter: max(1, until.timeIntervalSince(now())))
        case .failed: show(.connect(outcome), clearAfter: 8)
        }
    }

    private func show(_ f: Feedback?, clearAfter seconds: TimeInterval?) {
        feedbackGen += 1
        let gen = feedbackGen
        feedback = f
        guard let seconds else { return }
        Task { [weak self] in
            try? await Task.sleep(for: .seconds(seconds))
            if let self, self.feedbackGen == gen { self.feedback = nil }
        }
    }

    /// The link rests to save the unit's energy; a user action may connect again.
    var resting: Bool { if case .rest = link { true } else { false } }

    var linkLabel: LocalizedStringResource { linkLabel(at: now()) }

    func linkLabel(at date: Date) -> LocalizedStringResource {
        if bond == .notPaired, link != .up { return "not paired" }
        return switch link {
        case .up: isStale(at: date) ? "offline" : "live"
        case .connecting: "connecting…"
        case .pairing: "Pairing"
        case .rest: "Disconnected to save energy"
        case .unreachable: "unreachable"
        case .off: "offline"
        }
    }

    /// Follows the session's updates. A session state without a frame (`asOf` nil) does not replace
    /// the state shown, so a restored state keeps its age until this link has read the unit.
    func start() {
        guard follow == nil else { return }
        // ponytail: never cancelled; the store lives as long as the app.
        follow = Task { [weak self, session] in
            for await (state, link) in await session.updates() {
                guard let self else { return }
                self.apply(state, link)
            }
        }
    }

    /// One update of the session.
    ///
    /// - Requirement: R_IOS_HONEST
    func apply(_ state: CamperState, _ link: LinkState) {
        self.link = link
        guard state.asOf != nil else { return }
        var state = state
        // The water guard stores nothing on a cold start: the reading shown so far stays, as held.
        if state.fields["water"] == nil, let water = self.state.fields["water"] {
            state.fields["water"] = water
            state.interpreted["water"] = self.state.interpreted["water"]
            state.waterHeld = true
            state.waterGoodAt = self.state.waterGoodAt ?? self.state.asOf
        }
        self.state = state
        save()
    }

    /// One connect attempt and its outcome; it waits out the unit's reconnect cooldown first. No
    /// redial loop (`R_IOS_POWER_SAVE`).
    ///
    /// - Requirement: R_IOS_BLE_DIRECT, R_IOS_POWER_SAVE
    @discardableResult
    func connect() async -> ConnectOutcome {
        do {
            try await session.ensureConnected(timeout: .seconds(20))
            bond = .paired
            return .connected
        } catch {
            note(error)
            if case .resting(let s) = Failure(error) { return .resting(until: now().addingTimeInterval(TimeInterval(s))) }
            return .failed(Failure(error))
        }
    }

    func send(_ cmd: Command) async -> CommandResult {
        await session.run(cmd)
    }

    // MARK: first pairing

    /// What the app knows of the iPhone's bond with the unit, from the last connect or pairing.
    var bond = Bond.unknown

    private func note(_ error: Error) {
        switch error as? SessionError {
        case .notPaired?: bond = .notPaired
        case .pairingRequired?: bond = .stale
        default: break
        }
    }

    /// The units in range for the guide's pick list (a 3 s scan).
    ///
    /// - Requirement: R_IOS_PAIRING
    func pairingCandidates() async -> Result<[PairingCandidate], PairingScanError> {
        do { return .success(try await session.pairingCandidates()) } catch { return .failure(error as? PairingScanError ?? .busy) }
    }

    /// The user's pick: the session ends the current link and pairs with `id` (`firstPairing`).
    ///
    /// - Requirement: R_IOS_PAIRING
    func firstPairing(with id: UUID) async -> Result<PairingOutcome, SessionError> {
        do {
            let outcome = try await session.firstPairing(with: id)
            bond = .paired
            return .success(outcome)
        } catch {
            note(error)
            return .failure(error as? SessionError ?? .unreachable)
        }
    }

    /// The user cancelled a waiting step of the guide: the pick's link ends.
    func cancelPairing() async { await session.cancelPairing() }

    /// Hides a shown result (a tap on the banner); "Sending…" and "Connecting…" stay.
    func dismissFeedback() {
        if feedback?.dismissible == true { show(nil, clearAfter: nil) }
    }
}

/// The iPhone's bond with the unit, as far as the app knows it.
enum Bond: Equatable {
    case unknown
    /// No unit adopted yet: the guide starts at step 1.
    case notPaired
    case paired
    /// The unit no longer knows this iPhone (for example after a Bluetooth reset on the unit).
    case stale
}

/// What the user sees of their last action.
enum Feedback: Equatable {
    case sending(function: String)
    case result(CommandResult, function: String)
    /// A tap-to-connect: the dial waits until `until` (the unit's cooldown), nil = dialling now.
    case connecting(until: Date?)
    case connect(ConnectOutcome)

    /// A result the user can tap away (not the waiting states).
    var dismissible: Bool {
        switch self {
        case .result, .connect: true
        case .sending, .connecting: false
        }
    }
}

/// The outcome of a connect attempt.
enum ConnectOutcome: Equatable {
    case connected
    /// The unit's reconnect cooldown runs until this time.
    case resting(until: Date)
    case failed(Failure)
}

/// An age like the web UI's `agoText`: "just now" under 90 s, then minutes, hours (under 48 h), days.
///
/// - Requirement: R_IOS_HONEST, R_IOS_I18N
func ageText(_ seconds: TimeInterval) -> LocalizedStringResource {
    let sec = max(0, seconds)
    if sec < 90 { return "just now" }
    let m = Int((sec / 60).rounded())
    if m < 90 { return "\(m) min ago" }
    let h = Int((Double(m) / 60).rounded())
    return h < 48 ? "\(h) h ago" : "\(Int((Double(h) / 24).rounded())) d ago"
}

extension LocalizedStringResource {
    /// This text in `locale`: the views pass their environment locale, so a snapshot can pin it.
    func string(_ locale: Locale) -> String {
        var r = self
        r.locale = locale
        return String(localized: r)
    }
}

extension JSONValue {
    var bool: Bool? { if case .bool(let v) = self { v } else { nil } }
    var number: Double? { if case .number(let v) = self { v } else { nil } }
    var string: String? { if case .string(let v) = self { v } else { nil } }
    subscript(key: String) -> JSONValue? { if case .object(let o) = self { o[key] } else { nil } }
}

/// The states the previews, the snapshot tests and the UI tests (`-UITestFixture <name>`) start
/// from. The frames are the defaults of `tools/mock_unit.py` (`MockCamperUnit().read`).
///
/// - Requirement: R_IOS_TESTED
enum Fixture: String, CaseIterable {
    case parked, campingOn, unsupportedV3, unreachable, staleRestore, noCooler, faults

    private static let mockFrames: [String: String] = [
        "airheater": "10000000000000",
        "campingmode": "20",
        "cooler": "0843000000000000",
        "energy": "00c0500a00ff810000003a00308d0000fffe00000000",
        "general": "303431303032303702",
        "generalpurposesignals": "000000000000000000000000000000000000000000",
        "lighting": "000000000000000000000000d00ddddd",
        "livingroomheater": "000000",
        "roofaircondition": "000000",
        "satelliteantenna": "000000",
        "stairs": "00",
        "water": "030b1d010016",
    ]

    /// Frames per function; the roof is left out (this app has no roof control).
    var frames: [String: [UInt8]] {
        var hex = Self.mockFrames
        switch self {
        case .campingOn: hex["campingmode"] = "21"
        case .unsupportedV3: hex["general"] = "303431303032303703"  // CommunicationVersion 3
        case .noCooler: hex["cooler"] = "0043000000000000"  // Installed (bit 4) cleared
        case .faults:
            // Second battery level 3 (30 %), -4.5 A, faults TwoBattNotCharged + CurrentDeratingTemperature;
            // fresh water pump protection (FreshWaterInfoPopUp 1). Starter power stays "—" (IOneBattBemAfs 0x81).
            hex["energy"] = "01c4500300ff810000003a00308dffd3fffe00000000"
            hex["water"] = "130b1d010016"
        case .parked, .unreachable, .staleRestore: break
        }
        return hex.mapValues { h in stride(from: 0, to: h.count, by: 2).map { i in
            UInt8(h[h.index(h.startIndex, offsetBy: i)...].prefix(2), radix: 16)!
        } }
    }

    /// The UI tests connect to the fake on launch only for these; the others keep their state.
    var isLive: Bool { ![.unreachable, .staleRestore, .faults].contains(self) }

    /// A session over the fake transport, which answers with this fixture's frames.
    func makeSession() -> CamperSession {
        let byChar = self == .unreachable ? [:] : Dictionary(uniqueKeysWithValues: frames.compactMap { fn, bytes in
            CamperGATT.stateChars[fn].map { ($0, bytes) }
        })
        // One unit in range for the pairing guide's scan (the UI tests pick it).
        let unit = PairingCandidate(id: UUID(uuidString: "00000000-0000-4000-8000-000000000001")!, name: CamperGATT.deviceName, rssi: -60)
        return CamperSession(transport: FakeCamperTransport(frames: byChar, candidates: [unit]), clock: ContinuousClock())
    }

    /// The state as a read pass would leave it, `asOf` = `now` (10 min earlier for `unreachable`,
    /// 5 h earlier for `staleRestore`).
    func state(now: Date) -> CamperState {
        var st = CamperState()
        for (fn, bytes) in frames {
            guard let fields = try? CaliCodec.decode(function: fn, bytes: bytes) else { continue }
            st.fields[fn] = fields
            st.interpreted[fn] = try? Semantics.shared.interpret(function: fn, fields: fields)
        }
        st.interpreted = (try? applySwCorrections(st.interpreted)) ?? st.interpreted
        st.gate = ProtocolGate(general: st.fields["general"])
        if isLive { st.liveFunctions = Set(st.fields.keys) }  // a restored state has no live link
        st.asOf = switch self {
        case .staleRestore: now.addingTimeInterval(-5 * 3600)
        case .unreachable: now.addingTimeInterval(-600)
        case .parked, .campingOn, .unsupportedV3, .noCooler, .faults: now
        }
        return st
    }
}

extension CamperStore {
    /// - Parameter now: a fixed clock (snapshots); nil keeps the real one.
    static func preview(_ fixture: Fixture, now: Date? = nil) -> CamperStore {
        let link: LinkState = switch fixture {
        case .unreachable: .unreachable("The camper unit did not answer.")
        case .staleRestore: .off
        case .faults: .rest(until: (now ?? Date()).addingTimeInterval(60))  // the power-save rest
        case .parked, .campingOn, .unsupportedV3, .noCooler: .up
        }
        let store = CamperStore(session: fixture.makeSession(), state: fixture.state(now: now ?? Date()), link: link)
        if let now { store.now = { now } }
        return store
    }
}
