import os

/// The app's decisions in unified logging (the system rotates it). Never log a peripheral name or
/// identifier, a MAC, the VIN or its hash, or a passkey: only states, functions and results.
///
/// - Requirement: R_IOS_TESTED
public enum Log {
    public static let subsystem = "open-california.camper"
    /// Link state changes (connecting, up, rest, unreachable with its reason).
    public static let session = Logger(subsystem: subsystem, category: "session")
    /// Each command and its result.
    public static let command = Logger(subsystem: subsystem, category: "command")
    /// The pairing guide's steps.
    public static let pairing = Logger(subsystem: subsystem, category: "pairing")
}
