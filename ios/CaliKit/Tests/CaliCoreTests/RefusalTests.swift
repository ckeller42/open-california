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
            // The header writes non-ASCII as octal escapes (\342\200\224 = "—").
            var bytes: [UInt8] = []
            var it = String(m.output[2].substring!).utf8.makeIterator()
            while let b = it.next() {
                if b == UInt8(ascii: "\\"), let d1 = it.next(), let d2 = it.next(), let d3 = it.next() {
                    bytes.append(UInt8(String(decoding: [d1, d2, d3], as: UTF8.self), radix: 8)!)
                } else { bytes.append(b) }
            }
            let text = String(decoding: bytes, as: UTF8.self)
            #expect(Refusal(planner: text) != .unknown, "\(m.output[1].substring!) has no Refusal case")
            seen += 1
        }
        #expect(seen >= 16)
        #expect(Refusal(planner: "something new") == .unknown)
    }
}
