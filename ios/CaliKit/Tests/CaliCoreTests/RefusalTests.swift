import Foundation
import Testing

@testable import CaliCore

/// Every refusal text of the C planner (`control_consts.h`) is a typed case, so the app can show it
/// in the user's language instead of the planner's English.
struct RefusalTests {
    /// - Test: T_IOS_REFUSAL_TYPED
    /// - Verifies: R_IOS_I18N
    @Test func everyPlannerReasonIsATypedCase() throws {
        let root = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
            .deletingLastPathComponent().deletingLastPathComponent()
        let header = try String(contentsOf: root.appending(path: "csrc/control_consts.h"), encoding: .utf8)
        let define = try Regex(#"#define (CALI_REASON_\w+|CALI_WAKEUP_UNKNOWN) "((?:[^"\\]|\\.)*)""#)
        var seen = 0
        for m in header.matches(of: define) {
            guard let text = Self.unescape(String(m.output[2].substring!)) else {
                Issue.record("\(m.output[1].substring!): an escape this test does not know")
                continue
            }
            #expect(Refusal(planner: text) != .unknown, "\(m.output[1].substring!) has no Refusal case")
            seen += 1
        }
        #expect(seen >= 16)
        #expect(Refusal(planner: "something new") == .unknown)
        #expect(Self.unescape(#"a\?b\"c\\d\342\200\224"#) == "a?b\"c\\d—")
        #expect(Self.unescape(#"bad \q"#) == nil)
    }

    /// A C string literal's body as the compiler reads it: octal escapes (`\342\200\224` = "—", as
    /// `tools/gen_c_dict.py` writes non-ASCII) and `\?`, `\"`, `\\`, `\n`; nil for any other escape.
    static func unescape(_ s: String) -> String? {
        var bytes: [UInt8] = []
        var it = Array(s.utf8)[...]
        while let b = it.popFirst() {
            guard b == UInt8(ascii: "\\") else {
                bytes.append(b)
                continue
            }
            guard let e = it.popFirst() else { return nil }
            switch e {
            case UInt8(ascii: "0")...UInt8(ascii: "7"):
                var digits = [e]
                while digits.count < 3, let d = it.first, (UInt8(ascii: "0")...UInt8(ascii: "7")).contains(d) {
                    digits.append(it.removeFirst())
                }
                guard let octal = String(bytes: digits, encoding: .ascii), let v = UInt8(octal, radix: 8) else { return nil }
                bytes.append(v)
            case UInt8(ascii: "?"), UInt8(ascii: "\""), UInt8(ascii: "\\"), UInt8(ascii: "'"): bytes.append(e)
            case UInt8(ascii: "n"): bytes.append(10)
            default: return nil
            }
        }
        return String(bytes: bytes, encoding: .utf8)
    }
}
