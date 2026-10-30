import Foundation
import Testing
@testable import CaliCore

/// The repo root, found from this file's path (`#filePath`): `ios/CaliKit/Tests/CaliCoreTests/<file>`.
func repoRoot(_ file: String = #filePath) -> URL {
    var url = URL(fileURLWithPath: file)
    for _ in 0..<5 { url.deleteLastPathComponent() }
    return url
}

func loadVectors(_ name: String) throws -> [String: Any] {
    let data = try Data(contentsOf: repoRoot().appendingPathComponent("tests/vectors/\(name)"))
    return try #require(try JSONSerialization.jsonObject(with: data) as? [String: Any])
}

func bytes(hex: String) -> [UInt8] {
    var out: [UInt8] = []
    var it = hex.makeIterator()
    while let a = it.next(), let b = it.next() { out.append(UInt8(String([a, b]), radix: 16)!) }
    return out
}

struct CodecVectorTests {
    /// - Test: T_IOS_CODEC_VECTORS
    /// - Verifies: R_IOS_TESTED, R_IOS_FUNCTIONS
    @Test func decodeMatchesCamperCodecVectors() throws {
        let cases = try #require(try loadVectors("camper_codec.json")["decode"] as? [[String: Any]])
        var checked = 0
        for c in cases {
            let fn = c["function"] as! String
            let want = (c["expect"] as! [String: NSNumber]).mapValues { $0.uint32Value }
            let got = try CaliCodec.decode(function: fn, bytes: bytes(hex: c["raw_hex"] as! String))
            #expect(got == want, "\(c["id"] as! String)")
            checked += 1
        }
        #expect(checked > 0)
        #expect(checked == cases.count)
    }

    /// - Test: T_IOS_CODEC_UNKNOWN_FUNCTION
    /// - Verifies: R_IOS_TESTED, R_IOS_FUNCTIONS
    @Test func unknownFunctionThrows() {
        #expect(throws: CaliCodecError.unknownFunction) {
            try CaliCodec.decode(function: "nope", bytes: [0])
        }
    }
}
