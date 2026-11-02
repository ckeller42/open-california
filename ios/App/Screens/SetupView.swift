import CaliBLE
import CoreTransferable
import SwiftUI
import UniformTypeIdentifiers

/// The Setup tab: the page frame (pill with the clock-driven age, the feedback), the unit's status,
/// the pairing guide and the Bluetooth trace.
///
/// - Requirement: R_IOS_PAIRING, R_IOS_FUNCTIONS, R_IOS_HONEST
struct SetupView: View {
    let store: CamperStore
    /// The guide's starting step (snapshots); nil: from the app's bond.
    var guide: PairingGuide?

    var body: some View {
        StatusPage(store: store) {
            DeviceStatusView(store: store)
            PairingGuideView(store: store, guide: guide)
            TraceCard()
                .padding(.bottom, 44)  // the last card clears the tab bar's blur band
        }
    }
}

/// The "Bluetooth trace" card: the switch, what the trace holds, and (only when a trace exists)
/// share and delete. Turning the switch off keeps the file.
///
/// - Requirement: R_BLE_TRACE, R_IOS_I18N
struct TraceCard: View {
    var file: TraceFile = CamperStore.traceFile
    @AppStorage(CamperStore.traceKey) private var isOn = false
    @State private var exists = false

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Bluetooth trace").font(.headline).accessibilityAddTraits(.isHeader)
            SwitchRow(label: Text("Record Bluetooth trace"), isOn: $isOn)
            Text("For a bug report: what the app and the camper unit exchanged. No location, no names.")
                .font(.footnote)
            if exists {
                ShareLink(item: TraceExport(file: file), preview: SharePreview(Text("Bluetooth trace"))) {
                    Label("Export trace", systemImage: "square.and.arrow.up")
                }
                .frame(minHeight: 44)
                Button(role: .destructive) {
                    Task {
                        await file.delete()
                        exists = file.exists
                    }
                } label: {
                    Label("Delete trace", systemImage: "trash")
                }
                .frame(minHeight: 44)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding()
        .background(Color(uiColor: .secondarySystemBackground), in: RoundedRectangle(cornerRadius: 16))
        // ponytail: polls the file's presence every 2 s while the tab shows (the first line can
        // come at any time); a callback from TraceFile if the poll ever matters.
        .task {
            while !Task.isCancelled {
                exists = file.exists
                try? await Task.sleep(for: .seconds(2))
            }
        }
    }
}

/// The trace as a file to share: the copy is taken when the share sheet asks for it, so a trace
/// that is still recording is shared whole, never with a half-written line.
struct TraceExport: Transferable {
    let file: TraceFile

    static var transferRepresentation: some TransferRepresentation {
        FileRepresentation(exportedContentType: .plainText) { export in
            guard let copy = await export.file.snapshot() else { throw CocoaError(.fileNoSuchFile) }
            return SentTransferredFile(copy)
        }
    }
}
