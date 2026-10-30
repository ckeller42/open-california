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

    init(session: CamperSession, state: CamperState = CamperState(), link: LinkState = .off) {
        self.session = session
        self.state = state
        self.link = link
    }

    /// No frame at all, or the last frame is older than 90 s.
    var isStale: Bool {
        guard let asOf = state.asOf else { return true }
        return now().timeIntervalSince(asOf) > Self.staleAfter
    }

    var controlsEnabled: Bool { link == .up && !isStale && state.gate == .supported }

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
                if state.asOf != nil { self.state = state }
                self.link = link
            }
        }
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

/// The states the previews, the snapshot tests and the UI tests (`-UITestFixture <name>`) start
/// from. The frames are the defaults of `tools/mock_unit.py` (`MockCamperUnit().read`).
///
/// - Requirement: R_IOS_TESTED
enum Fixture: String, CaseIterable {
    case parked, campingOn, unsupportedV3, unreachable, staleRestore

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
        case .parked, .unreachable, .staleRestore: break
        }
        return hex.mapValues { h in stride(from: 0, to: h.count, by: 2).map { i in
            UInt8(h[h.index(h.startIndex, offsetBy: i)...].prefix(2), radix: 16)!
        } }
    }

    /// The UI tests connect to the fake on launch only for these; the others keep their state.
    var isLive: Bool { self == .parked || self == .campingOn || self == .unsupportedV3 }

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
        st.asOf = switch self {
        case .staleRestore: now.addingTimeInterval(-5 * 3600)
        case .unreachable: now.addingTimeInterval(-600)
        case .parked, .campingOn, .unsupportedV3: now
        }
        return st
    }
}

extension CamperStore {
    static func preview(_ fixture: Fixture) -> CamperStore {
        let link: LinkState = switch fixture {
        case .unreachable: .unreachable("The camper unit did not answer.")
        case .staleRestore: .off
        case .parked, .campingOn, .unsupportedV3: .up
        }
        return CamperStore(session: fixture.makeSession(), state: fixture.state(now: Date()), link: link)
    }
}
