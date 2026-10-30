import CaliCore
import Foundation

/// The latest frame per function, as the unit sent it (a read or a push: the last frame wins).
///
/// - Requirement: R_IOS_HONEST
public struct CamperState: Sendable, Equatable {
    /// function -> decoded fields of its latest frame.
    public var fields: [String: [String: UInt32]] = [:]
    /// function -> `semantics.js` interpretation (with `applySwCorrections`). A function whose
    /// interpretation failed is absent here and listed in `semanticsErrors`; its `fields` stay.
    public var interpreted: [String: [String: JSONValue]] = [:]
    /// When the last frame arrived.
    public var asOf: Date?
    public var gate: ProtocolGate = .unknown
    /// function (or `applySwCorrections`) -> the JS error message of its failed interpretation.
    public var semanticsErrors: [String: String] = [:]

    public init() {}
}

public enum LinkState: Sendable, Equatable {
    case off, connecting, up
    /// The power-save rest: no auto-redial; a reconnect waits until `until`.
    case rest(until: Date)
    case unreachable(String)
}

public enum SessionError: Error, Equatable, Sendable {
    /// The unit's reconnect cooldown runs longer than the caller's timeout.
    case inRest(retryAfter: Duration)
    case unreachable
    case bluetooth(BluetoothAvailability)
}
