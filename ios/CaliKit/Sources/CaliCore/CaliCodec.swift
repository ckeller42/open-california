import CCodec

public enum CaliCodecError: Error, Equatable, Sendable {
    case unknownFunction
}

/// Decodes a state frame with the repo's C codec (`csrc/codec.c`), the code the ESP32 satellite runs.
///
/// - Requirement: R_IOS_CODEC
public enum CaliCodec {
    /// - Returns: every placed state field of `function` that fully fits `bytes`, by dictionary name.
    public static func decode(function: String, bytes: [UInt8]) throws -> [String: UInt32] {
        guard let f = codec_func_by_name(function) else { throw CaliCodecError.unknownFunction }
        var out = [codec_kv_t](repeating: codec_kv_t(name: nil, value: 0, supplied: 0), count: Int(CODEC_KV_MAX))
        let n = out.withUnsafeMutableBufferPointer { codec_decode(f, bytes, bytes.count, $0.baseAddress) }
        var fields: [String: UInt32] = [:]
        for kv in out.prefix(Int(n)) { fields[String(cString: kv.name)] = kv.value }
        return fields
    }
}
