import CaliBLE
import Foundation
import OSLog

/// The app's own log of this launch, for a bug report: unified logging keeps it; this reads it back.
///
/// - Requirement: R_IOS_TESTED
enum AppLogExport {
    /// One line per entry of the app's subsystem since `since`: time, category, level, message.
    static func lines(since: Date = .distantPast) throws -> [String] {
        let store = try OSLogStore(scope: .currentProcessIdentifier)
        let entries = try store.getEntries(
            at: store.position(date: since), matching: NSPredicate(format: "subsystem == %@", Log.subsystem))
        let time = Date.ISO8601FormatStyle.iso8601.time(includingFractionalSeconds: true)
        return entries.compactMap { $0 as? OSLogEntryLog }.map {
            "\($0.date.formatted(time)) \($0.category) \(name($0.level)) \($0.composedMessage)"
        }
    }

    private static func name(_ level: OSLogEntryLog.Level) -> String {
        switch level {
        case .debug: "debug"
        case .info: "info"
        case .notice: "notice"
        case .error: "error"
        case .fault: "fault"
        default: "-"
        }
    }
}
