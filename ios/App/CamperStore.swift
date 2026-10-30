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
    var isStale: Bool {
        guard let asOf = state.asOf else { return true }
        return now().timeIntervalSince(asOf) > Self.staleAfter
    }

    /// The age of the shown state ("5 h ago"); nil without a frame.
    var age: LocalizedStringResource? { state.asOf.map { ageText(now().timeIntervalSince($0)) } }

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

    var linkLabel: LocalizedStringResource {
        switch link {
        case .up: isStale ? "offline" : "live"
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

    /// One connect attempt; a failure shows in `link`. No redial loop (`R_IOS_POWER_SAVE`).
    ///
    /// - Requirement: R_IOS_BLE_DIRECT, R_IOS_POWER_SAVE
    func connect() async {
        try? await session.ensureConnected(timeout: .seconds(20))
    }

    func send(_ cmd: Command) async -> CommandResult {
        await session.run(cmd)
    }
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
    case parked, campingOn, unsupportedV3, unreachable, staleRestore, noCooler

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
        case .parked, .unreachable, .staleRestore: break
        }
        return hex.mapValues { h in stride(from: 0, to: h.count, by: 2).map { i in
            UInt8(h[h.index(h.startIndex, offsetBy: i)...].prefix(2), radix: 16)!
        } }
    }

    /// The UI tests connect to the fake on launch only for these; the others keep their state.
    var isLive: Bool { ![.unreachable, .staleRestore].contains(self) }

    /// A session over the fake transport, which answers with this fixture's frames.
    func makeSession() -> CamperSession {
        let byChar = self == .unreachable ? [:] : Dictionary(uniqueKeysWithValues: frames.compactMap { fn, bytes in
            CamperGATT.stateChars[fn].map { ($0, bytes) }
        })
        return CamperSession(transport: FakeCamperTransport(frames: byChar), clock: ContinuousClock())
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
        case .parked, .campingOn, .unsupportedV3, .noCooler: now
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
        case .parked, .campingOn, .unsupportedV3, .noCooler: .up
        }
        let store = CamperStore(session: fixture.makeSession(), state: fixture.state(now: now ?? Date()), link: link)
        if let now { store.now = { now } }
        return store
    }
}
