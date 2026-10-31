import CaliCore
import Foundation

/// The latest frame per function, as the unit sent it (a read or a push: the last frame wins).
///
/// - Requirement: R_IOS_HONEST
public struct CamperState: Sendable, Equatable, Codable {
    /// function -> decoded fields of its latest frame.
    public var fields: [String: [String: UInt32]] = [:]
    /// function -> `semantics.js` interpretation (with `applySwCorrections`). A function whose
    /// interpretation failed is absent here and listed in `semanticsErrors`; its `fields` stay.
    public var interpreted: [String: [String: JSONValue]] = [:]
    /// When the last frame arrived.
    public var asOf: Date?
    public var gate: ProtocolGate = .unknown
    /// Functions with a frame (read or push) from the current link; cleared at each link up. A
    /// command needs its function here (control_run.c `cali_session_frame_live`).
    public var liveFunctions: Set<String> = []
    /// function (or `applySwCorrections`) -> the JS error message of its failed interpretation.
    public var semanticsErrors: [String: String] = [:]
    /// This link's lighting config latch (`CALI_LCFG_KEYS` -> value) from the unit's own `1502`
    /// frames; cleared at each link up (control_run.c plans over the live latch only).
    public var lightConfig: [String: UInt32] = [:]
    /// The stored `water` frame is the last plausible reading, held against a stale 1 L latch or a
    /// measurement ramp (satellite `water_held`). `waterGoodAt` is when it was last adopted.
    public var waterHeld = false
    public var waterGoodAt: Date?

    /// The state the planner sees: the frames, with the lighting config latch answering like
    /// lighting fields (control_run.c `get_field`).
    public var planFields: [String: [String: UInt32]] {
        guard !lightConfig.isEmpty else { return fields }
        var f = fields
        f["lighting", default: [:]].merge(lightConfig) { _, latch in latch }
        return f
    }

    public init() {}
}

public enum LinkState: Sendable, Equatable {
    case off, connecting, up
    /// A first pairing: the user types the code from the camper screen into the iOS dialog.
    case pairing
    /// The power-save rest: no auto-redial; a reconnect waits until `until`.
    case rest(until: Date)
    case unreachable(String)
}

public enum SessionError: Error, Equatable, Sendable {
    /// The unit's reconnect cooldown runs longer than the caller's timeout.
    case inRest(retryAfter: Duration)
    case unreachable
    case bluetooth(BluetoothAvailability)
    /// The saved unit's bond is stale: *Settings → Bluetooth → Forget This Device*, then pair again.
    case pairingRequired
    /// No unit is adopted yet: the user picks one (`firstPairing`).
    case notPaired
}

/// How a first pairing ended.
public enum PairingOutcome: Sendable, Equatable {
    /// iOS paired the iPhone with the unit just now (its passkey dialog was shown).
    case paired
    /// The iPhone was already paired with the unit (a shared bond, AD_IOS_SHARED_BOND): no dialog.
    case alreadyPaired
}
