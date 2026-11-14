import CaliCore
import Foundation

/// The BLE trace file: one JSON line per event in the format of `calictl/trace.py`, so
/// `python3 -m tools.trace_compare <file>` replays an iPhone session.
///
/// I/O lines are `{"t","ev","char","fn","hex"}`, link lines `{"t","ev","addr"}` (+ `"reason"` on a
/// disconnect), in this key order. `addr` is always `null` (no identifier of the iPhone or the unit),
/// the `1003` heartbeat is skipped, a `1002` frame (the VIN hash) is written as `<vin-hash>`. At
/// `maxBytes` the file moves to `<name>.1` (one predecessor). File errors are swallowed: tracing
/// never breaks BLE I/O. The actor writes one whole line at a time, so lines never interleave.
///
/// - Requirement: R_BLE_TRACE, R_IOS_NO_VIN
public actor TraceFile {
    public nonisolated let url: URL
    private nonisolated let previous: URL
    private let maxBytes: Int
    private let now: @Sendable () -> Double

    /// - Parameter now: the epoch seconds of a line (tests fix it).
    public init(url: URL, maxBytes: Int = 10_000_000, now: @escaping @Sendable () -> Double = { Date().timeIntervalSince1970 }) {
        self.url = url
        previous = URL(fileURLWithPath: url.path + ".1")
        self.maxBytes = maxBytes
        self.now = now
    }

    /// function name per char short id (`stateChars`, `controlChars`), `heartbeat` for `1003`.
    private static let functions: [UInt16: String] = {
        var m: [UInt16: String] = [CamperGATT.heartbeat: "heartbeat"]
        for table in [CamperGATT.stateChars, CamperGATT.controlChars] {
            for (fn, c) in table { m[c] = fn }
        }
        return m
    }()

    /// The VIN-hash char; its payload never goes into the file (`trace.REDACTED_VIN_HASH`).
    static let vinHash: UInt16 = 0x1002

    /// Appends an I/O line (`notify`, `read`, `write`); a heartbeat is skipped.
    func io(_ ev: String, char: UInt16, bytes: [UInt8]) {
        guard char != CamperGATT.heartbeat else { return }
        let hex = char == Self.vinHash ? "<vin-hash>" : bytes.map { String(format: "%02x", $0) }.joined()
        let fn = Self.functions[char].map { "\"\($0)\"" } ?? "null"
        append(#"{"t":\#(now()),"ev":"\#(ev)","char":"\#(String(format: "%04x", char))","fn":\#(fn),"hex":"\#(hex)"}"#)
    }

    /// Appends a link line (`connect`, `disconnect` with its reason).
    func link(_ ev: String, reason: String?) {
        let extra = reason.map { #","reason":"\#($0)""# } ?? ""
        append(#"{"t":\#(now()),"ev":"\#(ev)","addr":null\#(extra)}"#)
    }

    /// Whether a trace exists (the current file or its predecessor).
    public nonisolated var exists: Bool {
        FileManager.default.fileExists(atPath: url.path) || FileManager.default.fileExists(atPath: previous.path)
    }

    /// A copy safe to share: the predecessor and the current file in one file (the order
    /// `trace.read_events` reads them), taken between two lines. Nil without a trace.
    public func snapshot() -> URL? {
        let parts = [previous, url].compactMap { try? Data(contentsOf: $0) }
        guard !parts.isEmpty else { return nil }
        let copy = FileManager.default.temporaryDirectory.appending(path: url.lastPathComponent)
        do {
            try parts.reduce(Data(), +).write(to: copy, options: .atomic)
            return copy
        } catch {
            return nil
        }
    }

    /// Removes the trace, its predecessor and the last shared copy.
    public func delete() {
        try? FileManager.default.removeItem(at: url)
        try? FileManager.default.removeItem(at: previous)
        try? FileManager.default.removeItem(at: FileManager.default.temporaryDirectory.appending(path: url.lastPathComponent))
    }

    private func append(_ line: String) {
        let fm = FileManager.default
        if let size = (try? fm.attributesOfItem(atPath: url.path))?[.size] as? Int, size >= maxBytes {
            try? fm.removeItem(at: previous)
            try? fm.moveItem(at: url, to: previous)
        }
        let data = Data((line + "\n").utf8)
        guard let h = try? FileHandle(forWritingTo: url) else {
            try? fm.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
            // A new file only: an existing one that does not open keeps its lines (this one is lost).
            try? data.write(to: url, options: .withoutOverwriting)
            // A bug-report file: kept on the device, never in a backup.
            var values = URLResourceValues()
            values.isExcludedFromBackup = true
            var file = url
            try? file.setResourceValues(values)
            return
        }
        defer { try? h.close() }
        _ = try? h.seekToEnd()
        try? h.write(contentsOf: data)
    }
}

/// A transport that forwards every call to `inner` and re-yields its events, and appends one
/// trace line per notification, read, write and link event while `isOn()` holds (checked per
/// event: a trace turned on during a link starts with the next event).
///
/// The line of an event is written before the event is re-yielded, so the session never acts on
/// an event whose line is not in the file yet. Writes are traced after the unit's ACK, as
/// `calictl.device` does.
///
/// - Requirement: R_BLE_TRACE, R_IOS_BLE_DIRECT
public actor TracingTransport: CamperTransport {
    public nonisolated let events: AsyncStream<TransportEvent>
    private let inner: CamperTransport
    private let file: TraceFile
    private let isOn: @Sendable () -> Bool

    public init(_ inner: CamperTransport, file: TraceFile, isOn: @escaping @Sendable () -> Bool) {
        self.inner = inner
        self.file = file
        self.isOn = isOn
        let (events, sink) = AsyncStream<TransportEvent>.makeStream()
        self.events = events
        Task {
            for await ev in inner.events {
                if isOn() {
                    switch ev {
                    case .connected: await file.link("connect", reason: nil)
                    case .disconnected(let reason): await file.link("disconnect", reason: "\(reason)")
                    case .notified(let char, let bytes): await file.io("notify", char: char, bytes: bytes)
                    default: break
                    }
                }
                sink.yield(ev)
            }
            sink.finish()
        }
    }

    public func connect() async { await inner.connect() }
    public func cancel() async { await inner.cancel() }

    public func read(_ char: UInt16) async throws -> [UInt8] {
        let bytes = try await inner.read(char)
        if isOn() { await file.io("read", char: char, bytes: bytes) }
        return bytes
    }

    public func write(_ char: UInt16, _ bytes: [UInt8]) async throws {
        try await inner.write(char, bytes)
        // The heartbeat (~0.6 s, for hours) never waits for the file actor (a snapshot can hold it).
        if char != CamperGATT.heartbeat, isOn() { await file.io("write", char: char, bytes: bytes) }
    }

    public func subscribe(_ chars: [UInt16]) async throws { try await inner.subscribe(chars) }
    public func pairingCandidates(window: Duration) async throws -> [PairingCandidate] {
        try await inner.pairingCandidates(window: window)
    }
    public func beginFirstPairing(with id: UUID) async { await inner.beginFirstPairing(with: id) }
    public func forgetUnit() async { await inner.forgetUnit() }
}
