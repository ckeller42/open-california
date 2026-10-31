import Foundation
import Testing

@testable import CaliCore

struct WaterGuardTests {
    static func levels(_ d: [String: Any]) -> WaterLevels {
        WaterLevels(
            fresh: (d["fresh"] as? NSNumber)?.uint32Value, waste: (d["waste"] as? NSNumber)?.uint32Value,
            empty: d["empty"] as? Bool ?? false)
    }

    /// - Test: T_IOS_WATER_CASES
    /// - Verifies: R_IOS_TESTED, R_IOS_HONEST, R_IOS_FUNCTIONS
    @Test func stateLatchCasesMatchTheSharedVectors() throws {
        let cases = try #require(try loadVectors("freshness.json")["cases"] as? [[String: Any]])
        #expect(cases.count == 22)
        for c in cases {
            let id = c["id"] as! String
            let got = WaterGuard.implausibleDrop(
                Self.levels(c["new"] as! [String: Any]), prev: Self.levels(c["prev"] as! [String: Any]))
            #expect(got == (c["expect"] as! Bool), "\(id)")
        }
    }

    /// - Test: T_IOS_WATER_SEQUENCES
    /// - Verifies: R_IOS_TESTED, R_IOS_HONEST, R_IOS_FUNCTIONS
    @Test func rampDebounceSequencesMatchTheSharedVectors() throws {
        let seqs = try #require(try loadVectors("freshness.json")["sequences"] as? [[String: Any]])
        #expect(seqs.count >= 15)
        for s in seqs {
            let id = s["id"] as! String
            var g = WaterGuard(baseline: (s["good"] as? [String: Any]).map(Self.levels))
            for st in s["steps"] as! [[Any]] {
                func n(_ v: Any) -> UInt32? { v is NSNull ? nil : (v as! NSNumber).uint32Value }
                let new = WaterLevels(fresh: n(st[1]), waste: n(st[2]), empty: st.count > 4 && (st[4] as! NSNumber).intValue == 1)
                let got = g.settle(new, nowMs: (st[0] as! NSNumber).uint64Value)
                #expect(got == ((st[3] as! NSNumber).intValue == 1), "\(id) @\(st[0])")
            }
        }
    }
}
