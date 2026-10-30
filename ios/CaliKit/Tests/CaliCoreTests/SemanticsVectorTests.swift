import Foundation
import Testing
@testable import CaliCore

private let python3Available = (ProcessInfo.processInfo.environment["PATH"] ?? "").split(separator: ":")
    .contains { FileManager.default.isExecutableFile(atPath: "\($0)/python3") }

struct SemanticsVectorTests {
    /// - Test: T_IOS_SEMANTICS_VECTORS
    /// - Verifies: R_IOS_TESTED, R_IOS_FUNCTIONS
    @Test func interpretMatchesSemanticsVectors() throws {
        let cases = try #require(try loadVectors("semantics.json")["interpret"] as? [[String: Any]])
        #expect(cases.count > 0)
        let dec = JSONDecoder()
        for c in cases {
            let id = c["id"] as? String ?? "?"
            let fields = (c["fields"] as! [String: NSNumber]).mapValues { $0.uint32Value }
            let want = try dec.decode(
                [String: JSONValue].self, from: JSONSerialization.data(withJSONObject: c["expect"]!))
            // Exact equality: JS and Python both produce IEEE doubles from the same rounding rule (pyRound).
            #expect(try Semantics.shared.interpret(function: c["function"] as! String, fields: fields) == want, "\(id)")
        }
    }

    /// - Test: T_IOS_SEMANTICS_UNKNOWN_FUNCTION
    /// - Verifies: R_IOS_TESTED, R_IOS_FUNCTIONS
    @Test func unknownFunctionGivesGenericViewAndNoError() throws {
        let out = try Semantics.shared.interpret(function: "nope", fields: ["A": 1])
        #expect(!out.isEmpty)
    }

    /// A JS exception is an error with the JS message, never an empty or unchanged result.
    /// - Test: T_IOS_SEMANTICS_ERROR_THROWS
    /// - Verifies: R_IOS_TESTED, R_IOS_HONEST
    @Test func jsExceptionThrowsWithItsMessage() {
        let broken = Semantics(scripts: [("""
            function interpret(f, d) { throw new Error("boom " + f); }
            function applySwCorrections(s) { throw new Error("no fix"); }
            """, nil)])
        #expect(throws: SemanticsError.self) { try broken.interpret(function: "energy", fields: [:]) }
        do { _ = try broken.interpret(function: "energy", fields: [:]) } catch {
            #expect((error as? SemanticsError)?.message.contains("boom energy") == true)
        }
        #expect(throws: SemanticsError.self) { try broken.applySwCorrections(["energy": [:]]) }
        let missing = Semantics(scripts: [], loadError: "missing resource semantics.js")
        #expect(throws: SemanticsError(message: "missing resource semantics.js")) {
            try missing.interpret(function: "energy", fields: [:])
        }
    }

    /// - Test: T_IOS_SEMANTICS_SW_CORRECTION
    /// - Verifies: R_IOS_TESTED, R_IOS_FUNCTIONS
    @Test func swCorrectionsMatchVectors() throws {
        let states = try #require(try loadVectors("semantics.json")["states"] as? [[String: Any]])
        #expect(states.count > 0)
        let dec = JSONDecoder()
        for c in states {
            let id = c["id"] as? String ?? "?"
            var st: [String: [String: JSONValue]] = [:]
            for (k, f) in c["fn"] as! [String: [String: NSNumber]] {
                st[k] = try Semantics.shared.interpret(function: k, fields: f.mapValues { $0.uint32Value })
            }
            let want = try dec.decode(
                [String: [String: JSONValue]].self,
                from: JSONSerialization.data(withJSONObject: (c["expect"] as! [String: Any])["state"]!))
            #expect(try applySwCorrections(st) == want, "\(id)")
        }
    }

    /// - Test: T_IOS_SEMANTICS_FRESH
    /// - Verifies: R_IOS_TESTED, R_IOS_FUNCTIONS
    @Test(.enabled(if: python3Available, "python3 is not on PATH")) func generatedCopiesAreFresh() throws {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        p.arguments = ["python3", "-m", "tools.gen_c_dict", "--check"]
        p.currentDirectoryURL = repoRoot()
        p.standardOutput = FileHandle.nullDevice
        p.standardError = FileHandle.nullDevice
        try p.run()
        p.waitUntilExit()
        #expect(p.terminationStatus == 0, "run: python3 -m tools.gen_c_dict")
    }
}
