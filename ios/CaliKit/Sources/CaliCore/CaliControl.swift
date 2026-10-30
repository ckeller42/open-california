import CCodec
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
            return .frames(frames(plan))
        case Int32(CALI_CTL_REFUSED):
            return plan.pull == 1 ? .needsLightConfig : .refused(reason ?? "")
        case Int32(CALI_CTL_ELSEWHERE):
            return .elsewhere(reason ?? "")
        default:  // BAD_VALUE, NONE
            return .badValue
        }
    }

    /// Command deadline, counted from the first frame (`CALI_CTL_DEADLINE_MS`).
    public static let deadline = Duration.milliseconds(Int(CALI_CTL_DEADLINE_MS))
    /// How long the unit's reply to `REQUEST_CONFIG` is awaited (`CODEC_CONFIG_PULL_MS`).
    public static let configPull = Duration.milliseconds(Int(CODEC_CONFIG_PULL_MS))
    /// The refusal when the pull did not bring the lighting config (`CALI_WAKEUP_UNKNOWN`).
    public static let wakeupUnknown = CALI_WAKEUP_UNKNOWN

    /// The lighting config pull: `REQUEST_CONFIG`, then the commit (`cali_ctl_pull_plan`).
    public static func pullPlan() -> [ControlFrame] {
        var plan = cali_ctl_plan_t()
        cali_ctl_pull_plan(&plan)
        return frames(plan)
    }

    /// True when this `1502` frame reports the lamps and the active profile, false for a config or
    /// ack frame (`cali_light_reports_state`, semantics `lighting_reports_state`).
    public static func lightReportsState(_ lighting: [String: UInt32]) -> Bool {
        lock.lock()
        defer { lock.unlock() }
        current = ["lighting": lighting]
        defer { current = [:] }
        return cali_light_reports_state { fn, field, out in
            guard let fn, let field, let v = CaliControl.current[String(cString: fn)]?[String(cString: field)]
            else { return 0 }
            out?.pointee = v
            return 1
        } == 1
    }

    /// The write allow-list (`cali_ctl_write_ok`): one of the five control chars at exactly its frame
    /// length. Never the roof's control char, never a V3 char.
    ///
    /// - Requirement: R_IOS_NO_ROOF, R_IOS_V2_ONLY
    public static func writeOK(_ char: UInt16, _ length: Int) -> Bool { cali_ctl_write_ok(char, length) == 1 }

    /// The lighting config latch (`cali_light_cfg`, session.c `latch_lighting`): the keys of
    /// `previous` (`CALI_LCFG_KEYS`), then what the unit's own `1502` frame adds (wake-up, door
    /// contact, stored favourites). Feed it only frames the unit sent, never one of our writes.
    ///
    /// - Requirement: R_IOS_FUNCTIONS, R_IOS_HONEST
    public static func lightConfig(previous: [String: UInt32], frame: [String: UInt32]) -> [String: UInt32] {
        lock.lock()
        defer { lock.unlock() }
        // the latch first, then the frame (session.c lcfg_get); the names do not overlap
        current = ["lighting": frame.merging(previous) { $1 }]
        defer { current = [:] }
        var cfg = cali_light_cfg_t()
        cali_light_cfg({ fn, field, out in
            guard let fn, let field, let v = CaliControl.current[String(cString: fn)]?[String(cString: field)]
            else { return 0 }
            out?.pointee = v
            return 1
        }, &cfg)
        var out: [String: UInt32] = [:]
        let values = withUnsafeBytes(of: cfg.v) { Array($0.bindMemory(to: UInt32.self)) }
        for (k, key) in lightConfigKeys.enumerated() where cfg.have >> k & 1 == 1 { out[key] = values[k] }
        return out
    }

    /// `CALI_LCFG_KEYS`, in its order.
    static let lightConfigKeys: [String] = withUnsafeBytes(of: CALI_LCFG_KEYS) { raw in
        raw.bindMemory(to: UnsafePointer<CChar>?.self).prefix(Int(CALI_LCFG_N)).map { String(cString: $0!) }
    }

    private static func frames(_ plan: cali_ctl_plan_t) -> [ControlFrame] {
        withUnsafeBytes(of: plan.f) { raw in
            raw.bindMemory(to: cali_ctl_frame_t.self).prefix(plan.n).map { f in
                ControlFrame(
                    char: f.chr, delayMs: f.delay_ms,
                    data: withUnsafeBytes(of: f.data) { Array($0.prefix(Int(f.len))) })
            }
        }
    }
}
