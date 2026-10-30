import CCodec
import Foundation

/// The unit's GATT map. The char tables are read from the generated C headers (`codec_chars.h`,
/// `control_consts.h`) that the satellite firmware also uses; nothing is typed by hand here.
///
/// - Requirement: R_IOS_BLE_DIRECT
public enum CamperGATT {
    /// The 128-bit UUID of a char's short id, lowercase (`CODEC_UUID_FMT`).
    public static func uuid(_ short: UInt16) -> String {
        String(format: CODEC_UUID_FMT, UInt32(short))
    }

    /// function name -> state char short id.
    public static let stateChars: [String: UInt16] = table(CODEC_CHARS, Int(CODEC_NCHARS)) { (e: codec_char) in (e.function, e.state_short) }

    /// function name -> control char short id (the five writable functions).
    public static let controlChars: [String: UInt16] = table(CALI_CTL_CHARS, Int(CALI_CTL_NCHARS)) { (e: cali_ctl_char) in (e.function, e.control_short) }

    public static let heartbeat = UInt16(CODEC_CHAR_HEARTBEAT)
    /// The authenticated read (`vehicle`): it succeeds only over an encrypted, bonded link.
    public static let auth = UInt16(CODEC_CHAR_AUTH)
    /// The unit's advertised name.
    public static let deviceName = CODEC_DEVICE_NAME
    /// The heartbeat's first counter value; +1 per beat, written as 4 bytes big-endian.
    public static let heartbeatStart = UInt32(CODEC_HEARTBEAT_START)
    public static let heartbeatPeriod = Duration.milliseconds(Int(CODEC_HEARTBEAT_PERIOD_MS))
    /// A link that came up for a command waits this long after its first beat before the first
    /// frame (`CODEC_ARM_DELAY_MS`, calictl `device.ARM_DELAY_S`).
    public static let armDelay = Duration.milliseconds(Int(CODEC_ARM_DELAY_MS))

    /// Chars of the V3 protocol; this app never writes them (`R_IOS_V2_ONLY`).
    public static let forbiddenWrites: Set<UInt16> = [0x1603, 0xF002]

    // A C array imports as a tuple; Mirror walks its elements.
    private static func table<E>(_ tuple: Any, _ n: Int, _ pick: (E) -> (UnsafePointer<CChar>?, UInt16)) -> [String: UInt16] {
        var out: [String: UInt16] = [:]
        for child in Mirror(reflecting: tuple).children.prefix(n) {
            let (name, id) = pick(child.value as! E)
            out[String(cString: name!)] = id
        }
        return out
    }
}
