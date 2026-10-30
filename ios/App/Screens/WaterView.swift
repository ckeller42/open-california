import CaliCore
import SwiftUI

/// The water screen: fresh and waste tank like the web UI ("<%>  (<l>/<capacity> L)" with a bar) and
/// the unit's own water fault texts. A held reading (the stale-latch guard kept the last plausible
/// one) shows its measurement age, never as live.
///
/// - Requirement: R_IOS_FUNCTIONS, R_IOS_INSTALLED, R_IOS_HONEST
struct WaterView: View {
    let store: CamperStore

    static let freshAlert: [String: LocalizedStringResource] = [
        "pump_protection": "Pump protection is active. Please fill up fresh water.",
        "sensor_error": "Fill level sensor in fresh water tank. Emergency water pump operation possible. Please visit a workshop.",
        "error": "Unknown fresh water error.",
        "pump_error": "Fresh water pump. Please visit a workshop.",
        "empty": "Fresh water is empty. Please refill the fresh water tank.",
    ]
    static let wasteAlert: [String: LocalizedStringResource] = [
        "full": "Grey water tank is full. Please empty.",
        "sensor_error": "Fill level sensor in waste water tank. Please visit a workshop.",
        "error": "General grey water error. Please visit a workshop.",
    ]

    /// "measured 2 h ago" for a held reading (from `waterGoodAt`), else "last measured".
    static func measured(_ store: CamperStore) -> LocalizedStringResource {
        guard let at = store.state.waterGoodAt else { return "last measured" }
        return "measured \(String(localized: ageText(store.now().timeIntervalSince(at))))"
    }

    private func tank(_ key: String) -> (text: String, percent: Double?) {
        let t = store.value("water", key)
        guard let p = t?["percent"]?.number else { return ("—", nil) }
        let l = t?["liters"]?.number.map { String(Int($0)) } ?? "—"
        let c = t?["capacity_l"]?.number.map { String(Int($0)) } ?? "—"
        return ("\(Int(p))%  (\(l)/\(c) L)", p)
    }

    var body: some View {
        StatusPage(store: store) {
            if store.installed("water") {
                VStack(spacing: 0) {
                    let fresh = tank("fresh"), waste = tank("waste")
                    Row("Fresh water", Text(verbatim: fresh.text), bar: fresh.percent)
                    Row("Waste water", Text(verbatim: waste.text), bar: waste.percent)
                    if store.state.waterHeld {
                        Label { Text(Self.measured(store)) } icon: { Image(systemName: "clock") }
                            .font(.footnote)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .padding()
                    }
                }
                .background(Color(uiColor: .secondarySystemBackground), in: RoundedRectangle(cornerRadius: 16))
                ForEach(alerts.indices, id: \.self) { i in
                    Label { Text(alerts[i]) } icon: { Image(systemName: "exclamationmark.triangle") }
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
        }
    }

    /// The unit's fault dialogs, fresh first, then waste.
    private var alerts: [LocalizedStringResource] {
        [store.value("water", "fresh_alert")?.string.flatMap { Self.freshAlert[$0] },
         store.value("water", "waste_alert")?.string.flatMap { Self.wasteAlert[$0] }].compactMap { $0 }
    }
}
