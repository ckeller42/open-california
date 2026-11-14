import CaliBLE
import CoreTransferable
import SwiftUI
import UIKit
import UniformTypeIdentifiers

/// Setup → Diagnostics: what a bug report needs, and one export of the app's log of this launch,
/// the kept crash and hang reports and the Bluetooth trace (if one is recorded). No VIN, no address,
/// no identifier, no location.
///
/// - Requirement: R_IOS_TESTED, R_IOS_NO_VIN
struct DiagnosticsView: View {
    let store: CamperStore
    var sink: MetricKitSink = .shared
    var trace: TraceFile = CamperStore.traceFile
    @AppStorage(CamperStore.traceKey) private var traceOn = false

    static var appVersion: String {
        let info = Bundle.main.infoDictionary
        let version = info?["CFBundleShortVersionString"] as? String ?? "?"
        return "\(version) (\(info?["CFBundleVersion"] as? String ?? "?"))"
    }

    var body: some View {
        let reports = sink.files()
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                VStack(alignment: .leading, spacing: 8) {
                    StatusRow(label: "App version", value: "\(Self.appVersion)")
                    StatusRow(label: "iOS version", value: "\(UIDevice.current.systemVersion)")
                    StatusRow(label: "Unit firmware", value: DeviceStatusView.firmware(store) ?? "—")
                    StatusRow(label: "Connection", value: store.linkLabel(at: store.now()))
                    StatusRow(label: "Bluetooth trace", value: traceOn ? "On" : "Off")
                    StatusRow(
                        label: "Crash and hang reports", value: reports.isEmpty ? "none" : "\(reports.count)")
                }
                .padding()
                .background(Color(uiColor: .secondarySystemBackground), in: RoundedRectangle(cornerRadius: 16))
                VStack(alignment: .leading, spacing: 8) {
                    ShareLink(
                        items: [DiagnosticFile.appLog] + reports.map(DiagnosticFile.report) + [.trace(trace)],
                        preview: { _ in SharePreview(Text("Diagnostics")) },
                        label: { Label("Export diagnostics", systemImage: "square.and.arrow.up") }
                    )
                    .frame(minHeight: 44)
                    .accessibilityIdentifier("diagnostics.export")
                    Text(
                        // swiftlint:disable:next line_length - a localization key stays one literal
                        "For a bug report: the app's log since it started, crash and hang reports, and the Bluetooth trace if one is recorded. No location, no names."
                    )
                    .font(.footnote)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding()
                .background(Color(uiColor: .secondarySystemBackground), in: RoundedRectangle(cornerRadius: 16))
            }
            .padding()
        }
        .navigationTitle("Diagnostics")
        .navigationBarTitleDisplayMode(.inline)
    }
}

/// One file of the diagnostics export, written when the share sheet asks for it.
enum DiagnosticFile: Transferable {
    case appLog
    case report(URL)
    case trace(TraceFile)

    static var transferRepresentation: some TransferRepresentation {
        FileRepresentation(exportedContentType: .data) { file in
            switch file {
            case .appLog:
                let url = FileManager.default.temporaryDirectory.appending(path: "app-log.txt")
                let lines = (try? AppLogExport.lines()) ?? ["(the app log could not be read)"]
                try lines.joined(separator: "\n").write(to: url, atomically: true, encoding: .utf8)
                return SentTransferredFile(url)
            case .report(let url):
                return SentTransferredFile(url)
            case .trace(let trace):
                // No trace recorded: an empty note instead of a missing file.
                guard let copy = await trace.snapshot() else {
                    let url = FileManager.default.temporaryDirectory.appending(path: "ble-trace-none.txt")
                    try "No Bluetooth trace was recorded.".write(to: url, atomically: true, encoding: .utf8)
                    return SentTransferredFile(url)
                }
                return SentTransferredFile(copy)
            }
        }
    }
}
