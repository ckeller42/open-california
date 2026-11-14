/// Whether the connected unit speaks the protocol this app supports: CommunicationVersion 2 only.
///
/// - Requirement: R_IOS_V2_ONLY
public enum ProtocolGate: Equatable, Sendable, Codable {
    case supported
    case unsupported(version: Int)
    /// No `general` frame yet (or no version field in it).
    case unknown

    /// - Parameter general: the decoded `general` function.
    public init(general: [String: UInt32]?) {
        switch general?["CommunicationVersion"] {
        case nil: self = .unknown
        case 2?: self = .supported
        case let v?: self = .unsupported(version: Int(v))
        }
    }
}
