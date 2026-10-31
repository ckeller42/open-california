import CaliCore
import SwiftUI

/// The water screen: fresh and waste tank like the web UI ("<%>  (<l>/<capacity> L)" with a bar) and
/// the unit's own water fault texts. A held reading (the stale-latch guard kept the last plausible
/// one) shows its measurement age, never as live.
///
/// - Requirement: R_IOS_FUNCTIONS, R_IOS_INSTALLED, R_IOS_HONEST
struct WaterView: View {
    let store: CamperStore
    @Environment(\.locale) private var locale

    static let freshAlert: [String: LocalizedStringResource] = [
        "pump_protection": "Pump protection is active. Please fill up fresh water.",
        "sensor_error":
            "Fill level sensor in fresh water tank. Emergency water pump operation possible. Please visit a workshop.",
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
    static func measured(_ store: CamperStore, locale: Locale = .current) -> String {
        HeldNote.text(since: store.state.waterGoodAt, now: store.now(), locale: locale)
    }

    /// "37%  (11/29 L)" like the web UI's `tank`, and the same in full words for VoiceOver.
    private func tank(_ key: String) -> (text: String, spoken: String, percent: Double?) {
        let t = store.value("water", key)
        guard let p = t?["percent"]?.number else { return ("—", "—", nil) }
        let l = t?["liters"]?.number
        let c = t?["capacity_l"]?.number
        let ls = l.map { String(Int($0)) } ?? "—"
        let cs = c.map { String(Int($0)) } ?? "—"
        let capSpoken = c.map { spoken($0, UnitVolume.liters, locale) } ?? "—"
        return (
            "\(Int(p))%\(nbsp) (\(ls)/\(cs)\(nbsp)L)",
            "\(Int(p))%, " + LocalizedStringResource("\(ls) of \(capSpoken)").string(locale), p
        )
    }

    var body: some View {
        StatusPage(store: store) {
            if store.installed("water") {
                VStack(spacing: 0) {
                    let fresh = tank("fresh")
                    let waste = tank("waste")
                    Row("Fresh water", Text(verbatim: fresh.text), spoken: fresh.spoken, bar: fresh.percent)
                    Row("Waste water", Text(verbatim: waste.text), spoken: waste.spoken, bar: waste.percent)
                    if store.state.waterHeld {
                        HeldNote(since: store.state.waterGoodAt)
                            .font(.footnote)
                            .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
                            .padding(.horizontal)
                            .padding(.bottom, 6)
                            .accessibilityElement(children: .combine)  // the whole line is the hit area
                    }
                }
                .background(Color(uiColor: .secondarySystemBackground), in: RoundedRectangle(cornerRadius: 16))
                let alerts = Self.alerts(store)
                ForEach(alerts.indices, id: \.self) { AlertCard(text: alerts[$0]) }
            }
        }
    }

    /// The unit's fault dialogs, fresh first, then waste.
    static func alerts(_ store: CamperStore) -> [LocalizedStringResource] {
        [
            store.value("water", "fresh_alert")?.string.flatMap { Self.freshAlert[$0] },
            store.value("water", "waste_alert")?.string.flatMap { Self.wasteAlert[$0] },
        ].compactMap { $0 }
    }
}

/// The age of a held water reading ("measured 2 h ago"), from the page's clock, so it grows on an
/// open screen without a new frame; never shown as live.
///
/// - Requirement: R_IOS_HONEST
struct HeldNote: View {
    /// When the held reading was measured; nil = unknown ("last measured").
    let since: Date?
    @Environment(\.pageNow) private var pageNow
    @Environment(\.locale) private var locale

    /// "measured 2 h ago" (or in full words for VoiceOver), "last measured" without a date.
    static func text(since: Date?, now: Date, locale: Locale, spoken: Bool = false) -> String {
        guard let since else { return LocalizedStringResource("last measured").string(locale) }
        let age = now.timeIntervalSince(since)
        return LocalizedStringResource("measured \(spoken ? spokenAge(age, locale) : ageText(age).string(locale))").string(locale)
    }

    var body: some View {
        let now = pageNow ?? Date()
        Label {
            Text(verbatim: Self.text(since: since, now: now, locale: locale))
        } icon: {
            Image(systemName: "clock")
        }
        .accessibilityLabel(Text(verbatim: Self.text(since: since, now: now, locale: locale, spoken: true)))
    }
}
