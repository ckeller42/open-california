import CControl
import Foundation

/// One planned characteristic write: `char` is the short id (`0x1101`), `delayMs` waits after the previous write's ACK.
public struct ControlFrame: Equatable, Sendable {
    public let char: UInt16
    public let delayMs: UInt16
    public let data: [UInt8]

    public init(char: UInt16, delayMs: UInt16, data: [UInt8]) {
        self.char = char
        self.delayMs = delayMs
        self.data = data
    }
}

public enum PlanResult: Equatable, Sendable {
    case frames([ControlFrame])
    /// A `command_precondition` gate; the text is the unit's own wording.
    case refused(String)
    /// Not a command of this app (the roof, or a function without a control twin).
    case elsewhere(String)
    /// Refused only for want of the unit's lighting config: pull it with `REQUEST_CONFIG`, then plan again.
    case needsLightConfig
    /// The value is not valid for the command (the C planner's BAD_VALUE, or NONE: no frame for it).
    case badValue
}

/// The control planner of the satellite (`firmware/components/cali_core/control.c`), linked as it is.
///
/// - Requirement: R_IOS_FUNCTIONS
public enum CaliControl {
    // cali_ctl_plan takes a C callback without a context pointer, so the state sits in a lock-guarded global
    // for the duration of one plan call. ponytail: serialises all plans; they take microseconds.
    private nonisolated(unsafe) static var current: [String: [String: UInt32]] = [:]
    private static let lock = NSLock()

    /// - Parameters:
    ///   - value: calictl's string form of the value (an integer's decimal text; nil = none).
    ///   - localNow: the phone's wall clock read as UTC, in seconds; nil = none.
    ///   - state: the decoded state per function, as `CaliCodec.decode` returns it.
    public static func plan(
        function: String, what: String, value: String?, localNow: Int64?, state: [String: [String: UInt32]]
    ) -> PlanResult {
        lock.lock()
        defer { lock.unlock() }
        current = state
        defer { current = [:] }

        var plan = cali_ctl_plan_t()
        cali_ctl_plan(function, what, value ?? "", localNow ?? -1, { fn, field, out in
            guard let fn, let field, let v = CaliControl.current[String(cString: fn)]?[String(cString: field)]
            else { return 0 }
            out?.pointee = v
            return 1
        }, &plan)

        let reason = plan.reason.map { String(cString: $0) }
        switch Int32(plan.rc) {
        case Int32(CALI_CTL_OK):
            let frames = withUnsafeBytes(of: plan.f) { raw in
                raw.bindMemory(to: cali_ctl_frame_t.self).prefix(plan.n).map { f in
                    ControlFrame(
                        char: f.chr, delayMs: f.delay_ms,
                        data: withUnsafeBytes(of: f.data) { Array($0.prefix(Int(f.len))) })
                }
            }
            return .frames(frames)
        case Int32(CALI_CTL_REFUSED):
            return plan.pull == 1 ? .needsLightConfig : .refused(reason ?? "")
        case Int32(CALI_CTL_ELSEWHERE):
            return .elsewhere(reason ?? "")
        default:  // BAD_VALUE, NONE
            return .badValue
        }
    }
}
