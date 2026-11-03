import Foundation
import Testing

@testable import Camper

struct MetricKitSinkTests {
    /// The sink keeps the newest `keep` reports, newest first.
    ///
    /// - Test: T_IOS_METRICKIT_KEEP
    /// - Verifies: R_IOS_TESTED
    @Test func keepsTheNewestReports() throws {
        let folder = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: folder) }
        let sink = MetricKitSink(folder: folder, keep: 2)
        for i in 1...3 {
            sink.store(Data("{\"n\":\(i)}".utf8), name: "d\(i).json")
            // Distinct modification times, oldest first.
            try FileManager.default.setAttributes(
                [.modificationDate: Date(timeIntervalSince1970: Double(i) * 1000)],
                ofItemAtPath: folder.appending(path: "d\(i).json").path)
        }
        sink.store(Data("{}".utf8), name: "d4.json")  // newest (now): trims to 2
        #expect(sink.files().map(\.lastPathComponent) == ["d4.json", "d3.json"])
    }
}
