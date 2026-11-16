import CaliBLE
import Foundation
import OSLog
import Testing

@testable import Camper

struct AppLogExportTests {
    /// A line logged through `Log` comes back with its category and level; other subsystems do not.
    ///
    /// - Test: T_IOS_APP_LOG_EXPORT
    /// - Verifies: R_IOS_TESTED
    @Test func aLoggedLineComesBack() throws {
        let start = Date().addingTimeInterval(-1)
        let probe = "probe \(UUID().uuidString)"
        Log.session.info("\(probe, privacy: .public)")
        Logger(subsystem: "other.subsystem", category: "x").info("\(probe, privacy: .public)")
        let lines = try AppLogExport.lines(since: start).filter { $0.hasSuffix(probe) }
        #expect(lines.count == 1)
        #expect(lines.first?.contains(" session info probe ") == true)
    }
}
