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

    /// A T7 (CarVariant 0) has no Grand California functions; `general` comes first, the rest sorted,
    /// the same list on every call.
    ///
    /// - Test: T_IOS_GATT_VARIANT_T7
    /// - Verifies: R_IOS_BLE_DIRECT
    @Test func t7DropsTheGrandCaliforniaFunctions() {
        let fns = CamperGATT.stateChars(forVariant: 0).map(\.function)
        #expect(fns.first == "general")
        #expect(Array(fns.dropFirst()) == fns.dropFirst().sorted())
        for gc in ["satelliteantenna", "stairs", "livingroomheater", "roofaircondition"] {
            #expect(!fns.contains(gc), "\(gc)")
        }
        for t7 in ["roof", "energy", "water", "airheater", "cooler", "lighting", "campingmode", "vehicle"] {
            #expect(fns.contains(t7), "\(t7)")
        }
    }

    /// The gate works both ways: a Grand California (CarVariant 1) has no roof but has the GC functions.
    ///
    /// - Test: T_IOS_GATT_VARIANT_GC
    /// - Verifies: R_IOS_BLE_DIRECT
    @Test func grandCaliforniaHasNoRoof() {
        let fns = CamperGATT.stateChars(forVariant: 1).map(\.function)
        #expect(!fns.contains("roof"))
        #expect(fns.contains("stairs") && fns.contains("satelliteantenna"))
    }

    /// Without a 1004 frame (variant unknown) every function; any other CarVariant counts as a T7.
    ///
    /// - Test: T_IOS_GATT_VARIANT_UNKNOWN
    /// - Verifies: R_IOS_BLE_DIRECT
    @Test func unknownVariantIsEveryFunction() {
        #expect(CamperGATT.stateChars(forVariant: nil).count == CamperGATT.stateChars.count)
        #expect(CamperGATT.stateChars(forVariant: 7).map(\.function) == CamperGATT.stateChars(forVariant: 0).map(\.function))
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
