import Foundation
import JavaScriptCore

/// A JSON value; numbers are `Double`, like JavaScript's.
public enum JSONValue: Equatable, Sendable, Codable {
    case null
    case bool(Bool)
    case number(Double)
    case string(String)
    case array([JSONValue])
    case object([String: JSONValue])

    public init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if c.decodeNil() {
            self = .null
        } else if let v = try? c.decode(Bool.self) {
            self = .bool(v)
        } else if let v = try? c.decode(Double.self) {
            self = .number(v)
        } else if let v = try? c.decode(String.self) {
            self = .string(v)
        } else if let v = try? c.decode([JSONValue].self) {
            self = .array(v)
        } else {
            self = .object(try c.decode([String: JSONValue].self))
        }
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.singleValueContainer()
        switch self {
        case .null: try c.encodeNil()
        case .bool(let v): try c.encode(v)
        case .number(let v): try c.encode(v)
        case .string(let v): try c.encode(v)
        case .array(let v): try c.encode(v)
        case .object(let v): try c.encode(v)
        }
    }
}

/// A JavaScript exception or load failure in `semantics.js`; `message` is the JS text.
public struct SemanticsError: Error, Equatable, Sendable {
    public let message: String
}

/// The web UI's interpretation layer (`semantics.js`, unchanged) run in JavaScriptCore.
///
/// One `JSContext`, touched only inside `queue.sync`, so the class is safe to call from any thread.
/// Data crosses the boundary as JSON strings (keeps bool/number/null exact). A JS failure throws
/// `SemanticsError`; it never comes back as an empty or unchanged result.
///
/// - Requirement: R_IOS_FUNCTIONS, R_IOS_HONEST
public final class Semantics: @unchecked Sendable {  // @unchecked: `ctx` and `error` are only used inside `queue`
    public static let shared = Semantics()

    private let queue = DispatchQueue(label: "de.opencalifornia.semantics")
    private let ctx = JSContext()!
    private var error: String?
    private var loadError: String?

    private convenience init() {
        var scripts: [(String, URL?)] = []
        for name in ["semantics_tables", "semantics"] {
            guard let url = Bundle.module.url(forResource: name, withExtension: "js"),
                let src = try? String(contentsOf: url, encoding: .utf8)
            else {
                self.init(scripts: [], loadError: "missing resource \(name).js")
                return
            }
            scripts.append((src, url))
        }
        self.init(scripts: scripts)
    }

    /// Tests load their own script (for example one whose `interpret` throws).
    init(scripts: [(String, URL?)], loadError: String? = nil) {
        self.loadError = loadError
        ctx.exceptionHandler = { [unowned self] _, e in error = e?.toString() ?? "unknown JS exception" }
        for (src, url) in scripts { ctx.evaluateScript(src, withSourceURL: url) }
        if self.loadError == nil, let e = error { self.loadError = e }
        ctx.evaluateScript(
            """
            function __call(fn, json) { return JSON.stringify(globalThis[fn](JSON.parse(json))); }
            function __interpret(f, json) { return JSON.stringify(interpret(f, JSON.parse(json))); }
            """)
    }

    /// Calls a global JS function with JSON in and JSON out.
    private func run<T: Decodable>(_ fn: String, _ args: [Any]) throws -> T {
        try queue.sync {
            if let loadError { throw SemanticsError(message: loadError) }
            error = nil
            let out = ctx.objectForKeyedSubscript(fn)?.call(withArguments: args)
            if let error { throw SemanticsError(message: error) }
            guard let out, out.isString, let s = out.toString(),
                let v = try? JSONDecoder().decode(T.self, from: Data(s.utf8))
            else { throw SemanticsError(message: "\(fn) returned no JSON") }
            return v
        }
    }

    /// `interpret(fn, d)` of semantics.js. An unknown function gives the generic view, like the web UI.
    public func interpret(function: String, fields: [String: UInt32]) throws -> [String: JSONValue] {
        let json = (try? JSONSerialization.data(withJSONObject: fields)).flatMap { String(bytes: $0, encoding: .utf8) } ?? "{}"
        return try run("__interpret", [function, json])
    }

    /// `applySwCorrections(states)` of semantics.js (DC-DC current +2 on certain unit software).
    /// It adds to the input: pass uncorrected interpretations, never an already corrected map.
    public func applySwCorrections(_ states: [String: [String: JSONValue]]) throws -> [String: [String: JSONValue]] {
        let json = (try? JSONEncoder().encode(states)).flatMap { String(bytes: $0, encoding: .utf8) } ?? "{}"
        return try run("__call", ["applySwCorrections", json])
    }
}

/// `Semantics.shared.applySwCorrections(_:)`.
///
/// - Requirement: R_IOS_FUNCTIONS
public func applySwCorrections(_ states: [String: [String: JSONValue]]) throws -> [String: [String: JSONValue]] {
    try Semantics.shared.applySwCorrections(states)
}
