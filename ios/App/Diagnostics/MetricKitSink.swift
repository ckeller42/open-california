import Foundation
import MetricKit

/// MetricKit's crash, hang and CPU-exception reports, kept for a bug report: iOS delivers them on a
/// later launch; the newest `keep` stay in `Application Support/diagnostics/`. Metric payloads
/// (battery, timing) are not kept.
///
/// - Requirement: R_IOS_TESTED
final class MetricKitSink: NSObject, MXMetricManagerSubscriber, @unchecked Sendable {
    static let shared = MetricKitSink()
    static let defaultFolder = URL.applicationSupportDirectory.appending(path: "diagnostics")

    let folder: URL
    let keep: Int

    init(folder: URL = defaultFolder, keep: Int = 10) {
        self.folder = folder
        self.keep = keep
    }

    /// Starts receiving the reports (once, at launch).
    func start() { MXMetricManager.shared.add(self) }

    func didReceive(_ payloads: [MXDiagnosticPayload]) {
        for p in payloads {
            store(p.jsonRepresentation(), name: "diagnostic-\(Int(p.timeStampEnd.timeIntervalSince1970)).json")
        }
    }

    /// Writes one report and deletes the oldest beyond `keep`. Never throws: a report is a bonus.
    func store(_ json: Data, name: String) {
        try? FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        try? json.write(to: folder.appending(path: name), options: .atomic)
        for old in files().dropFirst(keep) { try? FileManager.default.removeItem(at: old) }
    }

    /// The kept reports, newest first.
    func files() -> [URL] {
        let urls =
            (try? FileManager.default.contentsOfDirectory(
                at: folder, includingPropertiesForKeys: [.contentModificationDateKey])) ?? []
        func date(_ u: URL) -> Date {
            (try? u.resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate) ?? .distantPast
        }
        return urls.filter { $0.pathExtension == "json" }.sorted { date($0) > date($1) }
    }
}
