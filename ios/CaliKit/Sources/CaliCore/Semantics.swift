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
        if c.decodeNil() { self = .null }
        else if let v = try? c.decode(Bool.self) { self = .bool(v) }
        else if let v = try? c.decode(Double.self) { self = .number(v) }
        else if let v = try? c.decode(String.self) { self = .string(v) }
        else if let v = try? c.decode([JSONValue].self) { self = .array(v) }
        else { self = .object(try c.decode([String: JSONValue].self)) }
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

/// The web UI's interpretation layer (`semantics.js`, unchanged) run in JavaScriptCore.
///
/// One `JSContext`, touched only inside `queue.sync`, so the class is safe to call from any thread.
/// Data crosses the boundary as JSON strings (keeps bool/number/null exact).
///
/// - Requirement: R_IOS_FUNCTIONS
public final class Semantics: @unchecked Sendable {  // @unchecked: `ctx` and `error` are only used inside `queue`
    public static let shared = Semantics()

    private let queue = DispatchQueue(label: "de.opencalifornia.semantics")
    private let ctx = JSContext()!
    private var error: String?

    /// The last JS exception or load failure, nil if none (cleared by the next successful call).
    public var lastError: String? { queue.sync { error } }

    private init() {
        ctx.exceptionHandler = { [unowned self] _, e in error = e?.toString() ?? "unknown JS exception" }
        for name in ["semantics_tables", "semantics"] {
            guard let url = Bundle.module.url(forResource: name, withExtension: "js"),
                  let src = try? String(contentsOf: url, encoding: .utf8)
            else { error = "missing resource \(name).js"; return }
            ctx.evaluateScript(src, withSourceURL: url)
        }
        ctx.evaluateScript("""
            function __call(fn, json) { return JSON.stringify(globalThis[fn](JSON.parse(json))); }
            function __interpret(f, json) { return JSON.stringify(interpret(f, JSON.parse(json))); }
            """)
    }

    /// Calls a global JS function with JSON in and JSON out; `[:]` plus `lastError` on failure.
    private func run<T: Decodable>(_ fn: String, _ args: [Any], empty: T) -> T {
        queue.sync {
            error = nil
            guard let out = ctx.objectForKeyedSubscript(fn)?.call(withArguments: args), error == nil,
                  let s = out.isString ? out.toString() : nil,
                  let v = try? JSONDecoder().decode(T.self, from: Data(s.utf8))
            else { error = error ?? "\(fn) returned no JSON"; return empty }
            return v
        }
    }

    /// `interpret(fn, d)` of semantics.js. An unknown function gives the generic view, like the web UI.
    public func interpret(function: String, fields: [String: UInt32]) -> [String: JSONValue] {
        let json = String(decoding: (try? JSONSerialization.data(withJSONObject: fields)) ?? Data("{}".utf8), as: UTF8.self)
        return run("__interpret", [function, json], empty: [:])
    }

    fileprivate func corrected(_ states: [String: [String: JSONValue]]) -> [String: [String: JSONValue]] {
        let json = String(decoding: (try? JSONEncoder().encode(states)) ?? Data("{}".utf8), as: UTF8.self)
        return run("__call", ["applySwCorrections", json], empty: states)
    }
}

/// `applySwCorrections(states)` of semantics.js (DC-DC current +2 on certain unit software). Returns the input on a JS error.
///
/// - Requirement: R_IOS_FUNCTIONS
public func applySwCorrections(_ states: [String: [String: JSONValue]]) -> [String: [String: JSONValue]] {
    Semantics.shared.corrected(states)
}
