import Foundation
import Testing
@testable import CaliCore

struct ControlVectorTests {
    typealias State = [String: [String: UInt32]]

    static func value(_ v: Any?) -> String? {
        switch v {
        case nil, is NSNull: return nil
        case let n as NSNumber: return n.stringValue
        case let s as String: return s
        default: fatalError("unexpected value \(String(describing: v))")
        }
    }

    static func state(_ raw: [String: Any]) -> State {
        raw.mapValues { ($0 as! [String: NSNumber]).mapValues { $0.uint32Value } }
    }

    /// Checks one case against its `expect`; returns the kind it asserted.
    static func check(_ c: [String: Any], state: State, pull: String, kinds: inout Set<String>, pulls: inout Int) {
        let id = c["id"] as! String
        let e = c["expect"] as! [String: Any]
        let kind = e["kind"] as! String
        let got = CaliControl.plan(
            function: c["function"] as! String, what: c["what"] as! String,
            value: value(c["value"]), localNow: (c["local_now"] as? NSNumber)?.int64Value, state: state)
        let reason = e["reason"] as? String
        let want: PlanResult
        switch kind {
        case "frames":
            want = .frames((e["frames"] as! [[String: Any]]).map {
                ControlFrame(
                    char: UInt16($0["char"] as! String, radix: 16)!,
                    delayMs: UInt16(truncating: $0["delay_ms"] as! NSNumber),
                    data: bytes(hex: $0["hex"] as! String))
            })
        case "refused": want = reason == pull ? .needsLightConfig : .refused(reason!)
        case "elsewhere": want = .elsewhere(reason!)
        case "bad", "none": want = .badValue   // C rc NONE (build returned None) is a bad value for the app
        default: fatalError("unknown kind \(kind)")
        }
        #expect(got == want, "\(id)")
        kinds.insert(kind)
        if want == .needsLightConfig { pulls += 1 }
    }

    /// - Test: T_IOS_CONTROL_VECTORS
    /// - Verifies: R_IOS_CONTROL_PLAN
    @Test func planMatchesControlVectors() throws {
        let v = try loadVectors("control.json")
        let states = (v["states"] as! [String: Any]).mapValues { Self.state($0 as! [String: Any]) }
        let pull = (v["config_pull"] as! [String: Any])["reason"] as! String
        let cases = v["cases"] as! [[String: Any]]
        var kinds = Set<String>()
        var pulls = 0
        for c in cases { Self.check(c, state: states[c["state"] as! String]!, pull: pull, kinds: &kinds, pulls: &pulls) }
        #expect(cases.count > 0)
        #expect(kinds == ["frames", "refused", "bad", "none", "elsewhere"])
        #expect(pulls > 0, "no vector reached needsLightConfig")
    }

    /// - Test: T_IOS_CONTROL_APP_VECTORS
    /// - Verifies: R_IOS_CONTROL_PLAN
    @Test func planMatchesAppRecordedVectors() throws {
        let v = try loadVectors("control.json")
        let pull = (v["config_pull"] as! [String: Any])["reason"] as! String
        let app = v["app"] as! [[String: Any]]
        var kinds = Set<String>()
        var pulls = 0
        for c in app {
            // The recorded app frames, decoded in Swift, are the state the plan runs over.
            var st: State = [:]
            for (fn, hex) in c["frames_hex"] as! [String: String] {
                st[fn] = try CaliCodec.decode(function: fn, bytes: bytes(hex: hex))
            }
            if let latch = c["latch"] as? [String: NSNumber] {
                st["lighting", default: [:]].merge(latch.mapValues { $0.uint32Value }) { $1 }
            }
            Self.check(c, state: st, pull: pull, kinds: &kinds, pulls: &pulls)
        }
        #expect(app.count > 0)
    }
}
