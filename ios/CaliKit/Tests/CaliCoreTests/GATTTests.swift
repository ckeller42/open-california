import CCodec
import Testing

@testable import CaliCore

struct GATTTests {
    /// - Test: T_IOS_GATT_UUID
    /// - Verifies: R_IOS_BLE_DIRECT
    @Test func uuidFormat() {
        #expect(CamperGATT.uuid(0x1202) == "00001202-6c77-4b7d-bbf6-a5e587701f3d")
        #expect(CamperGATT.uuid(0xF001) == "0000f001-6c77-4b7d-bbf6-a5e587701f3d")
    }

    /// - Test: T_IOS_GATT_TABLES
    /// - Verifies: R_IOS_BLE_DIRECT
    @Test func stateCharsMatchHeader() {
        #expect(CamperGATT.stateChars.count == Int(CODEC_NCHARS))
        #expect(CamperGATT.stateChars.count > 0)
        #expect(CamperGATT.stateChars["campingmode"] == 0x1202)
        #expect(CamperGATT.controlChars.count == Int(CALI_CTL_NCHARS))
        #expect(CamperGATT.controlChars["lighting"] == 0x1501)
        #expect(CamperGATT.heartbeat == UInt16(CODEC_CHAR_HEARTBEAT))
    }

    /// - Test: T_IOS_GATT_NO_FORBIDDEN
    /// - Verifies: R_IOS_V2_ONLY
    @Test func forbiddenWritesNeverPlanned() throws {
        let v = try loadVectors("control.json")
        let cases = v["cases"] as! [[String: Any]]
        var frames = 0
        for c in cases {
            let e = c["expect"] as! [String: Any]
            for f in (e["frames"] as? [[String: Any]]) ?? [] {
                frames += 1
                let chr = UInt16(f["char"] as! String, radix: 16)!
                #expect(!CamperGATT.forbiddenWrites.contains(chr), "\((c["id"] as! String))")
                #expect(CamperGATT.controlChars.values.contains(chr), "\((c["id"] as! String))")
            }
        }
        #expect(frames > 0)
        #expect(CamperGATT.forbiddenWrites == [0x1603, 0xF002])
        #expect(!CamperGATT.controlChars.values.contains(0x1603))
    }
}
