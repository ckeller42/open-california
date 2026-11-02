import Foundation
import Testing

@testable import CaliBLE
@testable import CaliCore

/// Frames of `tools/mock_unit.py` (its defaults), so `trace_compare` re-packs them byte for byte.
private enum Mock {
    static let general: [UInt8] = hex("303431303032303702")
    static let cooler: [UInt8] = hex("0843000000000000")
    static let camping: [UInt8] = hex("20")
    static let campingOn: [UInt8] = hex("21")
    static let energy: [UInt8] = hex("00c0500a00ff810000003a00308d0000fffe00000000")
    static var frames: [UInt16: [UInt8]] {
        [
            CamperGATT.stateChars["general"]!: general, CamperGATT.stateChars["cooler"]!: cooler,
            CamperGATT.stateChars["campingmode"]!: camping, CamperGATT.stateChars["energy"]!: energy,
        ]
    }
    static func hex(_ s: String) -> [UInt8] {
        stride(from: 0, to: s.count, by: 2).map { UInt8(s.dropFirst($0).prefix(2), radix: 16)! }
    }
}

/// Polls `cond` in real time (the session works on its own tasks) for up to 2 s.
private func until(_ what: String, _ cond: () async -> Bool) async throws {
    for _ in 0..<2000 {
        if await cond() { return }
        try await Task.sleep(for: .milliseconds(1))
    }
    Issue.record("timed out waiting for \(what)")
}

private actor ResultBox {
    var value: CommandResult?
    func set(_ v: CommandResult) { value = v }
}

private struct TraceRig {
    let clock = TestClock()
    let fake: FakeCamperTransport
    let url = FileManager.default.temporaryDirectory.appending(path: "trace-\(UUID().uuidString).jsonl")
    let file: TraceFile
    let s: CamperSession

    init(on: Bool = true) {
        fake = FakeCamperTransport(clock: clock, frames: Mock.frames)
        file = TraceFile(url: url)
        s = CamperSession(transport: TracingTransport(fake, file: file, isOn: { on }), clock: clock)
    }

    /// Runs `cmd`, moving the clock in 100 ms steps until it has its result (the heartbeat beats).
    func run(_ cmd: Command) async throws -> CommandResult {
        let box = ResultBox()
        Task { await box.set(await s.run(cmd)) }
        for _ in 0..<600 {
            if let v = await box.value { return v }
            clock.advance(by: .milliseconds(100))
            try await Task.sleep(for: .milliseconds(1))
        }
        Issue.record("the command did not finish")
        return .timeout
    }

    func lines() -> [String] {
        ((try? String(contentsOf: url, encoding: .utf8)) ?? "").split(separator: "\n").map(String.init)
    }

    func events() throws -> [[String: Any]] {
        try lines().map { try #require(JSONSerialization.jsonObject(with: Data($0.utf8)) as? [String: Any]) }
    }

    func cleanUp() async { await file.delete() }
}

struct TracingTransportTests {
    /// A session over the traced fake connects and runs one command: every line is JSON; reads carry
    /// the char, its function and the fake's frame; the command's write is the frame the fake got;
    /// no heartbeat line; `connect` (with `"addr":null`) comes before the first read.
    ///
    /// With `IOS_TRACE_SAMPLE` set, the trace (with `t` fixed) goes to `tests/vectors/ios_trace_sample.jsonl`
    /// for the cross-language check `tests/test_ios_trace.py`.
    /// - Test: T_IOS_TRACE_SESSION
    /// - Verifies: R_BLE_TRACE, R_IOS_TESTED
    @Test func aTracedSessionWritesTheCalictlFormat() async throws {
        let r = TraceRig()
        let result = try await r.run(Command(function: "cooler", what: "power", value: "on"))
        #expect(result == .sent)
        await r.fake.pushForeign(char: CamperGATT.stateChars["campingmode"]!, bytes: Mock.campingOn)
        try await until("the notify line") { r.lines().contains { $0.contains(#""ev":"notify""#) } }
        await r.fake.dropLink()
        try await until("the disconnect line") { r.lines().last?.contains(#""ev":"disconnect""#) == true }
        #expect(r.lines().last?.hasSuffix(#""addr":null,"reason":"linkLost"}"#) == true)

        let ev = try r.events()
        let firstRead = try #require(ev.firstIndex { $0["ev"] as? String == "read" })
        let connect = try #require(ev.firstIndex { $0["ev"] as? String == "connect" })
        #expect(connect < firstRead)
        #expect(ev[connect]["addr"] is NSNull)
        for e in ev where e["ev"] as? String == "read" {
            let char = try #require((e["char"] as? String).flatMap { UInt16($0, radix: 16) })
            let fn = try #require(e["fn"] as? String)
            #expect(CamperGATT.stateChars[fn] == char || char == CamperGATT.auth)
            #expect(e["hex"] as? String == Mock.frames[char].map { $0.map { String(format: "%02x", $0) }.joined() })
        }
        let campingRead = ev.first { $0["ev"] as? String == "read" && $0["char"] as? String == "1202" }
        #expect(campingRead?["fn"] as? String == "campingmode")
        let commandWrites = await r.fake.writes.filter { $0.char != CamperGATT.heartbeat }
        let traced = ev.filter { $0["ev"] as? String == "write" }
        #expect(traced.map { $0["hex"] as? String } == commandWrites.map { $0.bytes.map { String(format: "%02x", $0) }.joined() })
        #expect(traced.first?["fn"] as? String == "cooler")
        #expect(await r.fake.writes.contains { $0.char == CamperGATT.heartbeat })  // beats were written…
        #expect(!r.lines().contains { $0.contains(#""char":"1003""#) })  // …and not traced
        #expect(ev.contains { $0["ev"] as? String == "notify" && $0["hex"] as? String == "21" })

        if let out = ProcessInfo.processInfo.environment["IOS_TRACE_SAMPLE"], !out.isEmpty {
            // `t` is always the first key: fixed values, 0.5 s apart.
            let fixed = r.lines().enumerated().map { i, line in
                #"{"t":\#(1_700_000_000 + Double(i) / 2)"# + line[line.firstIndex(of: ",")!...]
            }
            let repo = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent()
                .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
            try (fixed.joined(separator: "\n") + "\n").write(
                to: repo.appending(path: "tests/vectors/ios_trace_sample.jsonl"), atomically: true, encoding: .utf8)
        }
        await r.cleanUp()
    }

    /// The switch off: a whole session leaves no file.
    /// - Test: T_IOS_TRACE_OFF
    /// - Verifies: R_BLE_TRACE, R_IOS_TESTED
    @Test func offWritesNoFile() async throws {
        let r = TraceRig(on: false)
        try await r.s.ensureConnected(timeout: .seconds(10))
        await r.fake.pushForeign(char: CamperGATT.stateChars["campingmode"]!, bytes: Mock.campingOn)
        #expect(await r.fake.reads.count > 0)
        #expect(!FileManager.default.fileExists(atPath: r.url.path))
        #expect(!r.file.exists)
    }

    /// At the size cap the file moves to `<name>.1`; both hold whole lines, and the snapshot is the
    /// predecessor then the current file. Delete removes both.
    /// - Test: T_IOS_TRACE_ROTATE
    /// - Verifies: R_BLE_TRACE, R_IOS_TESTED
    @Test func theFileRotatesAtTheCap() async throws {
        let url = FileManager.default.temporaryDirectory.appending(path: "trace-\(UUID().uuidString).jsonl")
        let file = TraceFile(url: url, maxBytes: 100)
        for n in 0..<3 { await file.io("read", char: 0x1202, bytes: [UInt8(n)]) }
        let previous = URL(fileURLWithPath: url.path + ".1")
        for f in [previous, url] {
            let text = try String(contentsOf: f, encoding: .utf8)
            #expect(text.hasSuffix("\n"))
            for line in text.split(separator: "\n") {
                #expect((try? JSONSerialization.jsonObject(with: Data(line.utf8))) != nil, "\(line)")
            }
        }
        let snap = try String(contentsOf: try #require(await file.snapshot()), encoding: .utf8)
        #expect(snap.split(separator: "\n").map { $0.hasSuffix(#""hex":"00"}"#) } == [true, false, false])
        #expect(snap.split(separator: "\n").last?.hasSuffix(#""hex":"02"}"#) == true)
        await file.delete()
        #expect(!file.exists)
        #expect(await file.snapshot() == nil)
    }

    /// An I/O line's keys are exactly `t,ev,char,fn,hex` in this order, a link line's `t,ev,addr`
    /// (+ `reason`); the VIN hash's payload is never written.
    /// - Test: T_IOS_TRACE_KEYS
    /// - Verifies: R_BLE_TRACE, R_IOS_NO_VIN
    @Test func keyOrderAndRedaction() async throws {
        let url = FileManager.default.temporaryDirectory.appending(path: "trace-\(UUID().uuidString).jsonl")
        let file = TraceFile(url: url, now: { 1_700_000_000.25 })
        await file.io("notify", char: 0x1602, bytes: [0x00, 0xD0])
        await file.io("read", char: 0x1002, bytes: [0xAB, 0xCD])
        await file.io("write", char: 0x9999, bytes: [0x01])
        await file.link("connect", reason: nil)
        await file.link("disconnect", reason: "remoteHangUp")
        let lines = try String(contentsOf: url, encoding: .utf8).split(separator: "\n").map(String.init)
        #expect(
            lines == [
                #"{"t":1700000000.25,"ev":"notify","char":"1602","fn":"energy","hex":"00d0"}"#,
                #"{"t":1700000000.25,"ev":"read","char":"1002","fn":null,"hex":"<vin-hash>"}"#,
                #"{"t":1700000000.25,"ev":"write","char":"9999","fn":null,"hex":"01"}"#,
                #"{"t":1700000000.25,"ev":"connect","addr":null}"#,
                #"{"t":1700000000.25,"ev":"disconnect","addr":null,"reason":"remoteHangUp"}"#,
            ])
        await file.delete()
    }
}
