import CaliBLE
import SwiftUI

/// The Setup tab's "Camper unit" card, like the web UI's Device status: the link in the pill's
/// words, the age of the data (the page's clock), the pairing as far as the app knows it, and the
/// unit's firmware from `general` (amb software · CommunicationVersion). No VIN, no address, no
/// identifier.
///
/// - Requirement: R_IOS_FUNCTIONS, R_IOS_HONEST, R_IOS_NO_VIN
struct DeviceStatusView: View {
    let store: CamperStore
    @Environment(\.pageNow) private var pageNow

    /// "amb 0410 · comm 2" (`?` for a part the unit did not report); nil without a `general` frame.
    static func firmware(_ store: CamperStore) -> LocalizedStringResource? {
        let amb = store.value("general", "amb_sw_version")?.string
        let comm = store.value("general", "comm_version")?.number.map { String(Int($0)) }
        guard amb != nil || comm != nil else { return nil }
        return "amb \(amb ?? "?") · comm \(comm ?? "?")"
    }

    var body: some View {
        let now = pageNow ?? store.now()
        VStack(alignment: .leading, spacing: 8) {
            Text("Camper unit").font(.headline).accessibilityAddTraits(.isHeader)
            StatusRow(label: "Connection", value: store.linkLabel(at: now))
            StatusRow(label: "Last update", value: store.age(at: now) ?? "—")
            switch store.bond {
            case .paired: StatusRow(label: "Pairing", value: "Paired", symbol: "checkmark")
            case .notPaired, .stale: StatusRow(label: "Pairing", value: "Not paired")
            case .unknown: EmptyView()
            }
            if let fw = Self.firmware(store) { StatusRow(label: "Unit firmware", value: fw) }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding()
        .background(Color(uiColor: .secondarySystemBackground), in: RoundedRectangle(cornerRadius: 16))
    }
}

/// A label and its value, read as one element.
private struct StatusRow: View {
    let label: LocalizedStringResource
    let value: LocalizedStringResource
    /// A symbol after the value, not read by VoiceOver.
    var symbol: String?
    @Environment(\.dynamicTypeSize) private var typeSize

    var body: some View {
        StackedRow(label: Text(label)) {
            HStack(spacing: 4) {
                Text(value).fontWeight(.medium)
                    // "amb 0410 · comm 2" stays one line; at accessibility sizes it wraps (never cut).
                    .lineLimit(typeSize.isAccessibilitySize ? nil : 1).minimumScaleFactor(0.8)
                if let symbol { Image(systemName: symbol).accessibilityHidden(true) }
            }
        }
        .frame(minHeight: 32)
        .accessibilityElement(children: .combine)
    }
}
